import time
from unittest.mock import MagicMock

import plugin as a2a_plugin
from plugin.config import get_server_config
from plugin.server import A2AServer


def test_server_config_exposes_runtime_limits_from_yaml():
    cfg = {
        "a2a": {
            "server": {
                "sync_response_timeout_seconds": 45,
                "active_task_timeout_seconds": 3600,
                "max_pending_tasks": 25,
            }
        }
    }

    server = get_server_config(cfg)

    assert server.sync_response_timeout_seconds == 45
    assert server.active_task_timeout_seconds == 3600
    assert server.max_pending_tasks == 25


def test_server_config_runtime_limits_ignore_legacy_environment(monkeypatch):
    monkeypatch.setenv("A2A_SYNC_RESPONSE_TIMEOUT", "33")
    monkeypatch.setenv("A2A_ACTIVE_TASK_TIMEOUT", "3600")
    monkeypatch.setenv("A2A_MAX_PENDING", "17")

    server = get_server_config({"a2a": {"server": {}}})

    assert server.sync_response_timeout_seconds == 120
    assert server.active_task_timeout_seconds == 7200
    assert server.max_pending_tasks == 10


def test_server_uses_configured_max_pending(monkeypatch):
    monkeypatch.setattr("plugin.server.get_server_config", lambda: type("ServerCfg", (), {
        "host": "127.0.0.1",
        "port": 0,
        "public_url": "http://127.0.0.1:0",
        "require_auth": True,
        "auth_token": "token",
        "sync_response_timeout_seconds": 120,
        "active_task_timeout_seconds": 7200,
        "max_pending_tasks": 2,
    })())
    srv = A2AServer("127.0.0.1", 0)
    try:
        assert srv.max_pending_tasks == 2
    finally:
        srv.server_close()


def test_active_task_timeout_uses_server_config(monkeypatch):
    now = [1_000.0]
    failed = []
    fake_queue = MagicMock()
    fake_queue.fail.side_effect = lambda task_id, response: failed.append((task_id, response))
    monkeypatch.setattr(a2a_plugin.time, "time", lambda: now[0])
    monkeypatch.setattr(a2a_plugin.a2a_server, "task_queue", fake_queue)
    monkeypatch.setattr(a2a_plugin, "_server", MagicMock(active_task_timeout_seconds=60))

    a2a_plugin._active_a2a_tasks.clear()
    a2a_plugin._active_a2a_tasks["task-1"] = {"text": "hello", "metadata": {}, "activated_at": now[0] - 61}

    a2a_plugin._expire_stale_active_tasks()

    assert failed == [("task-1", "(expired active A2A task)")]
    assert a2a_plugin._active_a2a_tasks == {}


def test_sync_wait_uses_server_configured_timeout(monkeypatch):
    waited = []
    task = MagicMock()
    task.task_id = "task-1"
    task.response = None
    task.ready.wait.side_effect = lambda timeout: waited.append(timeout)
    task.state = "working"

    handler = MagicMock()
    handler.server.max_pending_tasks = 10
    handler.server.sync_response_timeout_seconds = 7
    handler.server.max_message_chars = 50_000
    handler.server.max_parts = 20
    handler.server.max_raw_part_bytes = 262_144
    handler.server.max_response_chars = 100_000
    handler.client_address = ("127.0.0.1", 12345)
    handler._owner_id.return_value = "owner"
    handler._task_id_from_params.return_value = "task-1"

    monkeypatch.setattr("plugin.server.task_queue.pending_count", lambda: 0)
    monkeypatch.setattr("plugin.server.task_queue.enqueue", lambda *args, **kwargs: task)
    monkeypatch.setattr("plugin.server.task_store.create_task", lambda *args, **kwargs: {})
    monkeypatch.setattr("plugin.server._trigger_webhook", lambda task_id="": None)

    from plugin.server import A2ARequestHandler

    response = A2ARequestHandler._handle_task_send(handler, {"message": {"parts": [{"text": "hello"}]}})

    assert waited == [7]
    assert response["status"]["state"] == "working"
