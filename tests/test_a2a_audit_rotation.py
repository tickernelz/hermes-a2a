import json

from plugin.security import AuditLogger


def test_audit_logger_rotates_multiple_bounded_backups(tmp_path):
    path = tmp_path / "a2a_audit.jsonl"
    audit = AuditLogger(path, max_bytes=180, backup_count=3)

    for idx in range(30):
        audit.log("rpc_request", {"task_id": f"task-{idx}", "message": "x" * 80})

    files = sorted(tmp_path.glob("a2a_audit.jsonl*"))
    assert {file.name for file in files}.issubset({"a2a_audit.jsonl", "a2a_audit.jsonl.1", "a2a_audit.jsonl.2", "a2a_audit.jsonl.3"})
    assert len(files) <= 4
    for file in files:
        for line in file.read_text(encoding="utf-8").splitlines():
            assert json.loads(line)["event"] == "rpc_request"


def test_audit_logger_redacts_and_caps_sensitive_event_data(tmp_path):
    path = tmp_path / "a2a_audit.jsonl"
    audit = AuditLogger(path, max_bytes=10_000, backup_count=2, max_field_chars=32)

    audit.log(
        "security",
        {
            "authorization": "Bearer super-secret-token",
            "nested": {"api_key": "sk-proj-1234567890abcdef", "text": "y" * 200},
            "items": [{"password": "hunter2"}],
        },
    )

    entry = json.loads(path.read_text(encoding="utf-8").strip())
    text = json.dumps(entry)
    assert "super-secret-token" not in text
    assert "sk-proj" not in text
    assert "hunter2" not in text
    assert entry["authorization"] == "[REDACTED]"
    assert len(entry["nested"]["text"]) < 80
