from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


class StateError(RuntimeError):
    pass

SCHEMA_VERSION = 1
STATE_DIR_NAME = "a2a"
STATE_FILE_NAME = "state.json"


def state_path(home: Path) -> Path:
    return home.expanduser().resolve() / STATE_DIR_NAME / STATE_FILE_NAME


def load_state(home: Path) -> dict[str, Any]:
    path = state_path(home)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StateError(f"Invalid state file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise StateError(f"State file {path} must contain an object")
    return data


def write_state(home: Path, state: dict[str, Any]) -> None:
    path = state_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def build_install_state(*, installed_version: str, source: dict[str, Any], backup_id: str | None = None, ledger_id: str | None = None) -> dict[str, Any]:
    entry_id = ledger_id or f"install_{installed_version.replace('.', '_')}"
    return {
        "schema_version": SCHEMA_VERSION,
        "installed_version": installed_version,
        "source": source,
        "migration_version": installed_version,
        "last_backup": backup_id,
        "migration_ledger": [
            {
                "id": entry_id,
                "from": None,
                "to": installed_version,
                "status": "success",
                "backup_id": backup_id,
            }
        ],
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def adopt_existing_install(home: Path, *, installed_version: str, source: dict[str, Any]) -> dict[str, Any]:
    existing = load_state(home)
    if existing:
        return existing
    state = build_install_state(
        installed_version=installed_version,
        source=source,
        backup_id=None,
        ledger_id="adopt_existing_install",
    )
    write_state(home, state)
    return state
