import importlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from plugin import config, paths, persistence, security, server, tools  # noqa: E402


def test_profile_paths_follow_hermes_home(monkeypatch, tmp_path):
    profile_home = tmp_path / "profiles" / "yanto"
    monkeypatch.setenv("HERMES_HOME", str(profile_home))
    monkeypatch.setattr(paths, "get_hermes_home", lambda: None, raising=False)

    assert paths.hermes_home() == profile_home.resolve()
    assert paths.config_path() == profile_home / "config.yaml"
    assert paths.conversation_dir() == profile_home / "a2a_conversations"
    assert paths.audit_log_path() == profile_home / "a2a_audit.jsonl"


def test_load_agents_resolves_auth_token_env_and_skips_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("YANTO_A2A_TOKEN", "secret-token")
    (tmp_path / "config.yaml").write_text(
        """
a2a:
  agents:
    - name: yanto_coder
      url: http://127.0.0.1:8082/
      description: Yanto
      auth_token_env: YANTO_A2A_TOKEN
      enabled: true
      trust_level: trusted
    - name: disabled
      url: http://127.0.0.1:8099
      enabled: false
""",
        encoding="utf-8",
    )

    agents = config.load_agents()

    assert len(agents) == 1
    assert agents[0]["name"] == "yanto_coder"
    assert agents[0]["url"] == "http://127.0.0.1:8082"
    assert agents[0]["auth_token"] == "secret-token"
    assert agents[0]["auth_token_env"] == "YANTO_A2A_TOKEN"


def test_direct_url_uses_configured_agent_auth_token(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        tools,
        "_load_configured_agents",
        lambda: [{"name": "local", "url": "http://127.0.0.1:8081", "auth_token": "secret-token"}],
    )
    monkeypatch.setattr(tools, "get_security_config", lambda: config.SecurityConfig(False, 50_000, 100_000, 20))

    def fake_http(method, url, json_body=None, headers=None):
        captured["headers"] = headers or {}
        return {"name": "local", "skills": [], "capabilities": {}}

    monkeypatch.setattr(tools, "_http_request", fake_http)

    result = json.loads(tools.handle_discover({"url": "http://127.0.0.1:8081"}))

    assert result["agent_name"] == "local"
    assert captured["headers"]["Authorization"] == "Bearer secret-token"


def test_task_queue_rejects_duplicate_completed_task_ids():
    queue = server.TaskQueue()
    task = queue.enqueue("task-1", "hello", {})
    assert task is not None

    queue.complete("task-1", "done")

    assert queue.enqueue("task-1", "again", {}) is None
    assert queue.get_status("task-1") == {"state": "completed", "response": "done"}


def test_task_queue_failed_task_reports_failed_state():
    queue = server.TaskQueue()
    task = queue.enqueue("task-1", "hello", {})
    assert task is not None

    queue.mark_processing("task-1")
    queue.fail("task-1", "failed response")

    assert task.ready.is_set()
    assert task.state == "failed"
    assert queue.drain_pending() == []
    assert queue.get_status("task-1") == {"state": "failed", "response": "failed response"}


def test_handle_task_send_reports_failed_task_state(monkeypatch):
    queue = server.TaskQueue()
    monkeypatch.setattr(server, "task_queue", queue)
    monkeypatch.setattr(server.threading, "Thread", lambda *args, **kwargs: MagicMock(start=lambda: None))

    handler = object.__new__(server.A2ARequestHandler)
    handler.server = MagicMock(max_message_chars=50_000)
    handler.client_address = ("127.0.0.1", 12345)

    def fail_async(task_id, text, metadata):
        task = original_enqueue(task_id, text, metadata)
        queue.fail(task_id, "failed response")
        return task

    original_enqueue = queue.enqueue
    monkeypatch.setattr(queue, "enqueue", fail_async)

    result = handler._handle_task_send({
        "id": "task-1",
        "message": {"parts": [{"type": "text", "text": "hello"}], "metadata": {}},
    })

    assert result["status"]["state"] == "failed"
    assert result["artifacts"][0]["parts"][0]["text"] == "failed response"


def test_task_queue_drain_excludes_processing_tasks():
    queue = server.TaskQueue()
    queue.enqueue("task-1", "first", {})
    queue.enqueue("task-2", "second", {})

    queue.mark_processing("task-1")

    pending = queue.drain_pending()
    assert [task.task_id for task in pending] == ["task-2"]
    assert queue.get_status("task-1") == {"state": "processing"}


def test_inbound_auth_fail_closed_when_required_without_token():
    handler = object.__new__(server.A2ARequestHandler)
    handler.server = MagicMock(auth_token="", require_auth=True)
    handler.client_address = ("127.0.0.1", 12345)

    assert handler._check_auth() is False


def test_inbound_auth_allows_localhost_only_when_auth_not_required():
    handler = object.__new__(server.A2ARequestHandler)
    handler.server = MagicMock(auth_token="", require_auth=False)
    handler.client_address = ("127.0.0.1", 12345)

    assert handler._check_auth() is True

    handler.client_address = ("10.0.0.2", 12345)
    assert handler._check_auth() is False


def test_filter_outbound_redacts_secrets():
    text = (
        "api_key='" + "abcdef" + "123456' "
        + "token=" + "ghp_" + "abcdefghijklmnopqrstuvwxyz "
        + "password: " + "hunter2"
    )

    filtered = security.filter_outbound(text)

    assert "abcdef" + "123456" not in filtered
    assert "ghp_" not in filtered
    assert "hunter2" not in filtered
    assert "[REDACTED]" in filtered


def test_persistence_writes_atomically_under_active_profile(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    path = persistence.save_exchange(
        agent_name="Yanto Coder",
        task_id="task-1",
        inbound_text="token=secretvalue",
        outbound_text="done",
        metadata={"auth_token": "secretvalue", "note": "ok"},
    )

    assert path == tmp_path / "a2a_conversations" / "yanto_coder" / f"{path.stem}.md"
    content = path.read_text(encoding="utf-8")
    assert "secretvalue" not in content
    assert "[REDACTED]" in content
    assert not list(path.parent.glob("*.tmp"))


def test_server_agent_card_uses_public_url(monkeypatch):
    monkeypatch.setenv("A2A_PUBLIC_URL", "https://jono.example/a2a/")
    monkeypatch.setenv("A2A_REQUIRE_AUTH", "true")
    monkeypatch.delenv("A2A_AUTH_TOKEN", raising=False)

    a2a_server = server.A2AServer("127.0.0.1", 0)
    try:
        card = a2a_server.build_agent_card()
    finally:
        a2a_server.server_close()

    assert card["url"] == "https://jono.example/a2a"
    assert a2a_server.require_auth is True
