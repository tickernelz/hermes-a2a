from hermes_a2a_cli.wizard import WizardAnswers, build_canonical_a2a_from_answers, collect_wizard_answers


def test_build_canonical_a2a_from_discord_answers():
    answers = WizardAnswers(
        identity_name="primary_agent",
        identity_description="Primary profile",
        host="127.0.0.1",
        port=41731,
        public_url="http://127.0.0.1:41731",
        require_auth=True,
        webhook_port=47644,
        wake_platform="discord",
        wake_chat_id="chat-1",
        wake_chat_type="group",
        wake_actor_id="user-1",
        wake_actor_name="Owner",
    )

    a2a = build_canonical_a2a_from_answers(answers, auth_token="server-token", wake_secret="wake-secret")

    assert a2a["identity"] == {"name": "primary_agent", "description": "Primary profile"}
    assert a2a["server"]["auth_token"] == "server-token"
    assert a2a["wake"]["secret"] == "wake-secret"
    assert a2a["wake"]["session"] == {
        "platform": "discord",
        "chat_id": "chat-1",
        "chat_type": "group",
        "actor": {"id": "user-1", "name": "Owner"},
    }


def test_build_canonical_a2a_env_secret_store_uses_refs():
    answers = WizardAnswers(
        identity_name="primary_agent",
        identity_description="Primary profile",
        host="127.0.0.1",
        port=41731,
        public_url="http://127.0.0.1:41731",
        require_auth=True,
        webhook_port=47644,
        secret_store="env",
    )

    a2a = build_canonical_a2a_from_answers(answers, auth_token="server-token", wake_secret="wake-secret")

    assert "auth_token" not in a2a["server"]
    assert a2a["server"]["auth_token_env"] == "A2A_AUTH_TOKEN"
    assert "secret" not in a2a["wake"]
    assert a2a["wake"]["secret_env"] == "A2A_WEBHOOK_SECRET"


def test_collect_wizard_answers_labels_actor_as_session_selector():
    prompts = {
        "A2A agent name": "",
        "A2A agent description": "",
        "A2A bind host": "",
        "A2A server port": "",
        "A2A public URL": "",
        "Webhook wake port": "",
        "Secret store (config/env)": "",
        "Wake platform (discord/telegram/custom/none)": "discord",
        "Wake chat/channel ID": "chat-1",
        "Wake chat type": "",
        "Wake actor ID (session selector, not allowlist)": "user-1",
        "Wake actor name": "Owner",
    }
    seen_questions = []

    def prompt(question, default):
        seen_questions.append(question)
        return prompts.get(question, default)

    def confirm(question, default):
        return default

    answers = collect_wizard_answers(
        profile_name="default",
        default_port=41731,
        default_webhook_port=47644,
        prompt_fn=prompt,
        confirm_fn=confirm,
    )

    assert answers.identity_name == "primary_agent"
    assert answers.wake_actor_id == "user-1"
    assert "Wake actor ID (session selector, not allowlist)" in seen_questions
