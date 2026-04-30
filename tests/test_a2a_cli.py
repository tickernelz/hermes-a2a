import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def write_profile(path: Path, config: dict | None = None, env_text: str = "") -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "config.yaml").write_text(yaml.safe_dump(config or {"plugins": {}}, sort_keys=False), encoding="utf-8")
    if env_text:
        (path / ".env").write_text(env_text, encoding="utf-8")


def run_cli(args: list[str], *, home: Path, extra_env: dict[str, str] | None = None):
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PYTHONPATH"] = str(ROOT)
    env["HERMES_PYTHON"] = sys.executable
    for key in list(env):
        if key == "HERMES_HOME" or key.startswith("A2A_") or key == "WEBHOOK_PORT":
            env.pop(key, None)
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, "-m", "hermes_a2a_cli", *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )


def test_cli_status_json_reports_profile_install_state(tmp_path):
    profile = tmp_path / ".hermes"
    write_profile(profile, {"plugins": {"enabled": ["a2a"]}, "a2a": {"server": {"port": 41731}}})
    plugin_dir = profile / "plugins" / "a2a"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text('name: a2a\nversion: "2.1.0"\n', encoding="utf-8")
    state_dir = profile / "a2a"
    state_dir.mkdir()
    (state_dir / "state.json").write_text(
        json.dumps({"schema_version": 1, "installed_version": "2.1.0", "migration_version": "2.1.0"}),
        encoding="utf-8",
    )

    result = run_cli(["status", "--hermes-home", str(profile), "--json"], home=tmp_path)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["profile"]["home"] == str(profile.resolve())
    assert payload["installed"] is True
    assert payload["plugin_version"] == "2.1.0"
    assert payload["state"]["installed_version"] == "2.1.0"
    assert payload["config"]["a2a_enabled"] is True


def test_cli_doctor_fails_closed_for_multiple_profiles_without_target(tmp_path):
    write_profile(tmp_path / ".hermes")
    write_profile(tmp_path / ".hermes" / "profiles" / "coder")

    result = run_cli(["doctor", "--json"], home=tmp_path)

    assert result.returncode != 0
    assert "multiple Hermes profiles" in result.stderr


def test_cli_install_dry_run_does_not_mutate_and_prints_plan(tmp_path):
    profile = tmp_path / ".hermes"
    write_profile(profile)

    result = run_cli(["install", "--hermes-home", str(profile), "--dry-run", "--yes"], home=tmp_path)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["profile"]["home"] == str(profile.resolve())
    assert "install plugin payload" in payload["plan"]
    assert not (profile / "plugins" / "a2a").exists()
    assert not (profile / "a2a" / "state.json").exists()


def test_cli_install_writes_state_and_installs_plugin_without_restart(tmp_path):
    profile = tmp_path / ".hermes"
    write_profile(profile)

    result = run_cli(["install", "--hermes-home", str(profile), "--yes"], home=tmp_path)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["changed"] is True
    assert payload["restart_required"] is True
    assert (profile / "plugins" / "a2a" / "plugin.yaml").exists()
    state = json.loads((profile / "a2a" / "state.json").read_text(encoding="utf-8"))
    assert state["schema_version"] == 1
    assert state["installed_version"] == "2.1.0"
    assert state["source"]["type"] == "local_checkout"
    assert state["migration_version"] == "2.1.0"


def test_cli_native_install_delegates_to_new_manifest_flow(tmp_path):
    profile = tmp_path / ".hermes"
    write_profile(profile)
    result = run_cli(["install", "--hermes-home", str(profile), "--dry-run", "--yes", "--json"], home=tmp_path)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["command"] == "install"
