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


def test_remote_default_bootstraps_persistent_cli_only(tmp_path):
    result = run_remote(
        None,
        HOME=str(tmp_path),
        HERMES_A2A_SOURCE_DIR=str(ROOT),
        HERMES_A2A_REF="v-test",
    )

    assert result.returncode == 0, result.stderr
    bin_path = tmp_path / ".local" / "bin" / "hermes_a2a"
    current = tmp_path / ".local" / "share" / "hermes-a2a" / "current"
    assert bin_path.exists()
    assert os.access(bin_path, os.X_OK)
    assert current.exists()
    assert "Installed hermes_a2a" in result.stdout
    assert "hermes_a2a install" in result.stdout
    assert not (tmp_path / ".hermes" / "plugins" / "a2a").exists()

    wrapper = bin_path.read_text(encoding="utf-8")
    assert "-m hermes_a2a_cli" in wrapper
    assert str(current) in wrapper


def test_remote_wrapper_quotes_persistent_paths(tmp_path):
    malicious_root = tmp_path / "install'\"$(touch SHOULD_NOT_EXIST)"
    result = run_remote(
        None,
        HOME=str(tmp_path),
        HERMES_A2A_INSTALL_DIR=str(malicious_root),
        HERMES_A2A_SOURCE_DIR=str(ROOT),
        HERMES_A2A_REF="v-test",
    )

    assert result.returncode == 0, result.stderr
    wrapper = tmp_path / ".local" / "bin" / "hermes_a2a"
    assert wrapper.exists()
    bash_check = subprocess.run(["bash", "-n", str(wrapper)], text=True, capture_output=True, timeout=30)
    assert bash_check.returncode == 0, bash_check.stderr
    run = subprocess.run([str(wrapper), "--version"], env={"HOME": str(tmp_path)}, text=True, capture_output=True, timeout=30)
    assert run.returncode == 0, run.stderr
    assert not (tmp_path / "SHOULD_NOT_EXIST").exists()


def test_remote_delegates_explicit_command_after_bootstrap(tmp_path):
    profile = tmp_path / ".hermes"
    profile.mkdir(parents=True)
    (profile / "config.yaml").write_text("plugins: {}\n", encoding="utf-8")

    result = run_remote(
        None,
        "install",
        "--hermes-home",
        str(profile),
        "--dry-run",
        "--yes",
        HOME=str(tmp_path),
        HERMES_A2A_SOURCE_DIR=str(ROOT),
        HERMES_A2A_REF="v-test",
        HERMES_PYTHON=os.environ.get("PYTHON", "") or os.sys.executable,
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / ".local" / "bin" / "hermes_a2a").exists()
    assert "DRY RUN" in result.stdout or '"dry_run": true' in result.stdout
    assert not (profile / "plugins" / "a2a").exists()


def test_remote_help_does_not_require_hermes_home():
    result = run_remote(None, "--help")

    assert result.returncode == 0
    assert "hermes_a2a" in result.stdout
    assert "install" in result.stdout
    assert "update" in result.stdout
    assert "uninstall" in result.stdout


def test_remote_rejects_unknown_command_without_bootstrap(tmp_path):
    result = run_remote(tmp_path, "explode", HERMES_A2A_SOURCE_DIR=str(ROOT))

    assert result.returncode != 0
    assert "Unknown command" in result.stderr
    assert not (tmp_path / ".local" / "bin" / "hermes_a2a").exists()


def test_remote_has_no_redundant_command_wrappers():
    assert REMOTE.exists()
    assert not (ROOT / "scripts" / "install.sh").exists()
    assert not (ROOT / "scripts" / "update.sh").exists()
    assert not (ROOT / "scripts" / "uninstall.sh").exists()
    assert INSTALL.exists()
    assert UNINSTALL.exists()
