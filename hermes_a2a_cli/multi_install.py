from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .installer import choose_profile_ports, load_config
from .wizard import WizardAnswers


@dataclass(frozen=True)
class HermesProfile:
    name: str
    home: Path
    existing_config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GeneratedProfile:
    source: HermesProfile
    answers: WizardAnswers


def derive_agent_name(profile_name: str) -> str:
    raw = "jono" if profile_name in {"default", "main"} else profile_name
    raw = raw.removeprefix("hermes_")
    raw = re.sub(r"[^A-Za-z0-9_]+", "_", raw).strip("_").lower()
    return raw or "hermes_agent"


def discover_profiles(root_home: Path) -> list[HermesProfile]:
    root_home = root_home.expanduser().resolve()
    profiles: list[HermesProfile] = []
    default_config = root_home / "config.yaml"
    if default_config.exists():
        profiles.append(HermesProfile("default", root_home, _load_existing(default_config)))
    profiles_root = root_home / "profiles"
    if profiles_root.is_dir():
        for child in sorted(profiles_root.iterdir()):
            config_path = child / "config.yaml"
            if child.is_dir() and config_path.exists():
                profiles.append(HermesProfile(child.name, child.resolve(), _load_existing(config_path)))
    return profiles


def build_full_mesh(profiles: list[GeneratedProfile]) -> dict[str, list[dict[str, Any]]]:
    mesh: dict[str, list[dict[str, Any]]] = {}
    for profile in profiles:
        agents: list[dict[str, Any]] = []
        for peer in profiles:
            if peer.source.home == profile.source.home:
                continue
            agent = {
                "name": peer.answers.identity_name,
                "url": peer.answers.public_url.rstrip("/"),
                "description": peer.answers.identity_description,
                "enabled": True,
                "tags": ["local"],
                "trust_level": "trusted",
            }
            if peer.answers.auth_token:
                agent["auth_token"] = peer.answers.auth_token
            agents.append(agent)
        mesh[str(profile.source.home)] = agents
    return mesh


def infer_wake_session_from_history(home: Path, *, preferred_platform: str | None = None) -> dict[str, str]:
    sessions_path = home.expanduser().resolve() / "sessions" / "sessions.json"
    if not sessions_path.exists():
        return {}
    try:
        data = json.loads(sessions_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    candidates: list[tuple[str, dict[str, Any]]] = []
    for session in data.values():
        if not isinstance(session, dict):
            continue
        origin = session.get("origin") if isinstance(session.get("origin"), dict) else {}
        platform = str(origin.get("platform") or "").strip()
        chat_id = str(origin.get("chat_id") or "").strip()
        user_id = str(origin.get("user_id") or origin.get("user_id_alt") or "").strip()
        if not platform or not chat_id or not user_id:
            continue
        if preferred_platform and platform != preferred_platform:
            continue
        if platform == "webhook":
            continue
        updated = str(session.get("updated_at") or session.get("created_at") or "")
        candidates.append((updated, origin))
    if not candidates:
        return {}
    _updated, origin = sorted(candidates, key=lambda item: item[0], reverse=True)[0]
    inferred = {
        "platform": str(origin.get("platform") or "").strip(),
        "chat_id": str(origin.get("chat_id") or "").strip(),
        "chat_type": str(origin.get("chat_type") or "dm").strip() or "dm",
        "actor_id": str(origin.get("user_id") or origin.get("user_id_alt") or "").strip(),
        "actor_name": str(origin.get("user_name") or "user").strip() or "user",
    }
    if origin.get("thread_id") not in (None, ""):
        inferred["thread_id"] = str(origin["thread_id"])
    return inferred


def build_generated_profiles(
    selected: list[HermesProfile],
    *,
    a2a_ports: dict[str, int] | None = None,
    webhook_ports: dict[str, int] | None = None,
    wake_defaults: dict[str, str] | None = None,
    topology: str = "full_mesh",
) -> list[GeneratedProfile]:
    if topology != "full_mesh":
        raise ValueError(f"unsupported topology: {topology}")
    homes = [profile.home for profile in selected]
    a2a_ports = a2a_ports or choose_profile_ports(homes, "a2a", start=41731, check_socket=False)
    webhook_ports = webhook_ports or choose_profile_ports(homes, "webhook", start=47644, check_socket=False)
    wake_defaults = wake_defaults or {}
    generated: list[GeneratedProfile] = []
    for profile in selected:
        identity_name = _existing_identity_name(profile.existing_config) or derive_agent_name(profile.name)
        identity_description = _existing_identity_description(profile.existing_config) or f"{identity_name} Hermes profile"
        host = _existing_host(profile.existing_config) or "127.0.0.1"
        port = a2a_ports[str(profile.home)]
        public_url = _existing_public_url(profile.existing_config)
        if not public_url or _url_uses_port(public_url, _existing_port(profile.existing_config)):
            public_url = f"http://{host}:{port}"
        wake_ref = _existing_wake_ref(profile.existing_config)
        wake_session = _existing_wake_session(profile.existing_config)
        if not wake_ref and wake_session:
            wake_ref = _legacy_session_to_ref(wake_session)
        answers = WizardAnswers(
            identity_name=identity_name,
            identity_description=identity_description,
            host=host,
            port=port,
            public_url=public_url.rstrip("/"),
            require_auth=_existing_require_auth(profile.existing_config),
            webhook_port=webhook_ports[str(profile.home)],
            secret_store="config",
            wake_enabled=True,
            wake_platform=str(wake_ref.get("platform") or wake_defaults.get("platform") or ""),
            wake_chat_id=str(wake_ref.get("chat_id") or wake_defaults.get("chat_id") or ""),
            wake_chat_type=str(wake_session.get("chat_type") or wake_defaults.get("chat_type") or "group"),
            wake_thread_id=str(wake_ref.get("thread_id") or wake_defaults.get("thread_id") or ""),
            wake_actor_id=str(wake_defaults.get("actor_id") or _nested(wake_session, ("actor", "id")) or ""),
            wake_actor_name=str(wake_defaults.get("actor_name") or _nested(wake_session, ("actor", "name")) or "user"),
            auth_token=_existing_auth_token(profile.existing_config) or secrets.token_hex(24),
            wake_secret=_existing_wake_secret(profile.existing_config) or secrets.token_hex(24),
        )
        generated.append(GeneratedProfile(profile, answers))
    mesh = build_full_mesh(generated)
    for profile in generated:
        profile.answers.remote_agents = mesh[str(profile.source.home)]
    return generated


def preview_generated_profiles(profiles: list[GeneratedProfile]) -> list[str]:
    lines: list[str] = []
    for profile in profiles:
        peers = ", ".join(agent["name"] for agent in profile.answers.remote_agents) or "none"
        wake = "disabled"
        if profile.answers.wake_platform and profile.answers.wake_chat_id:
            wake = f"{profile.answers.wake_platform}:{profile.answers.wake_chat_id}"
            if profile.answers.wake_thread_id:
                wake += f":{profile.answers.wake_thread_id}"
        lines.append(
            f"{profile.source.name}: agent={profile.answers.identity_name} "
            f"a2a={profile.answers.public_url} wake_port={profile.answers.webhook_port} "
            f"wake_session={wake} connects_to={peers}"
        )
    return lines


def _load_existing(config_path: Path) -> dict[str, Any]:
    try:
        return load_config(config_path)
    except Exception:
        return {}


def _nested(mapping: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = mapping
    for key in path:
        current = current.get(key) if isinstance(current, dict) else None
    return current


def _existing_identity_name(config: dict[str, Any]) -> str:
    return str(_nested(config, ("a2a", "identity", "name")) or "").strip()


def _existing_identity_description(config: dict[str, Any]) -> str:
    return str(_nested(config, ("a2a", "identity", "description")) or "").strip()


def _existing_host(config: dict[str, Any]) -> str:
    return str(_nested(config, ("a2a", "server", "host")) or "").strip()


def _existing_port(config: dict[str, Any]) -> int | None:
    value = _nested(config, ("a2a", "server", "port"))
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _existing_public_url(config: dict[str, Any]) -> str:
    return str(_nested(config, ("a2a", "server", "public_url")) or "").strip()


def _existing_require_auth(config: dict[str, Any]) -> bool:
    value = _nested(config, ("a2a", "server", "require_auth"))
    return True if value is None else bool(value)


def _existing_auth_token(config: dict[str, Any]) -> str:
    return str(_nested(config, ("a2a", "server", "auth_token")) or "").strip()


def _existing_wake_secret(config: dict[str, Any]) -> str:
    return str(_nested(config, ("a2a", "wake", "secret")) or "").strip()


def _existing_wake_session(config: dict[str, Any]) -> dict[str, Any]:
    session = _nested(config, ("a2a", "wake", "session"))
    return session if isinstance(session, dict) else {}


def _url_uses_port(url: str, port: int | None) -> bool:
    return port is not None and url.rstrip("/").endswith(f":{port}")


def _existing_wake_ref(config: dict[str, Any]) -> dict[str, Any]:
    ref = _nested(config, ("a2a", "wake", "session_ref"))
    return ref if isinstance(ref, dict) else {}


def _legacy_session_to_ref(session: dict[str, Any]) -> dict[str, Any]:
    platform = str(session.get("platform") or "").strip()
    chat_id = str(session.get("chat_id") or "").strip()
    if not platform or not chat_id:
        return {}
    ref: dict[str, Any] = {"platform": platform, "chat_id": chat_id}
    if session.get("thread_id") not in (None, ""):
        ref["thread_id"] = session["thread_id"]
    return ref
