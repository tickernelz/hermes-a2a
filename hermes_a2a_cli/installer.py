from __future__ import annotations

import os
import secrets
import shutil
import socket
import time
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:
    yaml = None


class InstallError(RuntimeError):
    pass


def load_config(config_path: Path) -> dict[str, Any]:
    if yaml is None:
        raise InstallError("PyYAML is required to safely update config.yaml")
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise InstallError("config.yaml must contain a mapping")
    return data


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = time.strftime("%Y%m%d%H%M%S")
    target = path.with_name(f"{path.name}.bak.{stamp}")
    counter = 1
    while target.exists():
        target = path.with_name(f"{path.name}.bak.{stamp}.{counter}")
        counter += 1
    if path.is_dir():
        shutil.copytree(path, target)
    else:
        shutil.copy2(path, target)
    return target


def ensure_list(container: dict[str, Any], key: str) -> list[Any]:
    value = container.get(key)
    if not isinstance(value, list):
        value = []
        container[key] = value
    return value


def append_unique(items: list[Any], value: str) -> None:
    if value not in items:
        items.append(value)


def env_value(lines: list[str], key: str, default_factory) -> str:
    prefix = f"{key}="
    for line in lines:
        if line.startswith(prefix):
            return line.split("=", 1)[1]
    return default_factory()


def ensure_env(lines: list[str], key: str, value: str, *, overwrite: bool = False) -> None:
    prefix = f"{key}="
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            if overwrite:
                lines[index] = f"{key}={value}"
            return
    lines.append(f"{key}={value}")


def env_or_config(lines: list[str], key: str, current: Any, default: str, explicit_keys: set[str]) -> str:
    env_raw = os.environ.get(key)
    if key in explicit_keys and env_raw not in (None, ""):
        return str(env_raw)
    prefix = f"{key}="
    for line in lines:
        if line.startswith(prefix):
            return line.split("=", 1)[1]
    if current not in (None, ""):
        return str(current)
    return default


def bool_value(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def choose_webhook_port(home: Path, config: dict[str, Any], existing_env: list[str]) -> int:
    raw = os.environ.get("WEBHOOK_PORT", "").strip() or os.environ.get("A2A_WEBHOOK_PORT", "").strip()
    if raw:
        return int(raw)

    for section_path in (("platforms", "webhook", "extra"), ("webhook", "extra")):
        current: Any = config
        for key in section_path:
            current = current.get(key) if isinstance(current, dict) else None
        if isinstance(current, dict) and current.get("port") not in (None, ""):
            return int(current["port"])

    for line in existing_env:
        if line.startswith("WEBHOOK_PORT=") and line.split("=", 1)[1].strip():
            return int(line.split("=", 1)[1])

    base_port = 47644
    if home.name != ".hermes":
        profile_name = home.name
        if profile_name:
            base_port += 1 + (sum(profile_name.encode("utf-8")) % 1000)

    for port in range(base_port, min(base_port + 1000, 65535)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise InstallError("could not find an available local webhook port")


def explicit_a2a_keys() -> set[str]:
    return {
        key
        for key in [
            "A2A_PORT",
            "A2A_HOST",
            "A2A_PUBLIC_URL",
            "A2A_AGENT_NAME",
            "A2A_AGENT_DESCRIPTION",
            "A2A_REQUIRE_AUTH",
        ]
        if os.environ.get(key) not in (None, "")
    }


def install_profile(home: Path, source_dir: Path, dashboard_dir: Path, *, dry_run: bool = False) -> dict[str, Any]:
    home = home.expanduser().resolve()
    config_path = home / "config.yaml"
    env_path = home / ".env"
    plugin_dir = home / "plugins" / "a2a"

    if not source_dir.is_dir():
        raise InstallError(f"plugin source not found: {source_dir}")
    if not home.is_dir():
        raise InstallError(f"HERMES_HOME does not exist: {home}")
    if not config_path.exists():
        raise InstallError(f"Refusing to install: {config_path} not found")
    try:
        plugin_dir.relative_to(home)
    except ValueError as exc:
        raise InstallError(f"Refusing to install outside HERMES_HOME: {plugin_dir}") from exc

    cfg = load_config(config_path)
    existing_env = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    existing_a2a = cfg.get("a2a", {}) if isinstance(cfg.get("a2a"), dict) else {}
    existing_server = existing_a2a.get("server", {}) if isinstance(existing_a2a.get("server"), dict) else {}
    explicit_keys = explicit_a2a_keys()

    default_host = os.environ.get("A2A_HOST") or "127.0.0.1"
    default_port = os.environ.get("A2A_PORT") or "41731"
    a2a_host = env_or_config(existing_env, "A2A_HOST", existing_server.get("host"), default_host, explicit_keys)
    a2a_port = env_or_config(existing_env, "A2A_PORT", existing_server.get("port"), default_port, explicit_keys)
    a2a_public_url = env_or_config(
        existing_env,
        "A2A_PUBLIC_URL",
        existing_server.get("public_url"),
        os.environ.get("A2A_PUBLIC_URL") or f"http://{a2a_host}:{a2a_port}",
        explicit_keys,
    ).rstrip("/")
    a2a_agent_name = env_or_config(existing_env, "A2A_AGENT_NAME", None, os.environ.get("A2A_AGENT_NAME") or "hermes-agent", explicit_keys)
    a2a_agent_description = env_or_config(
        existing_env,
        "A2A_AGENT_DESCRIPTION",
        None,
        os.environ.get("A2A_AGENT_DESCRIPTION") or "Hermes A2A profile",
        explicit_keys,
    )
    a2a_require_auth = env_or_config(
        existing_env,
        "A2A_REQUIRE_AUTH",
        existing_server.get("require_auth"),
        os.environ.get("A2A_REQUIRE_AUTH") or "true",
        explicit_keys,
    )
    webhook_port = choose_webhook_port(home, cfg, existing_env)
    secret = env_value(existing_env, "A2A_WEBHOOK_SECRET", lambda: secrets.token_hex(24))
    auth_token = env_value(existing_env, "A2A_AUTH_TOKEN", lambda: secrets.token_hex(24))
    remote_token_env = os.environ.get("A2A_REMOTE_TOKEN_ENV", "").strip()
    remote_token = env_value(existing_env, remote_token_env, lambda: secrets.token_hex(24)) if remote_token_env else ""

    plan = {
        "plugin_dir": str(plugin_dir),
        "config_path": str(config_path),
        "env_path": str(env_path),
        "webhook_port": webhook_port,
    }
    if dry_run:
        return {"changed": False, "plan": plan, "messages": ["DRY RUN: no files will be modified", f"Would install plugin to {plugin_dir}", f"Would update {config_path}", f"Would update {env_path}"]}

    backups: list[str] = []
    for path in (plugin_dir, config_path, env_path):
        target = backup(path)
        if target is not None:
            backups.append(str(target))

    if plugin_dir.exists():
        shutil.rmtree(plugin_dir)
    shutil.copytree(source_dir, plugin_dir, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if dashboard_dir.exists():
        shutil.copytree(dashboard_dir, plugin_dir / "dashboard", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    env_lines = list(existing_env)
    ensure_env(env_lines, "A2A_ENABLED", "true")
    ensure_env(env_lines, "A2A_HOST", a2a_host, overwrite="A2A_HOST" in explicit_keys)
    ensure_env(env_lines, "A2A_PORT", a2a_port, overwrite="A2A_PORT" in explicit_keys)
    ensure_env(env_lines, "A2A_PUBLIC_URL", a2a_public_url, overwrite="A2A_PUBLIC_URL" in explicit_keys)
    ensure_env(env_lines, "A2A_AGENT_NAME", a2a_agent_name, overwrite="A2A_AGENT_NAME" in explicit_keys)
    ensure_env(env_lines, "A2A_AGENT_DESCRIPTION", a2a_agent_description, overwrite="A2A_AGENT_DESCRIPTION" in explicit_keys)
    ensure_env(env_lines, "A2A_REQUIRE_AUTH", a2a_require_auth, overwrite="A2A_REQUIRE_AUTH" in explicit_keys)
    ensure_env(env_lines, "A2A_AUTH_TOKEN", auth_token)
    ensure_env(env_lines, "A2A_WEBHOOK_SECRET", secret)
    ensure_env(env_lines, "WEBHOOK_ENABLED", "true")
    ensure_env(env_lines, "WEBHOOK_PORT", str(webhook_port))
    if remote_token_env:
        ensure_env(env_lines, remote_token_env, remote_token)

    plugins = cfg.setdefault("plugins", {})
    if not isinstance(plugins, dict):
        plugins = {}
        cfg["plugins"] = plugins
    append_unique(ensure_list(plugins, "enabled"), "a2a")

    platform = os.environ.get("A2A_HOME_PLATFORM", "").strip()
    if platform:
        platform_toolsets = cfg.setdefault("platform_toolsets", {})
        if not isinstance(platform_toolsets, dict):
            platform_toolsets = {}
            cfg["platform_toolsets"] = platform_toolsets
        append_unique(ensure_list(platform_toolsets, platform), "a2a")
        known = cfg.setdefault("known_plugin_toolsets", {})
        if not isinstance(known, dict):
            known = {}
            cfg["known_plugin_toolsets"] = known
        append_unique(ensure_list(known, platform), "a2a")

    route = {"secret": secret, "prompt": "[A2A trigger]"}
    chat_id = os.environ.get("A2A_HOME_CHAT_ID", "").strip()
    if platform and chat_id:
        route["deliver"] = platform
        route["deliver_extra"] = {"chat_id": chat_id}
        route["source"] = {
            "platform": platform,
            "chat_type": os.environ.get("A2A_HOME_CHAT_TYPE", "dm").strip() or "dm",
            "chat_id": chat_id,
            "user_id": os.environ.get("A2A_HOME_USER_ID", "").strip() or chat_id,
            "user_name": os.environ.get("A2A_HOME_USER_NAME", "").strip() or "user",
        }

    for root_key in ("webhook",):
        webhook = cfg.setdefault(root_key, {})
        if not isinstance(webhook, dict):
            webhook = {}
            cfg[root_key] = webhook
        webhook["enabled"] = True
        extra = webhook.setdefault("extra", {})
        if not isinstance(extra, dict):
            extra = {}
            webhook["extra"] = extra
        extra["port"] = webhook_port
        extra.setdefault("secret", secret)
        routes = extra.setdefault("routes", {})
        if not isinstance(routes, dict):
            routes = {}
            extra["routes"] = routes
        routes["a2a_trigger"] = route
        routes["a2a_dashboard"] = {"secret": secret, "prompt": "[A2A dashboard]"}

    platforms = cfg.setdefault("platforms", {})
    if not isinstance(platforms, dict):
        platforms = {}
        cfg["platforms"] = platforms
    platform_webhook = platforms.setdefault("webhook", {})
    if not isinstance(platform_webhook, dict):
        platform_webhook = {}
        platforms["webhook"] = platform_webhook
    platform_webhook["enabled"] = True
    platform_extra = platform_webhook.setdefault("extra", {})
    if not isinstance(platform_extra, dict):
        platform_extra = {}
        platform_webhook["extra"] = platform_extra
    platform_extra["port"] = webhook_port
    platform_routes = platform_extra.setdefault("routes", {})
    if not isinstance(platform_routes, dict):
        platform_routes = {}
        platform_extra["routes"] = platform_routes
    platform_routes["a2a_trigger"] = dict(route)
    platform_routes["a2a_dashboard"] = {"secret": secret, "prompt": "[A2A dashboard]"}

    a2a = cfg.setdefault("a2a", {})
    if not isinstance(a2a, dict):
        a2a = {}
        cfg["a2a"] = a2a
    a2a["enabled"] = True
    a2a["server"] = {
        **(a2a.get("server") if isinstance(a2a.get("server"), dict) else {}),
        "host": a2a_host,
        "port": int(a2a_port),
        "public_url": a2a_public_url,
        "require_auth": bool_value(a2a_require_auth, True),
    }
    security = a2a.setdefault("security", {})
    if not isinstance(security, dict):
        security = {}
        a2a["security"] = security
    for key, value in {
        "allow_unconfigured_urls": False,
        "redact_outbound": True,
        "max_message_chars": 50000,
        "max_response_chars": 100000,
        "max_request_bytes": 1048576,
        "max_raw_part_bytes": 262144,
        "max_parts": 20,
        "rate_limit_per_minute": 20,
    }.items():
        security.setdefault(key, value)

    remote_name = os.environ.get("A2A_REMOTE_NAME", "").strip()
    remote_url = os.environ.get("A2A_REMOTE_URL", "").strip().rstrip("/")
    if remote_name and remote_url:
        agents = a2a.setdefault("agents", [])
        if not isinstance(agents, list):
            agents = []
            a2a["agents"] = agents
        agents[:] = [agent for agent in agents if not (isinstance(agent, dict) and agent.get("name") == remote_name)]
        agents.append({"name": remote_name, "url": remote_url, "description": os.environ.get("A2A_REMOTE_DESCRIPTION", "").strip(), "auth_token_env": remote_token_env, "enabled": True, "tags": ["local"], "trust_level": "trusted"})

    write_text(config_path, yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))
    write_text(env_path, "\n".join(env_lines).rstrip() + "\n")
    return {"changed": True, "plan": plan, "backups": backups, "messages": [f"Installed plugin to {plugin_dir}", f"A2A install complete for {home}", "No restart performed. Restart the target Hermes gateway manually after reviewing the diff/config."]}


def uninstall_profile(home: Path, *, dry_run: bool = False) -> dict[str, Any]:
    home = home.expanduser().resolve()
    config_path = home / "config.yaml"
    plugin_dir = home / "plugins" / "a2a"
    if home == Path("/"):
        raise InstallError("Refusing to uninstall from filesystem root")
    if not config_path.exists():
        raise InstallError(f"Refusing to uninstall: {config_path} not found")
    try:
        plugin_dir.relative_to(home)
    except ValueError as exc:
        raise InstallError(f"Refusing unsafe plugin path: {plugin_dir}") from exc
    if dry_run:
        return {"changed": False, "plugin_dir": str(plugin_dir), "messages": [f"DRY RUN: would remove {plugin_dir}"]}
    if plugin_dir.exists():
        shutil.rmtree(plugin_dir)
        message = f"Removed {plugin_dir}"
    else:
        message = f"Plugin not found at {plugin_dir}"
    return {"changed": True, "plugin_dir": str(plugin_dir), "messages": [message, f"No config/env cleanup performed. Remove A2A entries from {home}/config.yaml and {home}/.env manually if desired, then restart the target Hermes gateway."]}
