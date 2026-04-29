import os
import shutil
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
INSTALL = ROOT / "install.sh"
UNINSTALL = ROOT / "uninstall.sh"


def run_install(home: Path, *args: str, **env_overrides):
    env = os.environ.copy()
    env.update({
        "HERMES_HOME": str(home),
        "A2A_PORT": "18081",
        "A2A_PUBLIC_URL": "http://127.0.0.1:18081",
        "A2A_AGENT_NAME": "jono",
        "A2A_AGENT_DESCRIPTION": "Jono test profile",
        "A2A_REMOTE_NAME": "yanto_coder",
        "A2A_REMOTE_URL": "http://127.0.0.1:18082",
        "A2A_REMOTE_DESCRIPTION": "Yanto Coder test profile",
        "A2A_REMOTE_TOKEN_ENV": "A2A_AGENT_YANTO_TOKEN",
        "A2A_HOME_PLATFORM": "discord",
        "A2A_HOME_CHAT_TYPE": "group",
        "A2A_HOME_CHAT_ID": "1499028849261023322",
        "A2A_HOME_USER_ID": "287600440659410944",
        "A2A_HOME_USER_NAME": "Zhafron",
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


def run_uninstall(home: Path, *args: str):
    env = os.environ.copy()
    env["HERMES_HOME"] = str(home)
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


def test_install_requires_explicit_hermes_home(tmp_path):
    env = os.environ.copy()
    env.pop("HERMES_HOME", None)

    result = subprocess.run(
        ["bash", str(INSTALL), "--dry-run"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "HERMES_HOME" in result.stderr


def test_uninstall_requires_explicit_hermes_home(tmp_path):
    env = os.environ.copy()
    env.pop("HERMES_HOME", None)

    result = subprocess.run(
        ["bash", str(UNINSTALL), "--dry-run"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "HERMES_HOME" in result.stderr


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
    home = tmp_path / "jono"
    sibling = tmp_path / "akbar_hmx"
    home.mkdir()
    sibling.mkdir()
    (sibling / "config.yaml").write_text("akbar: untouched\n", encoding="utf-8")
    (sibling / ".env").write_text("AKBAR=untouched\n", encoding="utf-8")
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

    first = run_install(home)
    second = run_install(home)

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
    assert route["secret"] == cfg["platforms"]["webhook"]["extra"]["routes"]["a2a_trigger"]["secret"]
    assert route["prompt"] == "[A2A trigger]"
    assert route["deliver"] == "discord"
    assert route["deliver_extra"] == {"chat_id": "1499028849261023322"}
    assert route["source"] == {
        "platform": "discord",
        "chat_type": "group",
        "chat_id": "1499028849261023322",
        "user_id": "287600440659410944",
        "user_name": "Zhafron",
    }
    assert cfg["a2a"]["enabled"] is True
    assert cfg["a2a"]["server"]["port"] == 18081
    assert cfg["a2a"]["server"]["require_auth"] is True
    assert cfg["a2a"]["agents"] == [{
        "name": "yanto_coder",
        "url": "http://127.0.0.1:18082",
        "description": "Yanto Coder test profile",
        "auth_token_env": "A2A_AGENT_YANTO_TOKEN",
        "enabled": True,
        "tags": ["local"],
        "trust_level": "trusted",
    }]

    env_text = (home / ".env").read_text(encoding="utf-8")
    for key in [
        "A2A_ENABLED",
        "A2A_AUTH_TOKEN",
        "A2A_REQUIRE_AUTH",
        "A2A_WEBHOOK_SECRET",
        "A2A_PUBLIC_URL",
        "A2A_AGENT_YANTO_TOKEN",
        "WEBHOOK_ENABLED",
    ]:
        assert env_text.count(f"{key}=") == 1
    assert "EXISTING=1" in env_text
    assert (sibling / "config.yaml").read_text(encoding="utf-8") == "akbar: untouched\n"
    assert (sibling / ".env").read_text(encoding="utf-8") == "AKBAR=untouched\n"


def test_uninstall_is_profile_safe_and_supports_dry_run(tmp_path):
    home = tmp_path / "profile"
    other = tmp_path / "other"
    plugin = home / "plugins" / "a2a"
    other_plugin = other / "plugins" / "a2a"
    plugin.mkdir(parents=True)
    other_plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text("name: a2a\n", encoding="utf-8")
    (other_plugin / "plugin.yaml").write_text("name: a2a\n", encoding="utf-8")

    dry = run_uninstall(home, "--dry-run")
    assert dry.returncode == 0, dry.stderr
    assert plugin.exists()

    result = run_uninstall(home)

    assert result.returncode == 0, result.stderr
    assert not plugin.exists()
    assert other_plugin.exists()
