import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from hermes_a2a_cli.installer import choose_a2a_port, install_profile
from hermes_a2a_cli.wizard import WizardAnswers

ROOT = Path(__file__).resolve().parent.parent
INSTALL = ROOT / "install.sh"
UNINSTALL = ROOT / "uninstall.sh"


def run_install(home: Path | None, *args: str, **env_overrides):
    env = os.environ.copy()
    if home is None:
        env.pop("HERMES_HOME", None)
    else:
        env["HERMES_HOME"] = str(home)
    if "HOME" in env_overrides and "HERMES_A2A_ROOT_HOME" not in env_overrides:
        env["HERMES_A2A_ROOT_HOME"] = str(Path(env_overrides["HOME"]) / ".hermes")
    env.update({
        "HERMES_PYTHON": sys.executable,
        "A2A_PORT": "18081",
        "A2A_PUBLIC_URL": "http://127.0.0.1:18081",
        "A2A_AGENT_NAME": "primary_agent",
        "A2A_AGENT_DESCRIPTION": "Primary test profile",
        "A2A_REMOTE_NAME": "reviewer_agent",
        "A2A_REMOTE_URL": "http://127.0.0.1:18082",
        "A2A_REMOTE_DESCRIPTION": "Reviewer test profile",
        "A2A_REMOTE_TOKEN_ENV": "A2A_AGENT_REVIEWER_TOKEN",
        "A2A_HOME_PLATFORM": "discord",
        "A2A_HOME_CHAT_TYPE": "group",
        "A2A_HOME_CHAT_ID": "123456789012345678",
        "A2A_HOME_USER_ID": "234567890123456789",
        "A2A_HOME_USER_NAME": "Example User",
        "WEBHOOK_PORT": "19044",
    })
    env.update(env_overrides)
    return subprocess.run(
        ["bash", str(INSTALL), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )


def run_uninstall(home: Path | None, *args: str, **env_overrides):
    env = os.environ.copy()
    if home is None:
        env.pop("HERMES_HOME", None)
    else:
        env["HERMES_HOME"] = str(home)
    if "HOME" in env_overrides and "HERMES_A2A_ROOT_HOME" not in env_overrides:
        env["HERMES_A2A_ROOT_HOME"] = str(Path(env_overrides["HOME"]) / ".hermes")
    env.update(env_overrides)
    return subprocess.run(
        ["bash", str(UNINSTALL), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )


def read_config(home: Path):
    return yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8")) or {}


def test_choose_a2a_port_skips_unavailable_port(monkeypatch, tmp_path):
    occupied = {41731}

    def available(port):
        return port not in occupied

    monkeypatch.setattr("hermes_a2a_cli.installer.is_local_port_available", available)

    assert choose_a2a_port(tmp_path / ".hermes" / "profiles" / "reviewer", {}, []) == 41732


def test_install_profile_writes_selected_local_agent_and_reciprocal_link(tmp_path):
    default_home = tmp_path / ".hermes"
    yanto_home = default_home / "profiles" / "hermes_yanto_coder"
    default_home.mkdir(parents=True)
    yanto_home.mkdir(parents=True)
    (default_home / "config.yaml").write_text(
        """
plugins:
  enabled:
    - a2a
a2a:
  enabled: true
  identity:
    name: jono
    description: Jono profile
  server:
    host: 127.0.0.1
    port: 41731
    public_url: http://127.0.0.1:41731
    require_auth: true
    auth_token: jono-token
""".lstrip(),
        encoding="utf-8",
    )
    (yanto_home / "config.yaml").write_text("plugins: {}\n", encoding="utf-8")
    answers = WizardAnswers(
        identity_name="yanto_coder",
        identity_description="Yanto profile",
        host="127.0.0.1",
        port=41732,
        public_url="http://127.0.0.1:41732",
        require_auth=True,
        webhook_port=47645,
        wake_enabled=False,
        remote_agents=[
            {
                "name": "jono",
                "url": "http://127.0.0.1:41731",
                "description": "Jono profile",
                "auth_token": "jono-token",
                "reciprocal": True,
                "reciprocal_home": str(default_home),
            }
        ],
    )

    result = install_profile(yanto_home, ROOT / "plugin", ROOT / "dashboard", answers=answers)

    assert result["changed"] is True
    yanto_cfg = read_config(yanto_home)
    assert yanto_cfg["a2a"]["agents"] == [
        {
            "name": "jono",
            "url": "http://127.0.0.1:41731",
            "description": "Jono profile",
            "enabled": True,
            "tags": ["local"],
            "trust_level": "trusted",
            "auth_token": "jono-token",
        }
    ]
    default_cfg = read_config(default_home)
    assert default_cfg["a2a"]["agents"] == [
        {
            "name": "yanto_coder",
            "url": "http://127.0.0.1:41732",
            "description": "Yanto profile",
            "enabled": True,
            "tags": ["local"],
            "trust_level": "trusted",
            "auth_token": yanto_cfg["a2a"]["server"]["auth_token"],
        }
    ]


def test_install_profile_preserves_existing_session_ref_and_regenerates_compat_route(tmp_path):
    home = tmp_path / "profile"
    home.mkdir()
    (home / "config.yaml").write_text(
        """
plugins: {}
a2a:
  wake:
    port: 47644
    secret: sec
    session_ref:
      platform: discord
      chat_id: chat-1
webhook:
  extra:
    routes:
      a2a_trigger:
        secret: sec
        prompt: "[A2A trigger]"
        deliver: discord
        deliver_extra:
          chat_id: chat-1
        source:
          platform: discord
          chat_type: group
          chat_id: chat-1
          user_id: user-1
          user_name: Owner
""".lstrip(),
        encoding="utf-8",
    )

    install_profile(home, ROOT / "plugin", ROOT / "dashboard")

    cfg = read_config(home)
    assert cfg["a2a"]["wake"]["session_ref"] == {"platform": "discord", "chat_id": "chat-1"}
    assert "session" not in cfg["a2a"]["wake"]
    route = cfg["webhook"]["extra"]["routes"]["a2a_trigger"]
    assert route["deliver"] == "discord"
    assert route["source"]["user_id"] == "user-1"
    assert route["source"]["user_name"] == "Owner"


def test_install_profile_migrates_existing_legacy_session_to_session_ref(tmp_path):
    home = tmp_path / "profile"
    home.mkdir()
    (home / "config.yaml").write_text(
        """
plugins: {}
a2a:
  wake:
    port: 47644
    secret: sec
    session:
      platform: discord
      chat_id: chat-1
      chat_type: group
      actor:
        id: user-1
        name: Owner
""".lstrip(),
        encoding="utf-8",
    )

    install_profile(home, ROOT / "plugin", ROOT / "dashboard")

    cfg = read_config(home)
    assert cfg["a2a"]["wake"]["session_ref"] == {"platform": "discord", "chat_id": "chat-1"}
    assert "session" not in cfg["a2a"]["wake"]
    assert cfg["webhook"]["extra"]["routes"]["a2a_trigger"]["source"]["user_id"] == "user-1"


def test_install_profile_wizard_answers_generate_compat_route_without_persisting_actor(tmp_path):
    home = tmp_path / "profile"
    home.mkdir()
    (home / "config.yaml").write_text("plugins: {}\n", encoding="utf-8")
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
        wake_chat_type="thread",
        wake_thread_id="thread-1",
        wake_actor_id="user-1",
        wake_actor_name="Owner",
    )

    install_profile(home, ROOT / "plugin", ROOT / "dashboard", answers=answers)

    cfg = read_config(home)
    assert cfg["a2a"]["wake"]["session_ref"] == {"platform": "discord", "chat_id": "chat-1", "thread_id": "thread-1"}
    assert "session" not in cfg["a2a"]["wake"]
    route = cfg["webhook"]["extra"]["routes"]["a2a_trigger"]
    assert route["deliver"] == "discord"
    assert route["deliver_extra"] == {"chat_id": "chat-1", "thread_id": "thread-1"}
    assert route["source"] == {
        "platform": "discord",
        "chat_type": "thread",
        "chat_id": "chat-1",
        "user_id": "user-1",
        "user_name": "Owner",
        "thread_id": "thread-1",
    }


def test_install_autodetects_single_default_profile_without_hermes_home(tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()
    config_path = home / "config.yaml"
    config_path.write_text("plugins: {}\n", encoding="utf-8")

    result = run_install(None, "--dry-run", HOME=str(tmp_path))

    assert result.returncode == 0, result.stderr
    assert "DRY RUN" in result.stdout
    assert not (home / "plugins" / "a2a").exists()


def test_uninstall_autodetects_single_default_profile_without_hermes_home(tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "config.yaml").write_text("plugins: {}\n", encoding="utf-8")
    plugin = home / "plugins" / "a2a"
    plugin.mkdir(parents=True)

    result = run_uninstall(None, "--dry-run", HOME=str(tmp_path))

    assert result.returncode == 0, result.stderr
    assert str(plugin) in result.stdout
    assert plugin.exists()


def test_install_profile_argument_targets_named_profile(tmp_path):
    home = tmp_path / ".hermes"
    named = home / "profiles" / "coder"
    named.mkdir(parents=True)
    (home).mkdir(exist_ok=True)
    (named / "config.yaml").write_text("plugins: {}\n", encoding="utf-8")

    result = run_install(None, "--profile", "coder", "--dry-run", HOME=str(tmp_path))

    assert result.returncode == 0, result.stderr
    assert "DRY RUN" in result.stdout
    assert str(named) in result.stdout


def test_install_fails_if_hermes_home_missing_config(tmp_path):
    home = tmp_path / "empty-profile"
    home.mkdir()

    result = run_install(home, "--dry-run")

    assert result.returncode != 0
    assert "config.yaml" in result.stderr
    assert not (home / "plugins" / "a2a").exists()


def test_install_dry_run_does_not_mutate_profile(tmp_path):
    home = tmp_path / "profile"
    home.mkdir()
    config_path = home / "config.yaml"
    env_path = home / ".env"
    config_path.write_text("platform_toolsets:\n  discord:\n    - hermes-discord\n", encoding="utf-8")
    env_path.write_text("EXISTING=1\n", encoding="utf-8")

    result = run_install(home, "--dry-run")

    assert result.returncode == 0, result.stderr
    assert config_path.read_text(encoding="utf-8") == "platform_toolsets:\n  discord:\n    - hermes-discord\n"
    assert env_path.read_text(encoding="utf-8") == "EXISTING=1\n"
    assert not (home / "plugins" / "a2a").exists()
    assert "DRY RUN" in result.stdout


def test_install_is_profile_safe_idempotent_and_enables_a2a(tmp_path):
    home = tmp_path / "primary"
    sibling = tmp_path / "sibling_profile"
    home.mkdir()
    sibling.mkdir()
    (sibling / "config.yaml").write_text("sibling: untouched\n", encoding="utf-8")
    (sibling / ".env").write_text("SIBLING=untouched\n", encoding="utf-8")
    (home / "config.yaml").write_text(
        """
plugins:
  enabled:
    - spotify
platform_toolsets:
  discord:
    - hermes-discord
known_plugin_toolsets:
  discord:
    - spotify
webhook:
  extra:
    routes: {}
""".lstrip(),
        encoding="utf-8",
    )
    (home / ".env").write_text("EXISTING=1\n", encoding="utf-8")

    first = run_install(home, "--yes")
    second = run_install(home, "--yes")

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert (home / "plugins" / "a2a" / "plugin.yaml").exists()
    assert (home / "plugins" / "a2a" / "dashboard" / "plugin_api.py").exists()
    assert list(home.glob("config.yaml.bak.*"))
    assert list(home.glob(".env.bak.*"))

    cfg = read_config(home)
    assert cfg["plugins"]["enabled"].count("a2a") == 1
    assert "spotify" in cfg["plugins"]["enabled"]
    assert cfg["platform_toolsets"]["discord"].count("a2a") == 1
    assert "hermes-discord" in cfg["platform_toolsets"]["discord"]
    assert cfg["known_plugin_toolsets"]["discord"].count("a2a") == 1
    route = cfg["webhook"]["extra"]["routes"]["a2a_trigger"]
    assert route["secret"]
    assert cfg["webhook"]["extra"]["port"] == 19044
    assert cfg["platforms"]["webhook"]["enabled"] is True
    assert cfg["platforms"]["webhook"]["extra"]["port"] == 19044
    assert route["secret"] == cfg["platforms"]["webhook"]["extra"]["routes"]["a2a_trigger"]["secret"]
    assert route["prompt"] == "[A2A trigger]"
    assert route["deliver"] == "discord"
    assert route["deliver_extra"] == {"chat_id": "123456789012345678"}
    assert route["source"] == {
        "platform": "discord",
        "chat_type": "group",
        "chat_id": "123456789012345678",
        "user_id": "234567890123456789",
        "user_name": "Example User",
    }
    assert cfg["a2a"]["enabled"] is True
    assert cfg["a2a"]["server"]["port"] == 18081
    assert cfg["a2a"].get("runtime") is None
    assert cfg["a2a"].get("security") is None
    assert cfg["a2a"].get("dashboard") is None
    assert cfg["a2a"]["wake"]["session_ref"] == {"platform": "discord", "chat_id": "123456789012345678"}
    assert "session" not in cfg["a2a"]["wake"]
    assert cfg["a2a"]["server"].get("require_auth") is None
    assert len(cfg["a2a"]["agents"]) == 1
    assert cfg["a2a"]["agents"][0]["name"] == "reviewer_agent"
    assert cfg["a2a"]["agents"][0]["url"] == "http://127.0.0.1:18082"
    assert cfg["a2a"]["agents"][0]["description"] == "Reviewer test profile"
    assert cfg["a2a"]["agents"][0]["auth_token"]
    assert cfg["a2a"]["agents"][0]["enabled"] is True
    assert cfg["a2a"]["agents"][0]["tags"] == ["local"]
    assert cfg["a2a"]["agents"][0]["trust_level"] == "trusted"
    assert "auth_token_env" not in cfg["a2a"]["agents"][0]

    env_text = (home / ".env").read_text(encoding="utf-8")
    for key in [
        "A2A_ENABLED",
        "A2A_AUTH_TOKEN",
        "A2A_REQUIRE_AUTH",
        "A2A_WEBHOOK_SECRET",
        "A2A_PUBLIC_URL",
        "A2A_AGENT_REVIEWER_TOKEN",
    ]:
        assert env_text.count(f"{key}=") == 0
    for key in ["WEBHOOK_ENABLED", "WEBHOOK_PORT"]:
        assert env_text.count(f"{key}=") == 1
    assert "auth_token" in cfg["a2a"]["server"]
    assert cfg["a2a"]["agents"][0]["auth_token"]
    assert "auth_token_env" not in cfg["a2a"]["agents"][0]
    assert "EXISTING=1" in env_text
    assert (sibling / "config.yaml").read_text(encoding="utf-8") == "sibling: untouched\n"
    assert (sibling / ".env").read_text(encoding="utf-8") == "SIBLING=untouched\n"


def test_install_auto_chooses_distinct_a2a_and_webhook_ports_for_named_profile(tmp_path):
    root_home = tmp_path / ".hermes"
    default_home = root_home
    profile_home = root_home / "profiles" / "reviewer"
    default_home.mkdir(parents=True)
    profile_home.mkdir(parents=True)
    (default_home / "config.yaml").write_text("plugins: {}\n", encoding="utf-8")
    (profile_home / "config.yaml").write_text("plugins: {}\n", encoding="utf-8")

    default_result = run_install(default_home, "--yes", WEBHOOK_PORT="", A2A_WEBHOOK_PORT="", A2A_PORT="", A2A_PUBLIC_URL="")
    profile_result = run_install(None, "--profile", "reviewer", "--yes", HOME=str(tmp_path), WEBHOOK_PORT="", A2A_WEBHOOK_PORT="", A2A_PORT="", A2A_PUBLIC_URL="")

    assert default_result.returncode == 0, default_result.stderr
    assert profile_result.returncode == 0, profile_result.stderr
    default_cfg = read_config(default_home)
    profile_cfg = read_config(profile_home)
    default_a2a_port = default_cfg["a2a"]["server"]["port"]
    profile_a2a_port = profile_cfg["a2a"]["server"]["port"]
    default_webhook_port = default_cfg["platforms"]["webhook"]["extra"]["port"]
    profile_webhook_port = profile_cfg["platforms"]["webhook"]["extra"]["port"]
    assert isinstance(default_a2a_port, int)
    assert profile_a2a_port == default_a2a_port + 1
    assert isinstance(default_webhook_port, int)
    assert profile_webhook_port == default_webhook_port + 1
    assert profile_cfg["a2a"]["server"].get("public_url") in (None, f"http://127.0.0.1:{profile_a2a_port}")
    assert default_webhook_port == int(
        next(line.split("=", 1)[1] for line in (default_home / ".env").read_text(encoding="utf-8").splitlines() if line.startswith("WEBHOOK_PORT="))
    )
    assert profile_webhook_port == int(
        next(line.split("=", 1)[1] for line in (profile_home / ".env").read_text(encoding="utf-8").splitlines() if line.startswith("WEBHOOK_PORT="))
    )


def test_install_multi_configures_two_profiles_with_reciprocal_agents(tmp_path):
    root_home = tmp_path / ".hermes"
    default_home = root_home
    yanto_home = root_home / "profiles" / "hermes_yanto_coder"
    default_home.mkdir(parents=True)
    yanto_home.mkdir(parents=True)
    (default_home / "config.yaml").write_text("plugins: {}\n", encoding="utf-8")
    (yanto_home / "config.yaml").write_text("plugins: {}\n", encoding="utf-8")

    result = run_install(
        None,
        "--multi",
        "--profile",
        "default,hermes_yanto_coder",
        "--yes",
        HOME=str(tmp_path),
        WEBHOOK_PORT="",
        A2A_WEBHOOK_PORT="",
        A2A_PORT="",
        A2A_PUBLIC_URL="",
        A2A_REMOTE_NAME="",
        A2A_REMOTE_URL="",
        A2A_REMOTE_DESCRIPTION="",
        A2A_REMOTE_TOKEN_ENV="",
    )

    assert result.returncode == 0, result.stderr
    default_cfg = read_config(default_home)
    yanto_cfg = read_config(yanto_home)
    assert default_cfg["a2a"]["identity"]["name"] == "jono"
    assert yanto_cfg["a2a"]["identity"]["name"] == "yanto_coder"
    default_port = default_cfg["a2a"]["server"]["port"]
    yanto_port = yanto_cfg["a2a"]["server"]["port"]
    default_wake_port = default_cfg["platforms"]["webhook"]["extra"]["port"]
    yanto_wake_port = yanto_cfg["platforms"]["webhook"]["extra"]["port"]
    assert yanto_port == default_port + 1
    assert yanto_wake_port == default_wake_port + 1
    assert default_cfg["a2a"]["agents"] == [
        {
            "name": "yanto_coder",
            "url": f"http://127.0.0.1:{yanto_port}",
            "description": "yanto_coder Hermes profile",
            "enabled": True,
            "tags": ["local"],
            "trust_level": "trusted",
            "auth_token": yanto_cfg["a2a"]["server"]["auth_token"],
        }
    ]
    assert yanto_cfg["a2a"]["agents"] == [
        {
            "name": "jono",
            "url": f"http://127.0.0.1:{default_port}",
            "description": "jono Hermes profile",
            "enabled": True,
            "tags": ["local"],
            "trust_level": "trusted",
            "auth_token": default_cfg["a2a"]["server"]["auth_token"],
        }
    ]
    assert "A2A_AUTH_TOKEN=" not in (default_home / ".env").read_text(encoding="utf-8")
    assert "A2A_AUTH_TOKEN=" not in (yanto_home / ".env").read_text(encoding="utf-8")


def test_install_preserves_existing_webhook_port_when_env_not_set(tmp_path):
    home = tmp_path / "profile"
    home.mkdir()
    (home / "config.yaml").write_text("""
platforms:
  webhook:
    extra:
      port: 19191
""".lstrip(), encoding="utf-8")

    result = run_install(home, "--yes", WEBHOOK_PORT="", A2A_WEBHOOK_PORT="")

    assert result.returncode == 0, result.stderr
    cfg = read_config(home)
    assert cfg["webhook"]["extra"]["port"] == 19191
    assert cfg["platforms"]["webhook"]["extra"]["port"] == 19191
    assert "WEBHOOK_PORT=19191" in (home / ".env").read_text(encoding="utf-8")


def test_uninstall_is_profile_safe_and_supports_dry_run(tmp_path):
    home = tmp_path / "profile"
    other = tmp_path / "other"
    home.mkdir()
    other.mkdir()
    (home / "config.yaml").write_text("plugins: {}\n", encoding="utf-8")
    (other / "config.yaml").write_text("plugins: {}\n", encoding="utf-8")
    plugin = home / "plugins" / "a2a"
    other_plugin = other / "plugins" / "a2a"
    plugin.mkdir(parents=True)
    other_plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text("name: a2a\n", encoding="utf-8")
    (other_plugin / "plugin.yaml").write_text("name: a2a\n", encoding="utf-8")

    dry = run_uninstall(home, "--dry-run")
    assert dry.returncode == 0, dry.stderr
    assert plugin.exists()

    result = run_uninstall(home, "--yes")

    assert result.returncode == 0, result.stderr
    assert not plugin.exists()
    assert other_plugin.exists()


def test_uninstall_refuses_profile_without_config(tmp_path):
    home = tmp_path / "profile"
    plugin = home / "plugins" / "a2a"
    plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text("name: a2a\n", encoding="utf-8")

    result = run_uninstall(home, "--dry-run")

    assert result.returncode != 0
    assert "config.yaml" in result.stderr
    assert plugin.exists()
