from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


PromptFn = Callable[[str, str], str]
ConfirmFn = Callable[[str, bool], bool]


@dataclass
class WizardAnswers:
    identity_name: str
    identity_description: str
    host: str
    port: int
    public_url: str
    require_auth: bool
    webhook_port: int
    secret_store: str = "config"
    wake_enabled: bool = True
    wake_platform: str = ""
    wake_chat_id: str = ""
    wake_chat_type: str = "dm"
    wake_thread_id: str = ""
    wake_actor_id: str = ""
    wake_actor_name: str = "user"
    remote_agents: list[dict[str, Any]] = field(default_factory=list)


def _ask(prompt_fn: PromptFn, question: str, default: str) -> str:
    value = prompt_fn(question, default).strip()
    return value or default


def _confirm(confirm_fn: ConfirmFn, question: str, default: bool) -> bool:
    return confirm_fn(question, default)


def build_canonical_a2a_from_answers(answers: WizardAnswers, *, auth_token: str, wake_secret: str) -> dict[str, Any]:
    a2a: dict[str, Any] = {
        "enabled": True,
        "identity": {"name": answers.identity_name, "description": answers.identity_description},
        "server": {
            "host": answers.host,
            "port": answers.port,
            "public_url": answers.public_url.rstrip("/"),
            "require_auth": answers.require_auth,
        },
        "wake": {
            "enabled": answers.wake_enabled,
            "port": answers.webhook_port,
            "route": "a2a_trigger",
            "prompt": "[A2A trigger]",
            "mode": "owner_session",
        },
        "dashboard": {"enabled": True, "route": "a2a_dashboard"},
        "runtime": {
            "sync_response_timeout_seconds": 120,
            "active_task_timeout_seconds": 7200,
            "max_pending_tasks": 10,
        },
        "security": {
            "allow_unconfigured_urls": False,
            "redact_outbound": True,
            "max_message_chars": 50000,
            "max_response_chars": 100000,
            "max_request_bytes": 1048576,
            "max_raw_part_bytes": 262144,
            "max_parts": 20,
            "rate_limit_per_minute": 20,
        },
    }
    if answers.secret_store == "env":
        a2a["server"]["auth_token_env"] = "A2A_AUTH_TOKEN"
        a2a["wake"]["secret_env"] = "A2A_WEBHOOK_SECRET"
    else:
        a2a["server"]["auth_token"] = auth_token
        a2a["wake"]["secret"] = wake_secret
    if answers.wake_platform and answers.wake_chat_id:
        session: dict[str, Any] = {
            "platform": answers.wake_platform,
            "chat_id": answers.wake_chat_id,
            "chat_type": answers.wake_chat_type or "dm",
            "actor": {
                "id": answers.wake_actor_id or answers.wake_chat_id,
                "name": answers.wake_actor_name or "user",
            },
        }
        if answers.wake_thread_id:
            session["thread_id"] = int(answers.wake_thread_id) if answers.wake_thread_id.isdigit() else answers.wake_thread_id
        a2a["wake"]["session"] = session
    if answers.remote_agents:
        agents = []
        for raw in answers.remote_agents:
            agent = {
                "name": raw["name"],
                "url": raw["url"].rstrip("/"),
                "description": raw.get("description", ""),
                "enabled": True,
                "tags": raw.get("tags", ["local"]),
                "trust_level": raw.get("trust_level", "trusted"),
            }
            if answers.secret_store == "env" and raw.get("auth_token_env"):
                agent["auth_token_env"] = raw["auth_token_env"]
            elif raw.get("auth_token"):
                agent["auth_token"] = raw["auth_token"]
            agents.append(agent)
        a2a["agents"] = agents
    return a2a


def collect_wizard_answers(
    *,
    profile_name: str,
    default_port: int,
    default_webhook_port: int,
    prompt_fn: PromptFn,
    confirm_fn: ConfirmFn,
) -> WizardAnswers:
    name_default = "primary_agent" if profile_name == "default" else profile_name.replace("hermes_", "").replace("-", "_")
    identity_name = _ask(prompt_fn, "A2A agent name", name_default)
    identity_description = _ask(prompt_fn, "A2A agent description", f"{identity_name} Hermes profile")
    host = _ask(prompt_fn, "A2A bind host", "127.0.0.1")
    port = int(_ask(prompt_fn, "A2A server port", str(default_port)))
    public_url = _ask(prompt_fn, "A2A public URL", f"http://{host}:{port}")
    require_auth = _confirm(confirm_fn, "Require bearer auth", True)
    webhook_port = int(_ask(prompt_fn, "Webhook wake port", str(default_webhook_port)))
    secret_store = _ask(prompt_fn, "Secret store (config/env)", "config").lower()
    if secret_store not in {"config", "env"}:
        secret_store = "config"
    wake_enabled = _confirm(confirm_fn, "Enable wake session routing", True)
    wake_platform = ""
    wake_chat_id = ""
    wake_chat_type = "dm"
    wake_thread_id = ""
    wake_actor_id = ""
    wake_actor_name = "user"
    if wake_enabled:
        wake_platform = _ask(prompt_fn, "Wake platform (discord/telegram/custom/none)", "discord")
        if wake_platform == "none":
            wake_platform = ""
        if wake_platform:
            wake_chat_id = _ask(prompt_fn, "Wake chat/channel ID", "")
            wake_chat_type = _ask(prompt_fn, "Wake chat type", "group" if wake_platform == "discord" else "dm")
            if wake_platform == "telegram":
                wake_thread_id = _ask(prompt_fn, "Telegram thread/topic ID (optional)", "")
            wake_actor_id = _ask(prompt_fn, "Wake actor ID (session selector, not allowlist)", wake_chat_id)
            wake_actor_name = _ask(prompt_fn, "Wake actor name", "user")
    return WizardAnswers(
        identity_name=identity_name,
        identity_description=identity_description,
        host=host,
        port=port,
        public_url=public_url,
        require_auth=require_auth,
        webhook_port=webhook_port,
        secret_store=secret_store,
        wake_enabled=wake_enabled,
        wake_platform=wake_platform,
        wake_chat_id=wake_chat_id,
        wake_chat_type=wake_chat_type,
        wake_thread_id=wake_thread_id,
        wake_actor_id=wake_actor_id,
        wake_actor_name=wake_actor_name,
    )
