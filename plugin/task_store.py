"""Persistent background A2A task store."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from .paths import task_store_path
from .protocol import is_terminal_state, transition_state
from .security import filter_outbound

_lock = Lock()


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


def _load() -> dict[str, dict[str, Any]]:
    path = task_store_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save(tasks: dict[str, dict[str, Any]]) -> None:
    _atomic_write(task_store_path(), json.dumps(tasks, ensure_ascii=False, sort_keys=True, indent=2))


def _clean(value: Any) -> str:
    return str(value or "").strip()[:512]


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
        "response": filter_outbound(response or ""),
    }
    with _lock:
        tasks = _load()
        existing = tasks.get(record["task_id"])
        if existing and is_terminal_state(existing.get("state")):
            return existing
        if existing:
            record["created_at"] = existing.get("created_at") or now
            record["response"] = existing.get("response") or record["response"]
        tasks[record["task_id"]] = record
        _save(tasks)
    return record


def update_task(task_id: str, **changes: Any) -> dict[str, Any] | None:
    key = _clean(task_id)
    with _lock:
        tasks = _load()
        record = tasks.get(key)
        if not record:
            return None
        old_state = record.get("state", "unknown")
        requested_state = changes.pop("state", None)
        if requested_state is not None:
            record["state"] = transition_state(old_state, requested_state)
        if is_terminal_state(old_state):
            tasks[key] = record
            _save(tasks)
            return record
        for field in ("agent_name", "url", "context_id", "local_task_id", "remote_task_id"):
            if field in changes and changes[field] not in (None, ""):
                record[field] = _clean(changes[field])
        if "notify_requested" in changes:
            record["notify_requested"] = bool(changes["notify_requested"])
        if "response" in changes and changes["response"] is not None:
            record["response"] = filter_outbound(str(changes["response"]))
        record["updated_at"] = _now()
        tasks[key] = record
        _save(tasks)
    return record


def get_task(task_id: str) -> dict[str, Any] | None:
    with _lock:
        record = _load().get(_clean(task_id))
        return dict(record) if isinstance(record, dict) else None


def find_task(task_id: str, *, agent_name: str = "", url: str = "") -> dict[str, Any] | None:
    wanted = _clean(task_id)
    wanted_agent = _clean(agent_name).lower()
    wanted_url = _clean(url).rstrip("/")
    with _lock:
        tasks = _load()
        for record in tasks.values():
            if not isinstance(record, dict):
                continue
            ids = {record.get("task_id"), record.get("local_task_id"), record.get("remote_task_id")}
            if wanted not in ids:
                continue
            if wanted_agent and str(record.get("agent_name") or "").lower() != wanted_agent:
                continue
            if wanted_url and str(record.get("url") or "").rstrip("/") != wanted_url:
                continue
            return dict(record)
    return None
