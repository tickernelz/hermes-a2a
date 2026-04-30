from plugin import persistence


def test_save_exchange_appends_without_reading_existing_file(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    target = tmp_path / "a2a_conversations" / "reviewer" / "2099-01-01.md"
    monkeypatch.setattr(persistence, "datetime", FixedDateTime)

    persistence.save_exchange("reviewer", "task-1", "hello", "waiting", direction="outbound")

    original_read_text = type(target).read_text

    def fail_read_text(self, *args, **kwargs):
        if self.name.endswith(".md"):
            raise AssertionError("save_exchange must not read and rewrite existing markdown")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(type(target), "read_text", fail_read_text)
    persistence.save_exchange("reviewer", "task-2", "hi", "done", direction="outbound")

    monkeypatch.setattr(type(target), "read_text", original_read_text)
    content = target.read_text(encoding="utf-8")
    assert "task:task-1" in content
    assert "task:task-2" in content


def test_update_exchange_appends_update_event_instead_of_rewriting_today(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(persistence, "datetime", FixedDateTime)
    persistence.save_exchange("reviewer", "task-1", "(waiting for reply…)", "question", direction="outbound")
    target = tmp_path / "a2a_conversations" / "reviewer" / "2099-01-01.md"
    before = target.read_text(encoding="utf-8")

    assert persistence.update_exchange("reviewer", "task-1", "final answer") is True

    after = target.read_text(encoding="utf-8")
    assert after.startswith(before)
    assert "task:task-1 | update:completed" in after
    assert "final answer" in after


class FixedDateTime:
    @classmethod
    def now(cls, tz=None):
        from datetime import datetime, timezone

        return datetime(2099, 1, 1, 12, 34, 56, tzinfo=tz or timezone.utc)
