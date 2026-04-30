import os

from plugin.config import get_identity_config, get_server_config, get_wake_config, load_agents


def test_canonical_identity_server_wake_and_agent_tokens(monkeypatch):
    monkeypatch.delenv("A2A_AGENT_NAME", raising=False)
    monkeypatch.delenv("A2A_AGENT_DESCRIPTION", raising=False)
    monkeypatch.delenv("A2A_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("A2A_WEBHOOK_SECRET", raising=False)
    config = {
        "a2a": {
            "identity": {"name": "primary_agent", "description": "Primary profile"},
            "server": {
                "port": 41731,
                "auth_token": "server-token",
            },
            "wake": {
                "port": 47644,
                "secret": "wake-secret",
                "session_ref": {
                    "platform": "discord",
                    "chat_id": "1499099497572339904",
                },
            },
            "agents": [
                {
                    "name": "secondary_agent",
                    "url": "http://127.0.0.1:41732",
                    "auth_token": "remote-token",
                    "enabled": True,
                }
            ],
        }
    }

    identity = get_identity_config(config)
    server = get_server_config(config)
    wake = get_wake_config(config)
    agents = load_agents(config)

    assert identity.name == "primary_agent"
    assert identity.description == "Primary profile"
    assert server.auth_token == "server-token"
    assert wake.port == 47644
    assert wake.secret == "wake-secret"
    assert wake.session == {"platform": "discord", "chat_id": "1499099497572339904"}
    assert server.public_url == "http://127.0.0.1:41731"
    assert server.require_auth is True
    assert agents[0]["auth_token"] == "remote-token"


def test_runtime_ignores_legacy_a2a_environment_after_migration(monkeypatch):
    monkeypatch.setenv("A2A_AGENT_NAME", "legacy-name")
    monkeypatch.setenv("A2A_AGENT_DESCRIPTION", "legacy description")
    monkeypatch.setenv("A2A_AUTH_TOKEN", "legacy-server-token")
    monkeypatch.setenv("A2A_WEBHOOK_SECRET", "legacy-wake-secret")
    config = {"a2a": {}}

    assert get_identity_config(config).name == "hermes-agent"
    assert get_server_config(config).auth_token == ""
    assert get_wake_config(config).secret == ""


def test_env_refs_are_supported_for_advanced_secret_store(monkeypatch):
    monkeypatch.setenv("SERVER_TOKEN_REF", "server-token")
    monkeypatch.setenv("WAKE_SECRET_REF", "wake-secret")
    monkeypatch.setenv("REMOTE_TOKEN_REF", "remote-token")
    config = {
        "a2a": {
            "server": {"auth_token_env": "SERVER_TOKEN_REF"},
            "wake": {"secret_env": "WAKE_SECRET_REF"},
            "agents": [
                {
                    "name": "remote",
                    "url": "http://127.0.0.1:41732",
                    "auth_token_env": "REMOTE_TOKEN_REF",
                }
            ],
        }
    }

    assert get_server_config(config).auth_token == "server-token"
    assert get_wake_config(config).secret == "wake-secret"
    assert load_agents(config)[0]["auth_token"] == "remote-token"


def test_wake_config_requires_canonical_session_not_legacy_route(monkeypatch):
    monkeypatch.delenv("A2A_WEBHOOK_SECRET", raising=False)
    config = {
        "webhook": {
            "extra": {
                "port": 47644,
                "secret": "legacy-secret",
                "routes": {
                    "a2a_trigger": {
                        "secret": "route-secret",
                        "prompt": "[A2A trigger]",
                        "deliver": "discord",
                        "deliver_extra": {"chat_id": "chat-1"},
                        "source": {
                            "platform": "discord",
                            "chat_type": "group",
                            "chat_id": "chat-1",
                            "user_id": "user-1",
                            "user_name": "Owner",
                        },
                    }
                },
            }
        }
    }

    wake = get_wake_config(config)

    assert wake.port == 47644
    assert wake.secret == ""
    assert wake.session == {}
