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
    auth_token: str = ""
    wake_secret: str = ""


def _ask(prompt_fn: PromptFn, question: str, default: str) -> str:
    value = prompt_fn(question, default).strip()
    return value or default


def _confirm(confirm_fn: ConfirmFn, question: str, default: bool) -> bool:
    return confirm_fn(question, default)


def _session_ref_from_answers(answers: WizardAnswers) -> Any:
    if not answers.wake_platform or not answers.wake_chat_id:
        return None
    ref: dict[str, Any] = {"platform": answers.wake_platform, "chat_id": answers.wake_chat_id}
    if answers.wake_thread_id:
        ref["thread_id"] = int(answers.wake_thread_id) if answers.wake_thread_id.isdigit() else answers.wake_thread_id
    return ref


def build_canonical_a2a_from_answers(answers: WizardAnswers, *, auth_token: str, wake_secret: str) -> dict[str, Any]:
    server: dict[str, Any] = {"port": answers.port}
    if answers.host and answers.host != "127.0.0.1":
        server["host"] = answers.host
    default_public_url = f"http://{answers.host or '127.0.0.1'}:{answers.port}".rstrip("/")
    public_url = (answers.public_url or default_public_url).rstrip("/")
    if public_url != default_public_url:
        server["public_url"] = public_url
    if answers.require_auth is not True:
        server["require_auth"] = answers.require_auth

    wake: dict[str, Any] = {"port": answers.webhook_port}
    if answers.wake_enabled is False:
        wake["enabled"] = False
    session_ref = _session_ref_from_answers(answers)
    if session_ref:
        wake["session_ref"] = session_ref

    a2a: dict[str, Any] = {
        "enabled": True,
        "identity": {"name": answers.identity_name, "description": answers.identity_description},
        "server": server,
        "wake": wake,
    }
    if answers.secret_store == "env":
        a2a["server"]["auth_token_env"] = "A2A_AUTH_TOKEN"
        a2a["wake"]["secret_env"] = "A2A_WEBHOOK_SECRET"
    else:
        a2a["server"]["auth_token"] = auth_token
        a2a["wake"]["secret"] = wake_secret
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


def _parse_choice_tokens(raw: str) -> list[str]:
    return [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]


def _select_local_agents(raw: str, choices: list[dict[str, Any]], reciprocal: bool) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for token in _parse_choice_tokens(raw):
        match = None
        if token.isdigit():
            index = int(token) - 1
            if 0 <= index < len(choices):
                match = choices[index]
        if match is None:
            wanted = token.lower()
            for choice in choices:
                if wanted in {str(choice.get("profile_name", "")).lower(), str(choice.get("name", "")).lower()}:
                    match = choice
                    break
        if match is None:
            continue
        agent = {
            "name": match["name"],
            "url": match["url"].rstrip("/"),
            "description": match.get("description", ""),
            "auth_token": match.get("auth_token", ""),
            "tags": match.get("tags", ["local"]),
            "trust_level": match.get("trust_level", "trusted"),
            "reciprocal_home": str(match.get("home", "")),
            "reciprocal": reciprocal,
        }
        selected.append(agent)
    return selected


def collect_wizard_answers(
    *,
    profile_name: str,
    default_port: int,
    default_webhook_port: int,
    prompt_fn: PromptFn,
    confirm_fn: ConfirmFn,
    local_agent_choices: list[dict[str, Any]] | None = None,
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
            wake_actor_id = _ask(prompt_fn, "Wake actor ID (your Discord/Telegram user ID; session selector, not auth)", wake_chat_id)
            wake_actor_name = _ask(prompt_fn, "Wake actor name", "user")
    remote_agents: list[dict[str, Any]] = []
    if local_agent_choices:
        selection = _ask(prompt_fn, "Connect local A2A profiles (comma numbers/names, blank none)", "")
        reciprocal = _confirm(confirm_fn, "Also write reciprocal links to selected local profiles", True) if selection else False
        remote_agents.extend(_select_local_agents(selection, local_agent_choices, reciprocal))
    if _confirm(confirm_fn, "Add manual remote A2A agent", False):
        remote_name = _ask(prompt_fn, "Remote agent alias", "")
        remote_url = _ask(prompt_fn, "Remote agent URL", "")
        if remote_name and remote_url:
            remote_agents.append({
                "name": remote_name,
                "url": remote_url.rstrip("/"),
                "description": _ask(prompt_fn, "Remote agent description", ""),
                "auth_token": _ask(prompt_fn, "Remote agent bearer token (optional)", ""),
                "tags": ["manual"],
                "trust_level": "trusted",
            })
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
        remote_agents=remote_agents,
    )
