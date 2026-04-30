import json
from pathlib import Path

import pytest

from test_a2a_cli import run_cli

from hermes_a2a_cli.migrations.base import MigrationStep
from hermes_a2a_cli.migrations.registry import build_migration_plan, register_migration
from hermes_a2a_cli.state import adopt_existing_install, load_state, state_path, write_state


def test_state_round_trip_writes_profile_local_state(tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()
    payload = {
        "schema_version": 1,
        "installed_version": "0.2.2",
        "migration_version": "0.2.2",
        "source": {"type": "test"},
        "migration_ledger": [],
    }

    write_state(home, payload)

    assert state_path(home) == home / "a2a" / "state.json"
    assert load_state(home) == payload


def test_cli_status_reports_invalid_state_without_traceback(tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "config.yaml").write_text("plugins: {}\n", encoding="utf-8")
    path = state_path(home)
    path.parent.mkdir(parents=True)
    path.write_text("{bad json", encoding="utf-8")

    result = run_cli(["status", "--hermes-home", str(home), "--json"], home=tmp_path)

    assert result.returncode != 0
    assert "Invalid state file" in result.stderr
    assert "Traceback" not in result.stderr


def test_adopt_existing_install_creates_baseline_state_for_profile_without_state(tmp_path):
    home = tmp_path / ".hermes"
    plugin = home / "plugins" / "a2a"
    plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text('name: a2a\nversion: "0.2.2"\n', encoding="utf-8")

    state = adopt_existing_install(home, installed_version="0.2.2", source={"type": "local_checkout"})

    assert state["schema_version"] == 1
    assert state["installed_version"] == "0.2.2"
    assert state["migration_version"] == "0.2.2"
    assert state["source"] == {"type": "local_checkout"}
    assert state["migration_ledger"] == [
        {
            "id": "adopt_existing_install",
            "from": None,
            "to": "0.2.2",
            "status": "success",
            "backup_id": None,
        }
    ]
    assert json.loads(state_path(home).read_text(encoding="utf-8")) == state


def test_adopt_existing_install_keeps_existing_state(tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()
    existing = {
        "schema_version": 1,
        "installed_version": "2.0.0",
        "migration_version": "2.0.0",
        "source": {"type": "existing"},
        "migration_ledger": [],
    }
    write_state(home, existing)

    state = adopt_existing_install(home, installed_version="0.2.2", source={"type": "new"})

    assert state == existing
    assert load_state(home) == existing


class DummyMigration(MigrationStep):
    def __init__(self, from_version: str, to_version: str):
        self.id = f"{from_version}_to_{to_version}"
        self.from_version = from_version
        self.to_version = to_version

    def precheck(self, home: Path) -> None:
        pass

    def apply(self, home: Path, backup_id: str) -> None:
        pass

    def verify(self, home: Path) -> None:
        pass

    def rollback(self, home: Path, backup_id: str) -> None:
        pass


def test_migration_registry_builds_stepwise_plan():
    migrations = [DummyMigration("1.0.0", "1.1.0"), DummyMigration("1.1.0", "2.0.0")]

    plan = build_migration_plan("1.0.0", "2.0.0", migrations=migrations)

    assert [step.id for step in plan] == ["1.0.0_to_1.1.0", "1.1.0_to_2.0.0"]


def test_migration_registry_fails_closed_when_path_missing():
    migrations = [DummyMigration("1.0.0", "1.1.0")]

    with pytest.raises(ValueError, match="No migration path"):
        build_migration_plan("1.0.0", "2.0.0", migrations=migrations)


def test_migration_registry_rejects_duplicate_edges():
    migration = DummyMigration("1.0.0", "1.1.0")

    with pytest.raises(ValueError, match="Duplicate migration edge"):
        register_migration(migration, [migration])
