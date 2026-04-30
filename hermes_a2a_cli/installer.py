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


def _stringify_id(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _coerce_id(value: Any) -> Any:
    text = _stringify_id(value)
    return int(text) if text.isdigit() else text


def _wake_session_ref_from_legacy_session(session: dict[str, Any]) -> Any:
    platform = _stringify_id(session.get("platform"))
    chat_id = _stringify_id(session.get("chat_id"))
    if not platform or not chat_id:
        return None
    ref: dict[str, Any] = {"platform": platform, "chat_id": chat_id}
    if session.get("thread_id") not in (None, ""):
        ref["thread_id"] = _coerce_id(session.get("thread_id"))
    return ref




def _route_resolved_session(route: dict[str, Any]) -> dict[str, Any]:
    source = route.get("source") if isinstance(route.get("source"), dict) else {}
    extra = route.get("deliver_extra") if isinstance(route.get("deliver_extra"), dict) else {}
    platform = _stringify_id(source.get("platform") or route.get("deliver"))
    chat_id = _stringify_id(source.get("chat_id") or extra.get("chat_id"))
    actor_id = _stringify_id(source.get("user_id"))
    if not platform or not chat_id or not actor_id:
        return {}
    session: dict[str, Any] = {
        "platform": platform,
        "chat_id": chat_id,
        "chat_type": _stringify_id(source.get("chat_type")) or "dm",
        "actor": {"id": actor_id, "name": _stringify_id(source.get("user_name")) or "user"},
    }
    thread_id = source.get("thread_id") if source.get("thread_id") not in (None, "") else extra.get("thread_id")
    if thread_id not in (None, ""):
        session["thread_id"] = _coerce_id(thread_id)
    return session


def _existing_a2a_route(cfg: dict[str, Any]) -> dict[str, Any]:
    for path in (("webhook", "extra", "routes"), ("platforms", "webhook", "extra", "routes")):
        routes = _get_nested(cfg, path)
        if isinstance(routes, dict) and isinstance(routes.get("a2a_trigger"), dict):
            return routes["a2a_trigger"]
    return {}


def _existing_resolved_wake_session(cfg: dict[str, Any], existing_wake: dict[str, Any]) -> dict[str, Any]:
    session = existing_wake.get("session") if isinstance(existing_wake.get("session"), dict) else {}
    actor = session.get("actor") if isinstance(session.get("actor"), dict) else {}
    if session.get("platform") and session.get("chat_id") and actor.get("id"):
        return dict(session)
    return _route_resolved_session(_existing_a2a_route(cfg))

def resolve_wake_session(config_or_a2a: dict[str, Any]) -> dict[str, Any]:
    a2a = config_or_a2a.get("a2a") if isinstance(config_or_a2a.get("a2a"), dict) else config_or_a2a
    wake = a2a.get("wake") if isinstance(a2a, dict) and isinstance(a2a.get("wake"), dict) else {}
    session = wake.get("session") if isinstance(wake.get("session"), dict) else {}
    if session:
        return dict(session)
    ref = wake.get("session_ref")
    if ref in (None, "", "none", False):
        return {}
    if ref == "latest":
        try:
            from .multi_install import infer_wake_session_from_history

            inferred = infer_wake_session_from_history(Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))))
        except Exception:
            inferred = {}
        if not inferred:
            return {}
        session = {
            "platform": inferred.get("platform"),
            "chat_id": inferred.get("chat_id"),
            "chat_type": inferred.get("chat_type") or "dm",
            "actor": {"id": inferred.get("actor_id"), "name": inferred.get("actor_name") or "user"},
        }
        if inferred.get("thread_id") not in (None, ""):
            session["thread_id"] = _coerce_id(inferred.get("thread_id"))
        return session
    if not isinstance(ref, dict):
        return {}
    platform = _stringify_id(ref.get("platform"))
    chat_id = _stringify_id(ref.get("chat_id"))
    if not platform or not chat_id:
        return {}
    session = {"platform": platform, "chat_id": chat_id, "chat_type": _stringify_id(ref.get("chat_type")) or "dm"}
    if ref.get("thread_id") not in (None, ""):
        session["thread_id"] = _coerce_id(ref.get("thread_id"))
    actor = ref.get("actor") if isinstance(ref.get("actor"), dict) else {}
    actor_id = _stringify_id(actor.get("id"))
    actor_name = _stringify_id(actor.get("name")) or "user"
    if not actor_id:
        try:
            from .multi_install import infer_wake_session_from_history

            inferred = infer_wake_session_from_history(Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))), preferred_platform=platform)
        except Exception:
            inferred = {}
        if inferred and inferred.get("chat_id") == chat_id:
            actor_id = inferred.get("actor_id", "")
            actor_name = inferred.get("actor_name") or actor_name
            session["chat_type"] = inferred.get("chat_type") or session["chat_type"]
            if "thread_id" not in session and inferred.get("thread_id") not in (None, ""):
                session["thread_id"] = _coerce_id(inferred.get("thread_id"))
    if not actor_id:
        return {}
    session["actor"] = {"id": actor_id, "name": actor_name}
    return session


def is_local_port_available(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _profile_root(home: Path) -> Path:
    home = home.expanduser().resolve()
    if home.parent.name == "profiles":
        return home.parent.parent
    return home


def _profile_config_paths(home: Path) -> list[Path]:
    root = _profile_root(home)
    paths = [root / "config.yaml"]
    profiles_root = root / "profiles"
    if profiles_root.is_dir():
        paths.extend(sorted(child / "config.yaml" for child in profiles_root.iterdir() if child.is_dir()))
    return paths


def _get_nested(mapping: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = mapping
    for key in path:
        current = current.get(key) if isinstance(current, dict) else None
    return current


def _read_env_lines(env_path: Path) -> list[str]:
    return env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []


def _env_line_value(lines: list[str], key: str) -> str:
    prefix = f"{key}="
    for line in lines:
        if line.startswith(prefix):
            return line.split("=", 1)[1].strip()
    return ""


def _configured_profile_port(config_path: Path, kind: str) -> int | None:
    if not config_path.exists():
        return None
    try:
        cfg = load_config(config_path)
    except Exception:
        return None
    candidates: list[Any]
    if kind == "a2a":
        candidates = [_get_nested(cfg, ("a2a", "server", "port"))]
        env_key = "A2A_PORT"
    else:
        candidates = [
            _get_nested(cfg, ("a2a", "wake", "port")),
            _get_nested(cfg, ("platforms", "webhook", "extra", "port")),
            _get_nested(cfg, ("webhook", "extra", "port")),
        ]
        env_key = "WEBHOOK_PORT"
    env_value = _env_line_value(_read_env_lines(config_path.parent / ".env"), env_key)
    if env_value:
        candidates.append(env_value)
    for value in candidates:
        if value in (None, ""):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _used_profile_ports(home: Path, kind: str) -> set[int]:
    used: set[int] = set()
    current = home.expanduser().resolve() / "config.yaml"
    for config_path in _profile_config_paths(home):
        if config_path.resolve() == current or not config_path.exists():
            continue
        port = _configured_profile_port(config_path, kind)
        if port is not None:
            used.add(port)
    return used


def choose_profile_ports(homes: list[Path], kind: str, *, start: int, check_socket: bool = True) -> dict[str, int]:
    """Choose deterministic ports for a batch install.

    Existing configured ports on selected homes are preserved when they are not
    duplicated by another profile. New/missing/conflicting ports are allocated
    sequentially from ``start`` while respecting sibling profile configs and
    currently occupied sockets.
    """
    resolved = [home.expanduser().resolve() for home in homes]
    selected_configs = {home / "config.yaml" for home in resolved}
    configured_by_path: dict[Path, int | None] = {
        config_path: _configured_profile_port(config_path, kind)
        for home in resolved
        for config_path in [home / "config.yaml"]
    }
    used: set[int] = set()
    for home in resolved:
        for config_path in _profile_config_paths(home):
            config_path = config_path.resolve()
            if config_path in selected_configs:
                continue
            port = _configured_profile_port(config_path, kind)
            if port is not None:
                used.add(port)
    duplicate_selected_ports = {
        port
        for port in configured_by_path.values()
        if port is not None and list(configured_by_path.values()).count(port) > 1
    }
    chosen: dict[str, int] = {}
    for home in resolved:
        config_path = home / "config.yaml"
        current = configured_by_path.get(config_path)
        if current is not None and current not in used and current not in duplicate_selected_ports:
            chosen[str(home)] = current
            used.add(current)
            continue
        port = _first_available_port(start, used, check_socket=check_socket)
        chosen[str(home)] = port
        used.add(port)
    return chosen


def _first_available_port(start: int, used: set[int], *, check_socket: bool = True) -> int:
    for port in range(start, 65535):
        if port in used:
            continue
        if not check_socket or is_local_port_available(port):
            return port
    raise InstallError(f"could not find an available local port starting at {start}")


def choose_a2a_port(home: Path, config: dict[str, Any], existing_env: list[str]) -> int:
    raw = os.environ.get("A2A_PORT", "").strip()
    if raw:
        return int(raw)
    current = _get_nested(config, ("a2a", "server", "port"))
    if current not in (None, ""):
        return int(current)
    env_value = _env_line_value(existing_env, "A2A_PORT")
    if env_value:
        return int(env_value)
    return _first_available_port(41731, _used_profile_ports(home, "a2a"))


def choose_webhook_port(home: Path, config: dict[str, Any], existing_env: list[str]) -> int:
    raw = os.environ.get("WEBHOOK_PORT", "").strip() or os.environ.get("A2A_WEBHOOK_PORT", "").strip()
    if raw:
        return int(raw)

    for section_path in (("a2a", "wake"), ("platforms", "webhook", "extra"), ("webhook", "extra")):
        current = _get_nested(config, section_path)
        if isinstance(current, dict) and current.get("port") not in (None, ""):
            return int(current["port"])

    env_value = _env_line_value(existing_env, "WEBHOOK_PORT")
    if env_value:
        return int(env_value)

    return _first_available_port(47644, _used_profile_ports(home, "webhook"))


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
    session = resolve_wake_session(a2a)
    platform = str(session.get("platform") or "").strip()
    chat_id = str(session.get("chat_id") or "").strip()
    if platform and chat_id:
        deliver_extra: dict[str, Any] = {"chat_id": chat_id}
        if session.get("thread_id") not in (None, ""):
            deliver_extra["thread_id"] = session["thread_id"]
        actor = session.get("actor") if isinstance(session.get("actor"), dict) else {}
        actor_id = str(actor.get("id") or "").strip()
        if actor_id:
            source: dict[str, Any] = {
                "platform": platform,
                "chat_type": str(session.get("chat_type") or "dm"),
                "chat_id": chat_id,
                "user_id": actor_id,
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
    default_port = str(choose_a2a_port(home, cfg, existing_env))
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
        default_answer_url = f"http://{answers.host}:{answers.port}"
        a2a_public_url = (answers.public_url or default_answer_url).rstrip("/")
        a2a_agent_name = answers.identity_name
        a2a_agent_description = answers.identity_description
        a2a_require_auth = "true" if answers.require_auth else "false"
        webhook_port = answers.webhook_port
    existing_wake = existing_a2a.get("wake", {}) if isinstance(existing_a2a.get("wake"), dict) else {}
    answer_secret = str(getattr(answers, "wake_secret", "") or "").strip() if answers is not None else ""
    answer_auth_token = str(getattr(answers, "auth_token", "") or "").strip() if answers is not None else ""
    secret = answer_secret or str(existing_wake.get("secret") or "").strip() or env_value(existing_env, "A2A_WEBHOOK_SECRET", lambda: secrets.token_hex(24))
    auth_token = answer_auth_token or str(existing_server.get("auth_token") or "").strip() or env_value(existing_env, "A2A_AUTH_TOKEN", lambda: secrets.token_hex(24))
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
    server_section = {"port": int(a2a_port), **({"auth_token_env": "A2A_AUTH_TOKEN"} if answers is not None and getattr(answers, "secret_store", "config") == "env" else {"auth_token": auth_token})}
    if a2a_host != "127.0.0.1":
        server_section["host"] = a2a_host
    default_public_url = f"http://{a2a_host}:{a2a_port}".rstrip("/")
    if a2a_public_url != default_public_url:
        server_section["public_url"] = a2a_public_url
    if bool_value(a2a_require_auth, True) is not True:
        server_section["require_auth"] = bool_value(a2a_require_auth, True)
    a2a["server"] = server_section
    existing_session_ref = existing_wake.get("session_ref") if isinstance(existing_wake.get("session_ref"), dict) else None
    existing_resolved_session = _existing_resolved_wake_session(cfg, existing_wake)
    a2a["wake"] = {"port": webhook_port, **({"secret_env": "A2A_WEBHOOK_SECRET"} if answers is not None and getattr(answers, "secret_store", "config") == "env" else {"secret": secret})}
    if answers is None and existing_session_ref:
        a2a["wake"]["session_ref"] = dict(existing_session_ref)
        if existing_resolved_session:
            a2a["wake"]["_resolved_session"] = existing_resolved_session
    elif answers is None and existing_resolved_session:
        session_ref = _wake_session_ref_from_legacy_session(existing_resolved_session)
        if session_ref:
            a2a["wake"]["session_ref"] = session_ref
            a2a["wake"]["_resolved_session"] = existing_resolved_session
    if answers is not None and getattr(answers, "wake_platform", "") and getattr(answers, "wake_chat_id", ""):
        session_ref = {"platform": answers.wake_platform, "chat_id": answers.wake_chat_id}
        if getattr(answers, "wake_thread_id", ""):
            session_ref["thread_id"] = int(answers.wake_thread_id) if str(answers.wake_thread_id).isdigit() else answers.wake_thread_id
        a2a["wake"]["session_ref"] = session_ref
        resolved_session = {
            "platform": answers.wake_platform,
            "chat_id": answers.wake_chat_id,
            "chat_type": answers.wake_chat_type or "dm",
            "actor": {
                "id": answers.wake_actor_id or "",
                "name": answers.wake_actor_name or "user",
            },
        }
        if getattr(answers, "wake_thread_id", ""):
            resolved_session["thread_id"] = session_ref["thread_id"]
        a2a["wake"]["_resolved_session"] = resolved_session
    elif platform and chat_id:
        a2a["wake"]["session_ref"] = {"platform": platform, "chat_id": chat_id}
        thread_id = os.environ.get("A2A_HOME_THREAD_ID", "").strip()
        if thread_id:
            a2a["wake"]["session_ref"]["thread_id"] = int(thread_id) if thread_id.isdigit() else thread_id
        legacy_session = {
            "platform": platform,
            "chat_id": chat_id,
            "chat_type": os.environ.get("A2A_HOME_CHAT_TYPE", "dm").strip() or "dm",
            "actor": {
                "id": os.environ.get("A2A_HOME_USER_ID", "").strip(),
                "name": os.environ.get("A2A_HOME_USER_NAME", "").strip() or "user",
            },
        }
        if thread_id:
            legacy_session["thread_id"] = a2a["wake"]["session_ref"]["thread_id"]
        a2a["wake"]["_resolved_session"] = legacy_session
    a2a.pop("dashboard", None)
    a2a.pop("runtime", None)
    a2a.pop("security", None)
    generated_wake_session = a2a.get("wake", {}).pop("_resolved_session", None) if isinstance(a2a.get("wake"), dict) else None
    route_source_a2a = dict(a2a)
    if generated_wake_session:
        route_source_a2a["wake"] = {**a2a["wake"], "session": generated_wake_session}
    routes = build_compat_webhook_routes(route_source_a2a)

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
        compat_routes = build_compat_webhook_routes(route_source_a2a)
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

    remote_specs: list[dict[str, Any]] = []
    if answers is not None:
        remote_specs.extend(getattr(answers, "remote_agents", []) or [])
    remote_name = os.environ.get("A2A_REMOTE_NAME", "").strip()
    remote_url = os.environ.get("A2A_REMOTE_URL", "").strip().rstrip("/")
    if remote_name and remote_url:
        env_agent = {"name": remote_name, "url": remote_url, "description": os.environ.get("A2A_REMOTE_DESCRIPTION", "").strip(), "enabled": True, "tags": ["local"], "trust_level": "trusted"}
        if secret_store == "env" and remote_token_env:
            env_agent["auth_token_env"] = remote_token_env
        elif remote_token:
            env_agent["auth_token"] = remote_token
        remote_specs.append(env_agent)
    for raw in remote_specs:
        if not isinstance(raw, dict):
            continue
        agent_name = str(raw.get("name") or "").strip()
        agent_url = str(raw.get("url") or "").strip().rstrip("/")
        if not agent_name or not agent_url:
            continue
        agents = a2a.setdefault("agents", [])
        if not isinstance(agents, list):
            agents = []
            a2a["agents"] = agents
        agents[:] = [agent for agent in agents if not (isinstance(agent, dict) and agent.get("name") == agent_name)]
        agent = {
            "name": agent_name,
            "url": agent_url,
            "description": str(raw.get("description") or "").strip(),
            "enabled": raw.get("enabled", True) is not False,
            "tags": raw.get("tags") if isinstance(raw.get("tags"), list) else ["local"],
            "trust_level": str(raw.get("trust_level") or "trusted"),
        }
        if raw.get("auth_token_env"):
            agent["auth_token_env"] = str(raw["auth_token_env"]).strip()
        elif raw.get("auth_token"):
            agent["auth_token"] = str(raw["auth_token"]).strip()
        agents.append(agent)

    for raw in remote_specs:
        reciprocal_home = str(raw.get("reciprocal_home") or "").strip() if isinstance(raw, dict) else ""
        if not reciprocal_home or raw.get("reciprocal") is not True:
            continue
        reciprocal_path = Path(reciprocal_home).expanduser().resolve() / "config.yaml"
        if not reciprocal_path.exists():
            continue
        reciprocal_cfg = load_config(reciprocal_path)
        reciprocal_a2a = reciprocal_cfg.setdefault("a2a", {})
        if not isinstance(reciprocal_a2a, dict):
            reciprocal_a2a = {}
            reciprocal_cfg["a2a"] = reciprocal_a2a
        reciprocal_agents = reciprocal_a2a.setdefault("agents", [])
        if not isinstance(reciprocal_agents, list):
            reciprocal_agents = []
            reciprocal_a2a["agents"] = reciprocal_agents
        reciprocal_agents[:] = [agent for agent in reciprocal_agents if not (isinstance(agent, dict) and agent.get("name") == a2a_agent_name)]
        reciprocal_agent = {
            "name": a2a_agent_name,
            "url": a2a_public_url,
            "description": a2a_agent_description,
            "enabled": True,
            "tags": ["local"],
            "trust_level": "trusted",
            "auth_token": auth_token,
        }
        backup(reciprocal_path)
        reciprocal_agents.append(reciprocal_agent)
        write_text(reciprocal_path, dump_config(reciprocal_cfg))

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
