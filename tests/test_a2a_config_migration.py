import yaml

from hermes_a2a_cli.migrations.v0_2_2_to_v0_3_0_config_unify import migrate_config_unify


def test_config_unify_migrates_legacy_env_and_routes_to_canonical_config(tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "plugins": {"enabled": ["a2a"]},
                "webhook": {
                    "enabled": True,
                    "extra": {
                        "port": 47644,
                        "secret": "legacy-wake-secret",
                        "routes": {
                            "custom_route": {"secret": "custom-secret", "prompt": "custom"},
                            "a2a_trigger": {
                                "secret": "legacy-wake-secret",
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
                            },
                        },
                    },
                },
                "platforms": {
                    "webhook": {
                        "extra": {
                            "routes": {
                                "custom_platform_route": {"secret": "platform-secret", "prompt": "platform"}
                            }
                        }
                    }
                },
                "a2a": {
                    "enabled": True,
                    "server": {"port": 41731},
                    "agents": [
                        {
                            "name": "secondary_agent",
                            "url": "http://127.0.0.1:41732",
                            "description": "Secondary local agent",
                            "auth_token_env": "A2A_AGENT_SECONDARY_TOKEN",
                            "enabled": True,
                        }
                    ],
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (home / ".env").write_text(
        "A2A_AGENT_NAME=primary_agent\n"
        "A2A_AGENT_DESCRIPTION=Primary profile\n"
        "A2A_HOST=127.0.0.1\n"
        "A2A_PORT=41731\n"
        "A2A_PUBLIC_URL=http://127.0.0.1:41731\n"
        "A2A_REQUIRE_AUTH=true\n"
        "A2A_AUTH_TOKEN=server-token\n"
        "A2A_WEBHOOK_SECRET=wake-secret\n"
        "WEBHOOK_ENABLED=true\n"
        "WEBHOOK_PORT=47644\n"
        "A2A_AGENT_SECONDARY_TOKEN=remote-token\n"
        "OPENROUTER_API_KEY=provider-key\n",
        encoding="utf-8",
    )

    result = migrate_config_unify(home, dry_run=False)

    assert result["changed"] is True
    cfg = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["a2a"]["identity"] == {"name": "primary_agent", "description": "Primary profile"}
    assert cfg["a2a"]["server"] == {
        "host": "127.0.0.1",
        "port": 41731,
        "public_url": "http://127.0.0.1:41731",
        "require_auth": True,
        "auth_token": "server-token",
    }
    assert cfg["a2a"]["wake"]["secret"] == "wake-secret"
    assert cfg["a2a"]["wake"]["session"] == {
        "platform": "discord",
        "chat_id": "chat-1",
        "chat_type": "group",
        "actor": {"id": "user-1", "name": "Owner"},
    }
    assert cfg["a2a"]["agents"][0]["auth_token"] == "remote-token"
    assert "auth_token_env" not in cfg["a2a"]["agents"][0]
    assert cfg["webhook"]["extra"]["routes"]["a2a_trigger"]["source"]["user_id"] == "user-1"
    assert cfg["webhook"]["extra"]["routes"]["custom_route"]["prompt"] == "custom"
    assert cfg["platforms"]["webhook"]["extra"]["routes"]["custom_platform_route"]["prompt"] == "platform"
    env_text = (home / ".env").read_text(encoding="utf-8")
    assert "OPENROUTER_API_KEY=provider-key" in env_text
    assert "A2A_AUTH_TOKEN=server-token" not in env_text
    assert "A2A_AGENT_SECONDARY_TOKEN=remote-token" not in env_text


def test_config_unify_dry_run_redacts_secrets_and_does_not_mutate(tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "config.yaml").write_text("a2a:\n  server: {}\n", encoding="utf-8")
    (home / ".env").write_text("A2A_AUTH_TOKEN=server-token\n", encoding="utf-8")

    result = migrate_config_unify(home, dry_run=True)

    assert result["changed"] is False
    assert "server-token" not in result["redacted_config_preview"]
    assert "[REDACTED]" in result["redacted_config_preview"]
    assert "A2A_AUTH_TOKEN=server-token" in (home / ".env").read_text(encoding="utf-8")
