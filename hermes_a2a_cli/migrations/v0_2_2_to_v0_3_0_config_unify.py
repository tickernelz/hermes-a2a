from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from .base import MigrationStep

try:
    import yaml
except Exception:
    yaml = None

from ..installer import build_compat_webhook_routes, dump_config, load_config, write_text

A2A_ENV_KEYS = {
    "A2A_ENABLED",
    "A2A_HOST",
    "A2A_PORT",
    "A2A_PUBLIC_URL",
    "A2A_AGENT_NAME",
    "A2A_AGENT_DESCRIPTION",
    "A2A_REQUIRE_AUTH",
    "A2A_AUTH_TOKEN",
    "A2A_WEBHOOK_SECRET",
    "WEBHOOK_ENABLED",
    "WEBHOOK_PORT",
}

SECRET_KEYS = {"auth_token", "secret"}


def parse_env(lines: list[str]) -> dict[str, str]:
    env = {}
    for line in lines:
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key] = value
    return env


def truthy(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def legacy_route(cfg: dict[str, Any]) -> dict[str, Any]:
    for path in (("webhook", "extra", "routes"), ("platforms", "webhook", "extra", "routes")):
        current: Any = cfg
        for key in path:
            current = current.get(key) if isinstance(current, dict) else None
        if isinstance(current, dict) and isinstance(current.get("a2a_trigger"), dict):
            return current["a2a_trigger"]
    return {}


def route_session(route: dict[str, Any]) -> dict[str, Any]:
    source = route.get("source") if isinstance(route.get("source"), dict) else {}
    extra = route.get("deliver_extra") if isinstance(route.get("deliver_extra"), dict) else {}
    platform = str(source.get("platform") or route.get("deliver") or "").strip()
    chat_id = str(source.get("chat_id") or extra.get("chat_id") or "").strip()
    if not platform or not chat_id:
        return {}
    session: dict[str, Any] = {
        "platform": platform,
        "chat_id": chat_id,
        "chat_type": str(source.get("chat_type") or "dm"),
        "actor": {
            "id": str(source.get("user_id") or chat_id),
            "name": str(source.get("user_name") or "user"),
        },
    }
    thread_id = source.get("thread_id") if source.get("thread_id") not in (None, "") else extra.get("thread_id")
    if thread_id not in (None, ""):
        session["thread_id"] = thread_id
    return session


def redacted(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if k in SECRET_KEYS else redacted(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redacted(item) for item in value]
    return value


def cleanup_env_lines(lines: list[str], remote_token_keys: set[str]) -> list[str]:
    remove = A2A_ENV_KEYS | remote_token_keys
    cleaned = []
    for line in lines:
        key = line.split("=", 1)[0] if "=" in line else ""
        if key in remove:
            continue
        cleaned.append(line)
    return cleaned


def build_canonical_config(cfg: dict[str, Any], env: dict[str, str]) -> tuple[dict[str, Any], set[str]]:
    a2a = cfg.get("a2a") if isinstance(cfg.get("a2a"), dict) else {}
    server = a2a.get("server") if isinstance(a2a.get("server"), dict) else {}
    route = legacy_route(cfg)
    wake_secret = env.get("A2A_WEBHOOK_SECRET") or route.get("secret") or cfg.get("webhook", {}).get("extra", {}).get("secret", "")
    webhook_extra = cfg.get("webhook", {}).get("extra", {}) if isinstance(cfg.get("webhook"), dict) else {}
    canonical = dict(cfg)
    canonical_a2a = dict(a2a)
    canonical_a2a["enabled"] = True
    canonical_a2a["identity"] = {
        "name": env.get("A2A_AGENT_NAME") or canonical_a2a.get("identity", {}).get("name") or "hermes-agent",
        "description": env.get("A2A_AGENT_DESCRIPTION") or canonical_a2a.get("identity", {}).get("description") or "Hermes A2A profile",
    }
    canonical_a2a["server"] = {
        "host": env.get("A2A_HOST") or server.get("host") or "127.0.0.1",
        "port": int(env.get("A2A_PORT") or server.get("port") or 41731),
        "public_url": (env.get("A2A_PUBLIC_URL") or server.get("public_url") or "").rstrip("/"),
        "require_auth": truthy(env.get("A2A_REQUIRE_AUTH"), truthy(server.get("require_auth"), True)),
        "auth_token": env.get("A2A_AUTH_TOKEN") or server.get("auth_token") or "",
    }
    if not canonical_a2a["server"]["public_url"]:
        canonical_a2a["server"]["public_url"] = f"http://{canonical_a2a['server']['host']}:{canonical_a2a['server']['port']}"
    wake = canonical_a2a.get("wake") if isinstance(canonical_a2a.get("wake"), dict) else {}
    canonical_wake = {
        "enabled": True,
        "port": int(env.get("WEBHOOK_PORT") or webhook_extra.get("port") or wake.get("port") or 47644),
        "secret": wake.get("secret") or wake_secret,
        "route": wake.get("route") or "a2a_trigger",
        "prompt": wake.get("prompt") or route.get("prompt") or "[A2A trigger]",
        "mode": wake.get("mode") or "owner_session",
    }
    session = wake.get("session") if isinstance(wake.get("session"), dict) else route_session(route)
    if session:
        canonical_wake["session"] = session
    canonical_a2a["wake"] = canonical_wake
    canonical_a2a["dashboard"] = {"enabled": True, "route": "a2a_dashboard"}
    canonical_a2a.setdefault("runtime", {"sync_response_timeout_seconds": 120, "active_task_timeout_seconds": 7200, "max_pending_tasks": 10})
    canonical_a2a.setdefault(
        "security",
        {
            "allow_unconfigured_urls": False,
            "redact_outbound": True,
            "max_message_chars": 50000,
            "max_response_chars": 100000,
            "max_request_bytes": 1048576,
            "max_raw_part_bytes": 262144,
            "max_parts": 20,
            "rate_limit_per_minute": 20,
        },
    )
    remote_token_keys = set()
    agents = []
    for raw in canonical_a2a.get("agents", []) if isinstance(canonical_a2a.get("agents"), list) else []:
        if not isinstance(raw, dict):
            continue
        agent = dict(raw)
        env_name = str(agent.pop("auth_token_env", "") or "")
        if env_name:
            remote_token_keys.add(env_name)
            if env.get(env_name):
                agent["auth_token"] = env[env_name]
        agents.append(agent)
    if agents:
        canonical_a2a["agents"] = agents
    canonical["a2a"] = canonical_a2a
    routes = build_compat_webhook_routes(canonical_a2a)
    webhook = canonical.setdefault("webhook", {})
    webhook["enabled"] = True
    extra = webhook.setdefault("extra", {})
    extra["port"] = canonical_wake["port"]
    extra["secret"] = canonical_wake["secret"]
    existing_routes = extra.get("routes") if isinstance(extra.get("routes"), dict) else {}
    for route_name in ("a2a_trigger", "a2a_dashboard"):
        existing_routes.pop(route_name, None)
    existing_routes.update(routes)
    extra["routes"] = existing_routes
    platforms = canonical.setdefault("platforms", {})
    platform_webhook = platforms.setdefault("webhook", {})
    platform_webhook["enabled"] = True
    platform_extra = platform_webhook.setdefault("extra", {})
    platform_extra["port"] = canonical_wake["port"]
    existing_platform_routes = platform_extra.get("routes") if isinstance(platform_extra.get("routes"), dict) else {}
    for route_name in ("a2a_trigger", "a2a_dashboard"):
        existing_platform_routes.pop(route_name, None)
    existing_platform_routes.update(routes)
    platform_extra["routes"] = existing_platform_routes
    return canonical, remote_token_keys


def migrate_config_unify(home: Path, *, dry_run: bool = False, backup_id: str | None = None) -> dict[str, Any]:
    home = home.expanduser().resolve()
    config_path = home / "config.yaml"
    env_path = home / ".env"
    cfg = load_config(config_path)
    env_lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    env = parse_env(env_lines)
    canonical, remote_token_keys = build_canonical_config(cfg, env)
    config_text = dump_config(canonical)
    env_text = "\n".join(cleanup_env_lines(env_lines, remote_token_keys)).rstrip() + "\n"
    preview = dump_config(redacted(canonical))
    if dry_run:
        return {"changed": False, "redacted_config_preview": preview}
    stamp = backup_id or time.strftime("%Y%m%d%H%M%S")
    for path in (config_path, env_path):
        if path.exists():
            shutil.copy2(path, path.with_name(f"{path.name}.bak.{stamp}"))
    write_text(config_path, config_text)
    write_text(env_path, env_text)
    return {"changed": True, "backup_id": stamp, "redacted_config_preview": preview}


class ConfigUnifyMigration(MigrationStep):
    id = "v0_2_2_to_v0_3_0_config_unify"
    from_version = "0.2.2"
    to_version = "0.3.0"

    def precheck(self, home: Path) -> None:
        config_path = home.expanduser().resolve() / "config.yaml"
        if not config_path.exists():
            raise FileNotFoundError(f"Refusing migration: {config_path} not found")

    def apply(self, home: Path, backup_id: str) -> None:
        migrate_config_unify(home, dry_run=False, backup_id=backup_id)

    def verify(self, home: Path) -> None:
        cfg = load_config(home.expanduser().resolve() / "config.yaml")
        a2a = cfg.get("a2a") if isinstance(cfg.get("a2a"), dict) else {}
        if not isinstance(a2a.get("identity"), dict) or not isinstance(a2a.get("server"), dict) or not isinstance(a2a.get("wake"), dict):
            raise RuntimeError("config-unify migration did not produce canonical a2a config")

    def rollback(self, home: Path, backup_id: str) -> None:
        root = home.expanduser().resolve()
        for name in ("config.yaml", ".env"):
            backup = root / f"{name}.bak.{backup_id}"
            if backup.exists():
                shutil.copy2(backup, root / name)
