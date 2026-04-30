"""Persistent background A2A task store."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from .paths import task_db_path, task_store_path
from .protocol import is_terminal_state, transition_state
from .security import filter_outbound

_lock = Lock()
_SCHEMA_VERSION = 1
_TERMINAL_STATES = {"completed", "failed", "canceled", "cancelled", "rejected", "expired"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def _clean(value: Any) -> str:
    return str(value or "").strip()[:512]


def _clean_response(value: Any) -> str:
    return filter_outbound(str(value or ""))


def _connect() -> sqlite3.Connection:
    path = task_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            direction TEXT NOT NULL DEFAULT '',
            agent_name TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            context_id TEXT NOT NULL DEFAULT '',
            local_task_id TEXT NOT NULL DEFAULT '',
            remote_task_id TEXT NOT NULL DEFAULT '',
            notify_requested INTEGER NOT NULL DEFAULT 0,
            response TEXT NOT NULL DEFAULT '',
            push_token TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_local_task_id ON tasks(local_task_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_remote_task_id ON tasks(remote_task_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_agent_remote ON tasks(agent_name, remote_task_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_state_updated ON tasks(state, updated_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_updated ON tasks(updated_at)")
    conn.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT OR IGNORE INTO metadata(key, value) VALUES ('schema_version', ?)", (str(_SCHEMA_VERSION),))
    conn.commit()


def _row_to_record(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    record = dict(row)
    record["notify_requested"] = bool(record.get("notify_requested"))
    if not record.get("push_token"):
        record.pop("push_token", None)
    return record


def _legacy_migration_marker(path: Path) -> Path:
    return path.with_name(path.name + ".migrated")


def _legacy_corrupt_marker(path: Path) -> Path:
    return path.with_name(path.name + ".corrupt")


def _load_legacy_json(path: Path) -> dict[str, dict[str, Any]] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        corrupt = _legacy_corrupt_marker(path)
        if not corrupt.exists():
            try:
                path.replace(corrupt)
            except Exception:
                pass
        return None
    return data if isinstance(data, dict) else {}


def _migrate_legacy_if_needed(conn: sqlite3.Connection) -> None:
    path = task_store_path()
    if not path.exists() or _legacy_migration_marker(path).exists():
        return
    count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    if count:
        _load_legacy_json(path)
        return
    data = _load_legacy_json(path)
    if data is None:
        return
    now = _now()
    for key, raw in data.items():
        if not isinstance(raw, dict):
            continue
        task_id = _clean(raw.get("task_id") or key)
        if not task_id:
            continue
        record = {
            "task_id": task_id,
            "direction": _clean(raw.get("direction")),
            "agent_name": _clean(raw.get("agent_name")),
            "url": _clean(raw.get("url")),
            "state": transition_state("", raw.get("state") or "submitted"),
            "created_at": _clean(raw.get("created_at")) or now,
            "updated_at": _clean(raw.get("updated_at")) or now,
            "context_id": _clean(raw.get("context_id")),
            "local_task_id": _clean(raw.get("local_task_id") or task_id),
            "remote_task_id": _clean(raw.get("remote_task_id")),
            "notify_requested": 1 if raw.get("notify_requested") else 0,
            "response": _clean_response(raw.get("response")),
            "push_token": _clean(raw.get("push_token")),
        }
        _upsert_record(conn, record, preserve_terminal=False)
    conn.commit()
    try:
        path.replace(_legacy_migration_marker(path))
    except Exception:
        try:
            _legacy_migration_marker(path).write_text("migrated\n", encoding="utf-8")
        except Exception:
            pass


def _ensure_ready(conn: sqlite3.Connection) -> None:
    _migrate_legacy_if_needed(conn)


def _upsert_record(conn: sqlite3.Connection, record: dict[str, Any], *, preserve_terminal: bool = True) -> dict[str, Any]:
    existing = _row_to_record(conn.execute("SELECT * FROM tasks WHERE task_id = ?", (record["task_id"],)).fetchone())
    if preserve_terminal and existing and is_terminal_state(existing.get("state")):
        return existing
    if existing:
        record["created_at"] = existing.get("created_at") or record["created_at"]
        if not record["response"]:
            record["response"] = existing.get("response") or ""
    conn.execute(
        """
        INSERT INTO tasks(
            task_id, direction, agent_name, url, state, created_at, updated_at,
            context_id, local_task_id, remote_task_id, notify_requested, response, push_token
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(task_id) DO UPDATE SET
            direction = excluded.direction,
            agent_name = excluded.agent_name,
            url = excluded.url,
            state = excluded.state,
            created_at = excluded.created_at,
            updated_at = excluded.updated_at,
            context_id = excluded.context_id,
            local_task_id = excluded.local_task_id,
            remote_task_id = excluded.remote_task_id,
            notify_requested = excluded.notify_requested,
            response = excluded.response,
            push_token = excluded.push_token
        """,
        (
            record["task_id"],
            record["direction"],
            record["agent_name"],
            record["url"],
            record["state"],
            record["created_at"],
            record["updated_at"],
            record["context_id"],
            record["local_task_id"],
            record["remote_task_id"],
            1 if record["notify_requested"] else 0,
            record["response"],
            record.get("push_token", ""),
        ),
    )
    return record


def create_task(
    task_id: str,
    *,
    direction: str,
    agent_name: str,
    url: str,
    state: str = "submitted",
    context_id: str = "",
    local_task_id: str = "",
    remote_task_id: str = "",
    notify_requested: bool = False,
    response: str = "",
    push_token: str = "",
) -> dict[str, Any]:
    now = _now()
    record = {
        "task_id": _clean(task_id),
        "direction": _clean(direction),
        "agent_name": _clean(agent_name),
        "url": _clean(url),
        "state": transition_state("", state),
        "created_at": now,
        "updated_at": now,
        "context_id": _clean(context_id),
        "local_task_id": _clean(local_task_id or task_id),
        "remote_task_id": _clean(remote_task_id),
        "notify_requested": bool(notify_requested),
        "response": _clean_response(response),
        "push_token": _clean(push_token),
    }
    with _lock, _connect() as conn:
        _ensure_ready(conn)
        saved = _upsert_record(conn, record)
        conn.commit()
    saved = dict(saved)
    if not saved.get("push_token"):
        saved.pop("push_token", None)
    return saved


def update_task(task_id: str, **changes: Any) -> dict[str, Any] | None:
    key = _clean(task_id)
    with _lock, _connect() as conn:
        _ensure_ready(conn)
        record = _row_to_record(conn.execute("SELECT * FROM tasks WHERE task_id = ?", (key,)).fetchone())
        if not record:
            return None
        old_state = record.get("state", "unknown")
        requested_state = changes.pop("state", None)
        if requested_state is not None:
            record["state"] = transition_state(old_state, requested_state)
        if is_terminal_state(old_state):
            conn.commit()
            return record
        for field in ("agent_name", "url", "context_id", "local_task_id", "remote_task_id", "push_token"):
            if field in changes and changes[field] not in (None, ""):
                record[field] = _clean(changes[field])
        if "notify_requested" in changes:
            record["notify_requested"] = bool(changes["notify_requested"])
        if "response" in changes and changes["response"] is not None:
            record["response"] = _clean_response(changes["response"])
        record["updated_at"] = _now()
        _upsert_record(conn, record, preserve_terminal=False)
        conn.commit()
    if not record.get("push_token"):
        record.pop("push_token", None)
    return record


def get_task(task_id: str) -> dict[str, Any] | None:
    with _lock, _connect() as conn:
        _ensure_ready(conn)
        return _row_to_record(conn.execute("SELECT * FROM tasks WHERE task_id = ?", (_clean(task_id),)).fetchone())


def find_task(task_id: str, *, agent_name: str = "", url: str = "") -> dict[str, Any] | None:
    wanted = _clean(task_id)
    wanted_agent = _clean(agent_name).lower()
    wanted_url = _clean(url).rstrip("/")
    clauses = ["(task_id = ? OR local_task_id = ? OR remote_task_id = ?)"]
    params: list[Any] = [wanted, wanted, wanted]
    if wanted_agent:
        clauses.append("LOWER(agent_name) = ?")
        params.append(wanted_agent)
    if wanted_url:
        clauses.append("RTRIM(url, '/') = ?")
        params.append(wanted_url)
    query = "SELECT * FROM tasks WHERE " + " AND ".join(clauses) + " ORDER BY updated_at DESC LIMIT 1"
    with _lock, _connect() as conn:
        _ensure_ready(conn)
        return _row_to_record(conn.execute(query, params).fetchone())


def cleanup(*, retention_days: int = 30, nonterminal_expire_hours: int = 24, dry_run: bool = True) -> dict[str, int | bool]:
    now = datetime.now(timezone.utc)
    terminal_cutoff = (now - timedelta(days=max(0, int(retention_days)))).isoformat()
    nonterminal_cutoff = (now - timedelta(hours=max(0, int(nonterminal_expire_hours)))).isoformat()
    terminal_placeholders = ",".join("?" for _ in _TERMINAL_STATES)
    with _lock, _connect() as conn:
        _ensure_ready(conn)
        terminal_params = [*_TERMINAL_STATES, terminal_cutoff]
        pruned_terminal = conn.execute(
            f"SELECT COUNT(*) FROM tasks WHERE state IN ({terminal_placeholders}) AND updated_at < ?",
            terminal_params,
        ).fetchone()[0]
        expired_nonterminal = conn.execute(
            f"SELECT COUNT(*) FROM tasks WHERE state NOT IN ({terminal_placeholders}) AND updated_at < ?",
            [*_TERMINAL_STATES, nonterminal_cutoff],
        ).fetchone()[0]
        if not dry_run:
            conn.execute(
                f"DELETE FROM tasks WHERE state IN ({terminal_placeholders}) AND updated_at < ?",
                terminal_params,
            )
            conn.execute(
                f"UPDATE tasks SET state = 'expired', response = CASE WHEN response = '' THEN '(expired)' ELSE response END, updated_at = ? WHERE state NOT IN ({terminal_placeholders}) AND updated_at < ?",
                [now.isoformat(), *_TERMINAL_STATES, nonterminal_cutoff],
            )
            conn.commit()
    return {"dry_run": bool(dry_run), "pruned_terminal": int(pruned_terminal), "expired_nonterminal": int(expired_nonterminal)}
