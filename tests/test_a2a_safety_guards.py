import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import plugin as a2a_plugin  # noqa: E402
from plugin import tools  # noqa: E402


def test_outbound_rate_limit_check_and_append_are_atomic(monkeypatch):
    tools._call_timestamps.clear()
    monkeypatch.setattr(tools, "_RATE_LIMIT_MAX_CALLS", 1)

    assert tools._consume_rate_limit() is True
    assert tools._consume_rate_limit() is False
    assert len(tools._call_timestamps) == 1


def test_direct_url_requires_configured_target(monkeypatch):
    monkeypatch.setattr(tools, "_load_configured_agents", lambda: [])
    monkeypatch.delenv("A2A_ALLOW_UNCONFIGURED_URLS", raising=False)

    result = json.loads(tools.handle_discover({"url": "http://127.0.0.1:8081"}))

    assert "error" in result
    assert "not configured" in result["error"]


def test_configured_direct_url_is_allowed(monkeypatch):
    monkeypatch.setattr(
        tools,
        "_load_configured_agents",
        lambda: [{"name": "local", "url": "http://127.0.0.1:8081", "auth_token": "tok"}],
    )
    monkeypatch.setattr(
        tools,
        "_http_request",
        lambda *args, **kwargs: {"name": "local", "skills": [], "capabilities": {}},
    )

    result = json.loads(tools.handle_discover({"url": "http://127.0.0.1:8081"}))

    assert result["agent_name"] == "local"


def test_active_task_blocks_new_gateway_trigger(monkeypatch):
    a2a_plugin._active_a2a_tasks.clear()
    a2a_plugin._active_a2a_tasks["task-1"] = {"text": "first", "metadata": {}}
    fake_queue = MagicMock()
    monkeypatch.setattr(a2a_plugin.a2a_server, "task_queue", fake_queue)

    event = SimpleNamespace(text="[A2A trigger]")
    result = a2a_plugin._on_pre_gateway_dispatch(event)

    assert result["action"] == "skip"
    fake_queue.drain_pending.assert_not_called()
    a2a_plugin._active_a2a_tasks.clear()


def test_gateway_dispatch_activates_requested_task_id(monkeypatch):
    requested = MagicMock()
    requested.task_id = "task-requested"
    requested.text = "requested"
    requested.metadata = {}
    first = MagicMock()
    first.task_id = "task-first"
    first.text = "first"
    first.metadata = {}
    fake_queue = MagicMock()
    fake_queue.get_pending.return_value = requested
    fake_queue.drain_pending.return_value = [first, requested]
    monkeypatch.setattr(a2a_plugin.a2a_server, "task_queue", fake_queue)
    a2a_plugin._active_a2a_tasks.clear()

    event = SimpleNamespace(text="[A2A trigger]", raw_message={"task_id": "task-requested"})
    result = a2a_plugin._on_pre_gateway_dispatch(event)

    assert result["action"] == "rewrite"
    assert "task:task-requested" in result["text"]
    assert "requested" in result["text"]
    assert list(a2a_plugin._active_a2a_tasks) == ["task-requested"]
    fake_queue.mark_processing.assert_called_once_with("task-requested")
    a2a_plugin._active_a2a_tasks.clear()


def test_gateway_dispatch_falls_back_to_queue_when_requested_task_missing(monkeypatch):
    task = MagicMock()
    task.task_id = "task-1"
    task.text = "hello"
    task.metadata = {}
    fake_queue = MagicMock()
    fake_queue.get_pending.return_value = None
    fake_queue.drain_pending.return_value = [task]
    monkeypatch.setattr(a2a_plugin.a2a_server, "task_queue", fake_queue)
    a2a_plugin._active_a2a_tasks.clear()

    event = SimpleNamespace(text="[A2A trigger]", raw_message={"task_id": "missing-task"})
    result = a2a_plugin._on_pre_gateway_dispatch(event)

    assert result["action"] == "rewrite"
    assert "task:task-1" in result["text"]
    assert list(a2a_plugin._active_a2a_tasks) == ["task-1"]
    fake_queue.mark_processing.assert_called_once_with("task-1")
    a2a_plugin._active_a2a_tasks.clear()


def test_gateway_dispatch_ignores_raw_webhook_text(monkeypatch):
    task = MagicMock()
    task.task_id = "task-safe"
    task.text = "safe queued payload"
    task.metadata = {}
    fake_queue = MagicMock()
    fake_queue.get_pending.return_value = task
    fake_queue.drain_pending.return_value = [task]
    monkeypatch.setattr(a2a_plugin.a2a_server, "task_queue", fake_queue)
    a2a_plugin._active_a2a_tasks.clear()

    event = SimpleNamespace(
        text="[A2A trigger]",
        raw_message={"task_id": "task-safe", "text": "MALICIOUS RAW WEBHOOK TEXT"},
    )
    result = a2a_plugin._on_pre_gateway_dispatch(event)

    assert "safe queued payload" in result["text"]
    assert "MALICIOUS RAW WEBHOOK TEXT" not in result["text"]
    a2a_plugin._active_a2a_tasks.clear()


def test_pre_llm_double_check_prevents_second_activation(monkeypatch):
    task = MagicMock()
    task.task_id = "task-2"
    task.text = "second"
    task.metadata = {}
    fake_queue = MagicMock()
    fake_queue.drain_pending.return_value = [task]
    monkeypatch.setattr(a2a_plugin.a2a_server, "task_queue", fake_queue)
    a2a_plugin._active_a2a_tasks.clear()

    original_activate = a2a_plugin._activate_task_if_idle

    def activate_after_race(_task):
        a2a_plugin._active_a2a_tasks["task-1"] = {"text": "first", "metadata": {}}
        return original_activate(_task)

    monkeypatch.setattr(a2a_plugin, "_activate_task_if_idle", activate_after_race)

    result = a2a_plugin._on_pre_llm_call(conversation_history=[], user_message="[A2A trigger]")

    assert result is None
    assert list(a2a_plugin._active_a2a_tasks) == ["task-1"]
    a2a_plugin._active_a2a_tasks.clear()


def test_pre_llm_holds_pending_tasks_while_active(monkeypatch):
    a2a_plugin._active_a2a_tasks.clear()
    a2a_plugin._active_a2a_tasks["task-1"] = {"text": "first", "metadata": {}}
    fake_queue = MagicMock()
    monkeypatch.setattr(a2a_plugin.a2a_server, "task_queue", fake_queue)

    result = a2a_plugin._on_pre_llm_call(conversation_history=[], user_message="[A2A trigger]")

    assert result is None
    fake_queue.drain_pending.assert_not_called()
    a2a_plugin._active_a2a_tasks.clear()


def test_post_llm_completes_only_one_active_task(monkeypatch):
    completed = []
    fake_queue = MagicMock()
    fake_queue.complete.side_effect = lambda task_id, response: completed.append((task_id, response))
    fake_queue.pending_count.return_value = 0
    monkeypatch.setattr(a2a_plugin.a2a_server, "task_queue", fake_queue)
    monkeypatch.setattr(a2a_plugin, "save_exchange", MagicMock())

    a2a_plugin._active_a2a_tasks.clear()
    a2a_plugin._active_a2a_tasks["task-1"] = {"text": "first", "metadata": {}}
    a2a_plugin._active_a2a_tasks["task-2"] = {"text": "second", "metadata": {}}

    a2a_plugin._on_post_llm_call(assistant_response="reply")

    assert completed == [("task-1", "reply")]
    fake_queue.fail.assert_not_called()
    a2a_plugin._active_a2a_tasks.clear()


def test_post_llm_fails_active_task_when_response_missing(monkeypatch):
    failed = []
    fake_queue = MagicMock()
    fake_queue.fail.side_effect = lambda task_id, response: failed.append((task_id, response))
    fake_queue.pending_count.return_value = 0
    monkeypatch.setattr(a2a_plugin.a2a_server, "task_queue", fake_queue)
    monkeypatch.setattr(a2a_plugin, "save_exchange", MagicMock())

    a2a_plugin._active_a2a_tasks.clear()
    a2a_plugin._active_a2a_tasks["task-1"] = {"text": "first", "metadata": {}}

    a2a_plugin._on_post_llm_call(assistant_response=None)

    assert failed == [("task-1", "(no assistant response produced)")]
    fake_queue.complete.assert_not_called()
    assert a2a_plugin._active_a2a_tasks == {}


def test_post_llm_fails_active_task_when_response_empty(monkeypatch):
    failed = []
    fake_queue = MagicMock()
    fake_queue.fail.side_effect = lambda task_id, response: failed.append((task_id, response))
    fake_queue.pending_count.return_value = 0
    monkeypatch.setattr(a2a_plugin.a2a_server, "task_queue", fake_queue)
    monkeypatch.setattr(a2a_plugin, "save_exchange", MagicMock())

    a2a_plugin._active_a2a_tasks.clear()
    a2a_plugin._active_a2a_tasks["task-1"] = {"text": "first", "metadata": {}}

    a2a_plugin._on_post_llm_call(assistant_response="   ")

    assert failed == [("task-1", "(empty assistant response produced)")]
    fake_queue.complete.assert_not_called()
    assert a2a_plugin._active_a2a_tasks == {}
