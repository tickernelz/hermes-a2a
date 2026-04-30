from __future__ import annotations

import os
import secrets
import shutil
import socket
import time
from pathlib import Path
from typing import Any

from .state import STATE_DIR_NAME

try:
    import yaml
except Exception:
    yaml = None


class NoAliasDumper(yaml.SafeDumper if yaml is not None else object):
    def ignore_aliases(self, data):
        return True


class InstallError(RuntimeError):
    pass


def load_config(config_path: Path) -> dict[str, Any]:
    if yaml is None:
        raise InstallError("PyYAML is required to safely update config.yaml")
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise InstallError("config.yaml must contain a mapping")
    return data


def dump_config(config: dict[str, Any]) -> str:
    if yaml is None:
        raise InstallError("PyYAML is required to safely update config.yaml")
    return yaml.dump(config, Dumper=NoAliasDumper, sort_keys=False, allow_unicode=True)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def move_stale_plugin_backups(home: Path) -> list[str]:
    plugins_dir = home / "plugins"
    if not plugins_dir.is_dir():
        return []
    moved: list[str] = []
    stamp = time.strftime("%Y%m%d%H%M%S")
    root = home / STATE_DIR_NAME / "backups" / stamp / "plugin-discovery-quarantine"
    for stale in sorted(plugins_dir.glob("a2a.bak.*")):
        if not stale.is_dir():
            continue
        root.mkdir(parents=True, exist_ok=True)
        target = root / stale.name
        counter = 1
        while target.exists():
            target = root / f"{stale.name}.{counter}"
            counter += 1
        shutil.move(str(stale), str(target))
        moved.append(str(target))
    return moved


def backup(path: Path, *, home: Path | None = None) -> Path | None:
    if not path.exists():
        return None
    stamp = time.strftime("%Y%m%d%H%M%S")
    if path.name == "a2a" and path.parent.name == "plugins" and home is not None:
        root = home / STATE_DIR_NAME / "backups" / stamp
        root.mkdir(parents=True, exist_ok=True)
        target = root / path.name
    else:
        target = path.with_name(f"{path.name}.bak.{stamp}")
    counter = 1
    base_target = target
    while target.exists():
        if path.name == "a2a" and path.parent.name == "plugins" and home is not None:
            target = base_target.with_name(f"{base_target.name}.{counter}")
        else:
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
        if os.environ.get(key) not in (None, "") and key in os.environ.get("HERMES_A2A_INSTALL_ENV_OVERRIDES", "").split(",")
    }


def build_compat_webhook_routes(a2a: dict[str, Any]) -> dict[str, Any]:
    wake = a2a.get("wake") if isinstance(a2a.get("wake"), dict) else {}
    dashboard = a2a.get("dashboard") if isinstance(a2a.get("dashboard"), dict) else {}
    secret = str(wake.get("secret") or "")
    route_name = str(wake.get("route") or "a2a_trigger").strip() or "a2a_trigger"
    trigger: dict[str, Any] = {"secret": secret, "prompt": str(wake.get("prompt") or "[A2A trigger]")}
    session = wake.get("session") if isinstance(wake.get("session"), dict) else {}
    platform = str(session.get("platform") or "").strip()
    chat_id = str(session.get("chat_id") or "").strip()
    if platform and chat_id:
        deliver_extra: dict[str, Any] = {"chat_id": chat_id}
        if session.get("thread_id") not in (None, ""):
            deliver_extra["thread_id"] = session["thread_id"]
        actor = session.get("actor") if isinstance(session.get("actor"), dict) else {}
        source: dict[str, Any] = {
            "platform": platform,
            "chat_type": str(session.get("chat_type") or "dm"),
            "chat_id": chat_id,
            "user_id": str(actor.get("id") or chat_id),
            "user_name": str(actor.get("name") or "user"),
        }
        if session.get("thread_id") not in (None, ""):
            source["thread_id"] = session["thread_id"]
        trigger.update({"deliver": platform, "deliver_extra": deliver_extra, "source": source})
    routes = {route_name: trigger}
    if dashboard.get("enabled", True) is not False:
        routes[str(dashboard.get("route") or "a2a_dashboard")] = {"secret": secret, "prompt": "[A2A dashboard]"}
    return routes


def install_profile(home: Path, source_dir: Path, dashboard_dir: Path, *, dry_run: bool = False, answers=None) -> dict[str, Any]:
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
    legacy_identity_from_env = any(line.startswith("A2A_AGENT_NAME=") or line.startswith("A2A_AGENT_DESCRIPTION=") for line in existing_env)
    a2a_host = env_or_config(existing_env, "A2A_HOST", existing_server.get("host"), default_host, explicit_keys)
    a2a_port = env_or_config(existing_env, "A2A_PORT", existing_server.get("port"), default_port, explicit_keys)
    a2a_public_url = env_or_config(
        existing_env,
        "A2A_PUBLIC_URL",
        existing_server.get("public_url"),
        os.environ.get("A2A_PUBLIC_URL") or f"http://{a2a_host}:{a2a_port}",
        explicit_keys,
    ).rstrip("/")
    existing_identity = existing_a2a.get("identity", {}) if isinstance(existing_a2a.get("identity"), dict) else {}
    a2a_agent_name = env_or_config(
        existing_env,
        "A2A_AGENT_NAME",
        existing_identity.get("name"),
        os.environ.get("A2A_AGENT_NAME") or "hermes-agent",
        explicit_keys,
    )
    a2a_agent_description = env_or_config(
        existing_env,
        "A2A_AGENT_DESCRIPTION",
        existing_identity.get("description"),
        os.environ.get("A2A_AGENT_DESCRIPTION") or "Hermes A2A profile",
        explicit_keys,
    )
    if not legacy_identity_from_env and not existing_identity and a2a_agent_name == "hermes-agent":
        a2a_agent_name = "primary_agent"
        a2a_agent_description = "Primary Hermes A2A profile"
    a2a_require_auth = env_or_config(
        existing_env,
        "A2A_REQUIRE_AUTH",
        existing_server.get("require_auth"),
        os.environ.get("A2A_REQUIRE_AUTH") or "true",
        explicit_keys,
    )
    webhook_port = choose_webhook_port(home, cfg, existing_env)
    if answers is not None:
        a2a_host = answers.host
        a2a_port = str(answers.port)
        a2a_public_url = answers.public_url.rstrip("/")
        a2a_agent_name = answers.identity_name
        a2a_agent_description = answers.identity_description
        a2a_require_auth = "true" if answers.require_auth else "false"
        webhook_port = answers.webhook_port
    existing_wake = existing_a2a.get("wake", {}) if isinstance(existing_a2a.get("wake"), dict) else {}
    secret = str(existing_wake.get("secret") or "").strip() or env_value(existing_env, "A2A_WEBHOOK_SECRET", lambda: secrets.token_hex(24))
    auth_token = str(existing_server.get("auth_token") or "").strip() or env_value(existing_env, "A2A_AUTH_TOKEN", lambda: secrets.token_hex(24))
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
    backups.extend(move_stale_plugin_backups(home))
    for path in (plugin_dir, config_path, env_path):
        target = backup(path, home=home)
        if target is not None:
            backups.append(str(target))

    if plugin_dir.exists():
        shutil.rmtree(plugin_dir)
    shutil.copytree(source_dir, plugin_dir, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if dashboard_dir.exists():
        shutil.copytree(dashboard_dir, plugin_dir / "dashboard", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    env_lines = list(existing_env)
    secret_store = getattr(answers, "secret_store", "config") if answers is not None else "config"
    if secret_store == "env":
        ensure_env(env_lines, "A2A_AUTH_TOKEN", auth_token)
        ensure_env(env_lines, "A2A_WEBHOOK_SECRET", secret)
        if remote_token_env:
            ensure_env(env_lines, remote_token_env, remote_token)
    ensure_env(env_lines, "WEBHOOK_ENABLED", "true")
    ensure_env(env_lines, "WEBHOOK_PORT", str(webhook_port))

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

    chat_id = os.environ.get("A2A_HOME_CHAT_ID", "").strip()

    a2a = cfg.setdefault("a2a", {})
    if not isinstance(a2a, dict):
        a2a = {}
        cfg["a2a"] = a2a
    a2a["enabled"] = True
    a2a["identity"] = {
        **(a2a.get("identity") if isinstance(a2a.get("identity"), dict) else {}),
        "name": a2a_agent_name,
        "description": a2a_agent_description,
    }
    a2a["server"] = {
        **(a2a.get("server") if isinstance(a2a.get("server"), dict) else {}),
        "host": a2a_host,
        "port": int(a2a_port),
        "public_url": a2a_public_url,
        "require_auth": bool_value(a2a_require_auth, True),
        **({"auth_token_env": "A2A_AUTH_TOKEN"} if answers is not None and getattr(answers, "secret_store", "config") == "env" else {"auth_token": auth_token}),
    }
    a2a["wake"] = {
        **(a2a.get("wake") if isinstance(a2a.get("wake"), dict) else {}),
        "enabled": True,
        "port": webhook_port,
        **({"secret_env": "A2A_WEBHOOK_SECRET"} if answers is not None and getattr(answers, "secret_store", "config") == "env" else {"secret": secret}),
        "route": "a2a_trigger",
        "prompt": "[A2A trigger]",
        "mode": "owner_session",
    }
    if answers is not None and getattr(answers, "wake_platform", "") and getattr(answers, "wake_chat_id", ""):
        a2a["wake"]["session"] = {
            "platform": answers.wake_platform,
            "chat_id": answers.wake_chat_id,
            "chat_type": answers.wake_chat_type or "dm",
            "actor": {
                "id": answers.wake_actor_id or answers.wake_chat_id,
                "name": answers.wake_actor_name or "user",
            },
        }
        if getattr(answers, "wake_thread_id", ""):
            a2a["wake"]["session"]["thread_id"] = int(answers.wake_thread_id) if str(answers.wake_thread_id).isdigit() else answers.wake_thread_id
    elif platform and chat_id:
        a2a["wake"]["session"] = {
            "platform": platform,
            "chat_id": chat_id,
            "chat_type": os.environ.get("A2A_HOME_CHAT_TYPE", "dm").strip() or "dm",
            "actor": {
                "id": os.environ.get("A2A_HOME_USER_ID", "").strip() or chat_id,
                "name": os.environ.get("A2A_HOME_USER_NAME", "").strip() or "user",
            },
        }
    a2a["dashboard"] = {**(a2a.get("dashboard") if isinstance(a2a.get("dashboard"), dict) else {}), "enabled": True, "route": "a2a_dashboard"}
    routes = build_compat_webhook_routes(a2a)

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
        compat_routes = build_compat_webhook_routes(a2a)
        if not compat_routes.get("a2a_trigger", {}).get("secret"):
            compat_routes["a2a_trigger"]["secret"] = secret
        if "a2a_dashboard" in compat_routes and not compat_routes["a2a_dashboard"].get("secret"):
            compat_routes["a2a_dashboard"]["secret"] = secret
        for route_name in ("a2a_trigger", "a2a_dashboard"):
            routes.pop(route_name, None)
        routes.update(compat_routes)

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
    for route_name in ("a2a_trigger", "a2a_dashboard"):
        platform_routes.pop(route_name, None)
    platform_routes.update(routes)

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
        agent = {"name": remote_name, "url": remote_url, "description": os.environ.get("A2A_REMOTE_DESCRIPTION", "").strip(), "enabled": True, "tags": ["local"], "trust_level": "trusted"}
        if secret_store == "env" and remote_token_env:
            agent["auth_token_env"] = remote_token_env
        elif remote_token:
            agent["auth_token"] = remote_token
        agents.append(agent)

    stale_a2a_env_keys = {
        "A2A_ENABLED",
        "A2A_HOST",
        "A2A_PORT",
        "A2A_PUBLIC_URL",
        "A2A_AGENT_NAME",
        "A2A_AGENT_DESCRIPTION",
        "A2A_REQUIRE_AUTH",
    }
    if secret_store == "config":
        stale_a2a_env_keys.update({"A2A_AUTH_TOKEN", "A2A_WEBHOOK_SECRET"})
        if remote_token_env:
            stale_a2a_env_keys.add(remote_token_env)
    env_lines = [line for line in env_lines if (line.split("=", 1)[0] if "=" in line else "") not in stale_a2a_env_keys]

    write_text(config_path, dump_config(cfg))
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
