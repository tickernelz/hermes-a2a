import json

import yaml

from test_a2a_cli import run_cli, write_profile


def test_cli_migrate_config_unify_dry_run_outputs_redacted_preview(tmp_path):
    home = tmp_path / ".hermes"
    write_profile(home, {"a2a": {"server": {}}}, "A2A_AUTH_TOKEN=server-token\n")

    result = run_cli(["migrate", "config-unify", "--hermes-home", str(home), "--dry-run", "--json"], home=tmp_path)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["changed"] is False
    assert "server-token" not in payload["redacted_config_preview"]
    assert "[REDACTED]" in payload["redacted_config_preview"]
    assert "A2A_AUTH_TOKEN=server-token" in (home / ".env").read_text(encoding="utf-8")


def test_cli_migrate_config_unify_writes_canonical_config_and_cleans_a2a_env(tmp_path):
    home = tmp_path / ".hermes"
    write_profile(
        home,
        {
            "webhook": {
                "extra": {
                    "port": 47644,
                    "routes": {
                        "custom_route": {"secret": "custom-secret", "prompt": "custom"},
                        "a2a_trigger": {
                            "secret": "wake-secret",
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
                }
            },
            "a2a": {"server": {"port": 41731}},
        },
        "A2A_AGENT_NAME=primary_agent\nA2A_AUTH_TOKEN=server-token\nOPENROUTER_API_KEY=provider-key\n",
    )

    result = run_cli(["migrate", "config-unify", "--hermes-home", str(home), "--yes", "--json"], home=tmp_path)

    assert result.returncode == 0, result.stderr
    cfg = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["a2a"]["identity"]["name"] == "primary_agent"
    assert cfg["a2a"]["server"]["auth_token"] == "server-token"
    assert cfg["a2a"]["wake"]["secret"] == "wake-secret"
    assert cfg["a2a"]["wake"]["session"]["actor"] == {"id": "user-1", "name": "Owner"}
    assert cfg["webhook"]["extra"]["routes"]["custom_route"]["prompt"] == "custom"
    env_text = (home / ".env").read_text(encoding="utf-8")
    assert "OPENROUTER_API_KEY=provider-key" in env_text
    assert "A2A_AUTH_TOKEN=server-token" not in env_text
