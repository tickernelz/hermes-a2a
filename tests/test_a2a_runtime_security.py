import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import plugin as a2a_plugin
from plugin import config, tools


def test_stale_active_task_is_failed_and_next_trigger_can_run(monkeypatch):
    old = time.time() - 999
    a2a_plugin._active_a2a_tasks.clear()
    a2a_plugin._active_a2a_tasks["old-task"] = {"text": "old", "metadata": {}, "activated_at": old}
    failed = []
    fake_queue = MagicMock()
    fake_queue.fail.side_effect = lambda task_id, response: failed.append((task_id, response))
    task = MagicMock()
    task.task_id = "new-task"
    task.text = "new payload"
    task.metadata = {}
    fake_queue.drain_pending.return_value = [task]
    monkeypatch.setattr(a2a_plugin.a2a_server, "task_queue", fake_queue)
    monkeypatch.setattr(a2a_plugin, "_ACTIVE_TASK_TIMEOUT", 1)

    result = a2a_plugin._on_pre_gateway_dispatch(SimpleNamespace(text="[A2A trigger]"))

    assert failed == [("old-task", "(expired active A2A task)")]
    assert result["action"] == "rewrite"
    assert list(a2a_plugin._active_a2a_tasks) == ["new-task"]
    a2a_plugin._active_a2a_tasks.clear()


def test_unconfigured_private_url_is_rejected_when_direct_urls_enabled(monkeypatch):
    monkeypatch.setattr(tools, "_load_configured_agents", lambda: [])
    monkeypatch.setattr(tools, "get_security_config", lambda: config.SecurityConfig(True, 50_000, 100_000, 1_048_576, 262_144, 20, 20))

    try:
        tools._resolve_target("", "http://169.254.169.254/latest/meta-data")
    except ValueError as exc:
        assert "private or link-local" in str(exc)
    else:
        raise AssertionError("private metadata URL should be rejected before HTTP request")


def test_configured_local_url_is_allowed_by_network_policy(monkeypatch):
    monkeypatch.setattr(
        tools,
        "_load_configured_agents",
        lambda: [{"name": "local", "url": "http://127.0.0.1:41731", "auth_token": "***", "trust_level": "trusted"}],
    )
    monkeypatch.setattr(tools, "_http_request", lambda *args, **kwargs: {"name": "local", "skills": [], "capabilities": {}})

    result = json.loads(tools.handle_discover({"url": "http://127.0.0.1:41731"}))

    assert result["agent_name"] == "local"
