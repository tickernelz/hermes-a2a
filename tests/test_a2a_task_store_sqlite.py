import json
import sqlite3
from datetime import datetime, timedelta, timezone

from plugin import task_store
from plugin.paths import task_db_path, task_store_path


def test_task_store_defaults_to_sqlite_and_does_not_rewrite_legacy_json(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    legacy = task_store_path()
    legacy.write_text(json.dumps({}), encoding="utf-8")

    task_store.create_task(
        "local-1",
        direction="outbound",
        agent_name="reviewer",
        url="http://agent.local",
        state="submitted",
        remote_task_id="remote-1",
    )

    assert task_db_path().exists()
    assert not legacy.exists()
    assert (tmp_path / "a2a_tasks.json.migrated").exists()
    with sqlite3.connect(task_db_path()) as conn:
        rows = conn.execute("SELECT task_id, remote_task_id FROM tasks").fetchall()
    assert rows == [("local-1", "remote-1")]


def test_task_store_migrates_legacy_json_once(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    legacy = task_store_path()
    legacy.write_text(
        json.dumps(
            {
                "old-local": {
                    "task_id": "old-local",
                    "direction": "outbound",
                    "agent_name": "reviewer",
                    "url": "http://agent.local",
                    "state": "working",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                    "local_task_id": "old-local",
                    "remote_task_id": "old-remote",
                    "notify_requested": True,
                    "response": "pending",
                    "push_token": "legacy-token",
                }
            }
        ),
        encoding="utf-8",
    )

    assert task_store.get_task("old-local")["remote_task_id"] == "old-remote"
    assert task_store.find_task("old-remote", agent_name="reviewer")["task_id"] == "old-local"
    assert task_store.get_task("old-local")["push_token"] == "legacy-token"
    assert (tmp_path / "a2a_tasks.json.migrated").exists()

    task_store.get_task("old-local")
    with sqlite3.connect(task_db_path()) as conn:
        count = conn.execute("SELECT COUNT(*) FROM tasks WHERE task_id = 'old-local'").fetchone()[0]
    assert count == 1


def test_task_store_corrupt_legacy_json_is_preserved_and_does_not_wipe_sqlite(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    task_store.create_task("existing", direction="outbound", agent_name="reviewer", url="http://agent.local", state="completed")
    task_store_path().write_text("{not json", encoding="utf-8")

    assert task_store.get_task("missing") is None
    assert task_store.get_task("existing")["state"] == "completed"
    assert (tmp_path / "a2a_tasks.json.corrupt").exists()


def test_task_store_prunes_old_terminal_and_expires_old_nonterminal(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    now = datetime.now(timezone.utc)
    task_store.create_task("done-old", direction="outbound", agent_name="reviewer", url="http://agent.local", state="completed")
    task_store.create_task("work-old", direction="outbound", agent_name="reviewer", url="http://agent.local", state="working")
    task_store.create_task("done-new", direction="outbound", agent_name="reviewer", url="http://agent.local", state="completed")

    with sqlite3.connect(task_db_path()) as conn:
        conn.execute("UPDATE tasks SET updated_at = ? WHERE task_id IN ('done-old', 'work-old')", ((now - timedelta(days=40)).isoformat(),))
        conn.commit()

    result = task_store.cleanup(retention_days=30, nonterminal_expire_hours=24, dry_run=False)

    assert result["pruned_terminal"] == 1
    assert result["expired_nonterminal"] == 1
    assert task_store.get_task("done-old") is None
    assert task_store.get_task("work-old")["state"] == "expired"
    assert task_store.get_task("done-new")["state"] == "completed"


def test_task_store_cleanup_dry_run_does_not_mutate(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    task_store.create_task("done-old", direction="outbound", agent_name="reviewer", url="http://agent.local", state="completed")
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    with sqlite3.connect(task_db_path()) as conn:
        conn.execute("UPDATE tasks SET updated_at = ? WHERE task_id = 'done-old'", (old,))
        conn.commit()

    result = task_store.cleanup(retention_days=30, dry_run=True)

    assert result["pruned_terminal"] == 1
    assert task_store.get_task("done-old") is not None
