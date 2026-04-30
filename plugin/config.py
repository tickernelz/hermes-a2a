"""Configuration helpers for the A2A plugin."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from .paths import config_path


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _positive_int(value: Any, default: int) -> int:
    result = _int(value, default)
    return result if result > 0 else default


def _load_yaml_config() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return {}
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_config() -> dict[str, Any]:
    try:
        from hermes_cli.config import load_config as hermes_load_config

        data = hermes_load_config() or {}
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return _load_yaml_config()


@dataclass(frozen=True)
class IdentityConfig:
    name: str
    description: str


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int
    public_url: str
    require_auth: bool
    sync_response_timeout_seconds: int
    active_task_timeout_seconds: int
    max_pending_tasks: int
    auth_token: str


@dataclass(frozen=True)
class WakeConfig:
    enabled: bool
    port: int
    secret: str
    route: str
    prompt: str
    mode: str
    session: dict[str, Any]


@dataclass(frozen=True)
class SecurityConfig:
    allow_unconfigured_urls: bool
    max_message_chars: int
    max_response_chars: int
    max_request_bytes: int
    max_raw_part_bytes: int
    max_parts: int
    rate_limit_per_minute: int


def _a2a_section(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config if config is not None else load_config()
    a2a = cfg.get("a2a", {}) if isinstance(cfg, dict) else {}
    return a2a if isinstance(a2a, dict) else {}


def _env_ref_value(section: dict[str, Any], key: str) -> str:
    direct = str(section.get(key) or "").strip()
    if direct:
        return direct
    env_name = str(section.get(f"{key}_env") or "").strip()
    if env_name:
        return os.getenv(env_name, "").strip()
    return ""


def get_identity_config(config: dict[str, Any] | None = None) -> IdentityConfig:
    a2a = _a2a_section(config)
    identity = a2a.get("identity", {})
    if not isinstance(identity, dict):
        identity = {}
    name = str(identity.get("name") or "hermes-agent").strip()
    description = str(identity.get("description") or "A self-improving AI agent powered by Hermes").strip()
    return IdentityConfig(name=name, description=description)


def get_server_config(config: dict[str, Any] | None = None) -> ServerConfig:
    a2a = _a2a_section(config)
    server = a2a.get("server", {})
    if not isinstance(server, dict):
        server = {}

    host = str(server.get("host") or "127.0.0.1")
    port = _int(server.get("port"), 41731)
    public_url = (str(server.get("public_url") or "") or f"http://{host}:{port}").rstrip("/")
    require_auth = server.get("require_auth", True) is not False
    runtime = a2a.get("runtime", {})
    if not isinstance(runtime, dict):
        runtime = {}
    sync_response_timeout_seconds = _positive_int(
        runtime.get("sync_response_timeout_seconds", server.get("sync_response_timeout_seconds")),
        120,
    )
    active_task_timeout_seconds = _positive_int(
        runtime.get("active_task_timeout_seconds", server.get("active_task_timeout_seconds")),
        7200,
    )
    max_pending_tasks = _positive_int(runtime.get("max_pending_tasks", server.get("max_pending_tasks")), 10)
    return ServerConfig(
        host=host,
        port=port,
        public_url=public_url,
        require_auth=require_auth,
        sync_response_timeout_seconds=sync_response_timeout_seconds,
        active_task_timeout_seconds=active_task_timeout_seconds,
        max_pending_tasks=max_pending_tasks,
        auth_token=_env_ref_value(server, "auth_token"),
    )



def get_wake_config(config: dict[str, Any] | None = None) -> WakeConfig:
    a2a = _a2a_section(config)
    wake = a2a.get("wake", {})
    if not isinstance(wake, dict):
        wake = {}
    session = wake.get("session") if isinstance(wake.get("session"), dict) else {}
    if not session and isinstance(wake.get("session_ref"), dict):
        ref = wake["session_ref"]
        platform = str(ref.get("platform") or "").strip()
        chat_id = str(ref.get("chat_id") or "").strip()
        if platform and chat_id:
            session = {"platform": platform, "chat_id": chat_id}
            if ref.get("thread_id") not in (None, ""):
                session["thread_id"] = ref["thread_id"]
    return WakeConfig(
        enabled=wake.get("enabled", True) is not False,
        port=_int(wake.get("port"), 47644),
        secret=_env_ref_value(wake, "secret"),
        route=str(wake.get("route") or "a2a_trigger").strip(),
        prompt=str(wake.get("prompt") or "[A2A trigger]").strip(),
        mode=str(wake.get("mode") or "owner_session").strip(),
        session=dict(session),
    )


def get_security_config(config: dict[str, Any] | None = None) -> SecurityConfig:
    a2a = _a2a_section(config)
    security = a2a.get("security", {})
    if not isinstance(security, dict):
        security = {}
    return SecurityConfig(
        allow_unconfigured_urls=_truthy(security.get("allow_unconfigured_urls")),
        max_message_chars=_int(security.get("max_message_chars"), 50_000),
        max_response_chars=_int(security.get("max_response_chars"), 100_000),
        max_request_bytes=_int(security.get("max_request_bytes"), 1_048_576),
        max_raw_part_bytes=_int(security.get("max_raw_part_bytes"), 262_144),
        max_parts=_int(security.get("max_parts"), 20),
        rate_limit_per_minute=_int(security.get("rate_limit_per_minute"), 20),
    )


def _normalize_url(url: str) -> str:
    return (url or "").strip().rstrip("/")


def validate_url(url: str) -> str:
    normalized = _normalize_url(url)
    parsed = urlparse(normalized)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("A2A URL must be an http(s) URL")
    return normalized


def _token_for_agent(agent: dict[str, Any]) -> str:
    env_name = str(agent.get("auth_token_env") or "").strip()
    if env_name:
        return os.getenv(env_name, "").strip()
    return str(agent.get("auth_token") or "").strip()


def load_agents(config: dict[str, Any] | None = None, *, include_disabled: bool = False) -> list[dict[str, Any]]:
    a2a = _a2a_section(config)
    raw_agents = a2a.get("agents", [])
    if not isinstance(raw_agents, list):
        return []

    agents: list[dict[str, Any]] = []
    for raw in raw_agents:
        if not isinstance(raw, dict):
            continue
        enabled = raw.get("enabled", True)
        if not include_disabled and enabled is False:
            continue
        name = str(raw.get("name") or "").strip()
        url = _normalize_url(str(raw.get("url") or ""))
        if not name or not url:
            continue
        agent = dict(raw)
        agent["name"] = name
        agent["url"] = url
        agent["enabled"] = enabled is not False
        agent["auth_token"] = _token_for_agent(raw)
        agent["auth_token_env"] = str(raw.get("auth_token_env") or "").strip()
        agents.append(agent)
    return agents


def find_agent_by_name(name: str, config: dict[str, Any] | None = None) -> dict[str, Any] | None:
    wanted = (name or "").strip().lower()
    if not wanted:
        return None
    for agent in load_agents(config):
        if str(agent.get("name", "")).lower() == wanted:
            return agent
    return None


def find_agent_by_url(url: str, config: dict[str, Any] | None = None) -> dict[str, Any] | None:
    normalized = _normalize_url(url)
    for agent in load_agents(config):
        if _normalize_url(str(agent.get("url") or "")) == normalized:
            return agent
    return None
