import os
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def write_profile(path: Path, config: dict | None = None, env_text: str = "") -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "config.yaml").write_text(yaml.safe_dump(config or {}, sort_keys=False), encoding="utf-8")
    (path / ".env").write_text(env_text, encoding="utf-8")


def run_script(script: str, args: list[str], *, home: Path, extra_env: dict[str, str] | None = None, input_text: str = ""):
    env = os.environ.copy()
    env["HOME"] = str(home)
    for key in list(env):
        if key == "HERMES_HOME" or key.startswith("A2A_") or key == "WEBHOOK_PORT":
            env.pop(key, None)
    env.pop("PROFILE_NAME", None)
    env["HERMES_PYTHON"] = "/home/zhafron/.hermes/hermes-agent/venv/bin/python"
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", str(ROOT / script), *args],
        input=input_text,
        text=True,
        capture_output=True,
        cwd=ROOT,
        env=env,
        timeout=30,
    )


def test_install_fails_closed_for_multiple_profiles_in_non_tty_without_target(tmp_path):
    write_profile(tmp_path / ".hermes")
    write_profile(tmp_path / ".hermes" / "profiles" / "reviewer")

    result = run_script("install.sh", ["--dry-run"], home=tmp_path)

    assert result.returncode != 0
    assert "multiple Hermes profiles" in result.stderr
    assert "--profile" in result.stderr


def test_uninstall_fails_closed_for_multiple_profiles_in_non_tty_without_target(tmp_path):
    write_profile(tmp_path / ".hermes")
    write_profile(tmp_path / ".hermes" / "profiles" / "reviewer")

    result = run_script("uninstall.sh", ["--dry-run"], home=tmp_path)

    assert result.returncode != 0
    assert "multiple Hermes profiles" in result.stderr
    assert "--profile" in result.stderr


def test_install_preserves_existing_env_and_server_config_without_explicit_overrides(tmp_path):
    profile = tmp_path / ".hermes"
    write_profile(
        profile,
        {
            "a2a": {
                "server": {
                    "host": "127.0.0.1",
                    "port": 49999,
                    "public_url": "http://127.0.0.1:49999",
                    "require_auth": True,
                }
            }
        },
        "A2A_PORT=49999\nA2A_PUBLIC_URL=http://127.0.0.1:49999\nA2A_AGENT_NAME=custom-agent\nA2A_REQUIRE_AUTH=false\n",
    )

    result = run_script("install.sh", ["--hermes-home", str(profile), "--yes"], home=tmp_path)

    assert result.returncode == 0, result.stderr + result.stdout
    env_text = (profile / ".env").read_text(encoding="utf-8")
    cfg = yaml.safe_load((profile / "config.yaml").read_text(encoding="utf-8"))
    assert "A2A_PORT=49999" not in env_text
    assert "A2A_PUBLIC_URL=http://127.0.0.1:49999" not in env_text
    assert "A2A_AGENT_NAME=custom-agent" not in env_text
    assert "A2A_REQUIRE_AUTH=false" not in env_text
    assert cfg["a2a"]["identity"]["name"] == "custom-agent"
    assert cfg["a2a"]["server"]["port"] == 49999
    assert cfg["a2a"]["server"].get("public_url") in (None, "http://127.0.0.1:49999")
    assert cfg["a2a"]["server"].get("require_auth") is False


def test_install_explicit_env_overrides_existing_values(tmp_path):
    profile = tmp_path / ".hermes"
    write_profile(
        profile,
        {"a2a": {"server": {"port": 49999, "public_url": "http://127.0.0.1:49999", "require_auth": False}}},
        "A2A_PORT=49999\nA2A_PUBLIC_URL=http://127.0.0.1:49999\nA2A_AGENT_NAME=old-agent\nA2A_REQUIRE_AUTH=false\n",
    )

    result = run_script(
        "install.sh",
        ["--hermes-home", str(profile), "--yes"],
        home=tmp_path,
        extra_env={
            "HERMES_A2A_INSTALL_ENV_OVERRIDES": "A2A_PORT,A2A_PUBLIC_URL,A2A_AGENT_NAME,A2A_REQUIRE_AUTH",
            "A2A_PORT": "45555",
            "A2A_PUBLIC_URL": "http://127.0.0.1:45555",
            "A2A_AGENT_NAME": "new-agent",
            "A2A_REQUIRE_AUTH": "true",
        },
    )

    assert result.returncode == 0, result.stderr + result.stdout
    env_text = (profile / ".env").read_text(encoding="utf-8")
    cfg = yaml.safe_load((profile / "config.yaml").read_text(encoding="utf-8"))
    assert "A2A_PORT=45555" not in env_text
    assert "A2A_PUBLIC_URL=http://127.0.0.1:45555" not in env_text
    assert "A2A_AGENT_NAME=new-agent" not in env_text
    assert "A2A_REQUIRE_AUTH=true" not in env_text
    assert cfg["a2a"]["identity"]["name"] == "new-agent"
    assert cfg["a2a"]["server"]["port"] == 45555
    assert cfg["a2a"]["server"].get("public_url") in (None, "http://127.0.0.1:45555")
    assert cfg["a2a"]["server"].get("require_auth") in (None, True)


def test_install_creates_dashboard_webhook_route(tmp_path):
    profile = tmp_path / ".hermes"
    write_profile(profile)

    result = run_script("install.sh", ["--hermes-home", str(profile), "--yes"], home=tmp_path)

    assert result.returncode == 0, result.stderr + result.stdout
    cfg = yaml.safe_load((profile / "config.yaml").read_text(encoding="utf-8"))
    routes = cfg["platforms"]["webhook"]["extra"]["routes"]
    assert "a2a_trigger" in routes
    assert "a2a_dashboard" in routes
    assert routes["a2a_dashboard"]["secret"] == routes["a2a_trigger"]["secret"]
