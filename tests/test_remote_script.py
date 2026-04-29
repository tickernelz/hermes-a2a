import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REMOTE = ROOT / "scripts" / "a2a.sh"
INSTALL = ROOT / "install.sh"
UNINSTALL = ROOT / "uninstall.sh"


def run_remote(home: Path | None, *args: str, **env_overrides):
    env = os.environ.copy()
    if home is None:
        env.pop("HERMES_HOME", None)
    else:
        env["HERMES_HOME"] = str(home)
    env.update(env_overrides)
    return subprocess.run(
        ["bash", str(REMOTE), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )


def test_remote_requires_command(tmp_path):
    home = tmp_path / "profile"
    home.mkdir()

    result = run_remote(home)

    assert result.returncode != 0
    assert "Usage:" in result.stderr


def test_remote_rejects_unknown_command(tmp_path):
    home = tmp_path / "profile"
    home.mkdir()

    result = run_remote(home, "explode")

    assert result.returncode != 0
    assert "Unknown command" in result.stderr


def test_remote_help_does_not_require_hermes_home():
    result = run_remote(None, "--help")

    assert result.returncode == 0
    assert "install" in result.stdout
    assert "update" in result.stdout
    assert "uninstall" in result.stdout


def test_remote_has_no_redundant_command_wrappers():
    assert REMOTE.exists()
    assert not (ROOT / "scripts" / "install.sh").exists()
    assert not (ROOT / "scripts" / "update.sh").exists()
    assert not (ROOT / "scripts" / "uninstall.sh").exists()
    assert INSTALL.exists()
    assert UNINSTALL.exists()
