from pathlib import Path

import yaml

from hermes_a2a_cli.multi_install import (
    HermesProfile,
    build_generated_profiles,
    build_full_mesh,
    derive_agent_name,
    infer_wake_session_from_history,
    preview_generated_profiles,
)


def write_config(home: Path, text: str) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(text.lstrip(), encoding="utf-8")


def test_derive_agent_name_uses_profile_semantics():
    assert derive_agent_name("default") == "jono"
    assert derive_agent_name("hermes_yanto_coder") == "yanto_coder"
    assert derive_agent_name("review-agent") == "review_agent"


def test_infer_wake_session_from_history_uses_latest_platform_session_origin(tmp_path):
    home = tmp_path / ".hermes"
    sessions = home / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "sessions.json").write_text(
        """
{
  "old": {
    "updated_at": "2026-04-29T00:00:00",
    "origin": {
      "platform": "discord",
      "chat_id": "old-channel",
      "chat_type": "thread",
      "user_id": "old-user",
      "user_name": "Old",
      "thread_id": "old-thread"
    }
  },
  "new": {
    "updated_at": "2026-04-30T00:00:00",
    "origin": {
      "platform": "discord",
      "chat_id": "thread-1",
      "chat_name": "Server / #jono / A2A",
      "chat_type": "thread",
      "user_id": "user-1",
      "user_name": "Zhafron",
      "thread_id": "thread-1",
      "guild_id": "guild-1",
      "parent_chat_id": "channel-1"
    }
  }
}
""".strip(),
        encoding="utf-8",
    )

    inferred = infer_wake_session_from_history(home)

    assert inferred == {
        "platform": "discord",
        "chat_id": "thread-1",
        "chat_type": "thread",
        "thread_id": "thread-1",
        "actor_id": "user-1",
        "actor_name": "Zhafron",
    }


def test_infer_wake_session_from_history_returns_empty_without_actor_id(tmp_path):
    home = tmp_path / ".hermes"
    sessions = home / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "sessions.json").write_text(
        '{"bad": {"updated_at": "2026-04-30", "origin": {"platform": "discord", "chat_id": "channel"}}}',
        encoding="utf-8",
    )

    assert infer_wake_session_from_history(home) == {}


def test_build_generated_profiles_allocates_ports_and_full_meshes_selected_profiles(tmp_path, monkeypatch):
    root = tmp_path / ".hermes"
    default_home = root
    yanto_home = root / "profiles" / "hermes_yanto_coder"
    write_config(default_home, "plugins: {}\n")
    write_config(yanto_home, "plugins: {}\n")
    monkeypatch.setattr("hermes_a2a_cli.installer.is_local_port_available", lambda port: True)

    generated = build_generated_profiles(
        [
            HermesProfile("default", default_home.resolve(), yaml.safe_load((default_home / "config.yaml").read_text())),
            HermesProfile("hermes_yanto_coder", yanto_home.resolve(), yaml.safe_load((yanto_home / "config.yaml").read_text())),
        ],
        wake_defaults={
            "platform": "discord",
            "chat_id": "channel-1",
            "chat_type": "group",
            "actor_id": "user-1",
            "actor_name": "Zhafron",
        },
    )

    assert [profile.answers.identity_name for profile in generated] == ["jono", "yanto_coder"]
    assert [profile.answers.port for profile in generated] == [41731, 41732]
    assert [profile.answers.webhook_port for profile in generated] == [47644, 47645]
    assert generated[0].answers.remote_agents[0]["name"] == "yanto_coder"
    assert generated[0].answers.remote_agents[0]["url"] == "http://127.0.0.1:41732"
    assert generated[0].answers.remote_agents[0]["auth_token"] == generated[1].answers.auth_token
    assert generated[1].answers.remote_agents[0]["name"] == "jono"
    assert generated[1].answers.remote_agents[0]["url"] == "http://127.0.0.1:41731"
    assert generated[0].answers.wake_chat_id == "channel-1"
    assert generated[0].answers.wake_actor_id == "user-1"
    assert generated[1].answers.wake_chat_id == "channel-1"


def test_build_generated_profiles_resolves_duplicate_existing_ports(tmp_path, monkeypatch):
    root = tmp_path / ".hermes"
    default_home = root
    yanto_home = root / "profiles" / "hermes_yanto_coder"
    existing = """
plugins: {}
a2a:
  server:
    port: 41731
    public_url: http://127.0.0.1:41731
  wake:
    port: 47644
"""
    write_config(default_home, existing)
    write_config(yanto_home, existing)
    monkeypatch.setattr("hermes_a2a_cli.installer.is_local_port_available", lambda port: True)

    generated = build_generated_profiles(
        [
            HermesProfile("default", default_home.resolve(), yaml.safe_load((default_home / "config.yaml").read_text())),
            HermesProfile("hermes_yanto_coder", yanto_home.resolve(), yaml.safe_load((yanto_home / "config.yaml").read_text())),
        ]
    )

    assert [profile.answers.port for profile in generated] == [41731, 41732]
    assert [profile.answers.webhook_port for profile in generated] == [47644, 47645]
    assert [profile.answers.public_url for profile in generated] == ["http://127.0.0.1:41731", "http://127.0.0.1:41732"]


def test_build_full_mesh_does_not_include_self():
    left = HermesProfile("default", Path("/tmp/default"))
    right = HermesProfile("coder", Path("/tmp/coder"))
    generated = build_generated_profiles(
        [left, right],
        a2a_ports={str(left.home): 41731, str(right.home): 41732},
        webhook_ports={str(left.home): 47644, str(right.home): 47645},
    )

    mesh = build_full_mesh(generated)

    assert [agent["name"] for agent in mesh[str(left.home)]] == ["coder"]
    assert [agent["name"] for agent in mesh[str(right.home)]] == ["jono"]


def test_preview_generated_profiles_explains_ports_wake_and_connections():
    left = HermesProfile("default", Path("/tmp/default"))
    right = HermesProfile("coder", Path("/tmp/coder"))
    generated = build_generated_profiles(
        [left, right],
        a2a_ports={str(left.home): 41731, str(right.home): 41732},
        webhook_ports={str(left.home): 47644, str(right.home): 47645},
        wake_defaults={"platform": "discord", "chat_id": "channel", "actor_id": "user", "actor_name": "Zhafron"},
    )

    lines = preview_generated_profiles(generated)

    assert lines == [
        "default: agent=jono a2a=http://127.0.0.1:41731 wake_port=47644 wake_session=discord:channel connects_to=coder",
        "coder: agent=coder a2a=http://127.0.0.1:41732 wake_port=47645 wake_session=discord:channel connects_to=jono",
    ]
