import io
import json
from unittest.mock import MagicMock

from plugin import server, task_store


class FakeHeaders(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def make_handler(monkeypatch, method, params, *, token="token"):
    handler = object.__new__(server.A2ARequestHandler)
    body = json.dumps({"jsonrpc": "2.0", "id": "rpc-1", "method": method, "params": params}).encode()
    headers = {"Content-Length": str(len(body))}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    handler.headers = FakeHeaders(headers)
    handler.rfile = io.BytesIO(body)
    handler.wfile = io.BytesIO()
    handler.client_address = ("127.0.0.1", 12345)
    handler.server = MagicMock(
        auth_token="token",
        require_auth=True,
        limiter=MagicMock(allow=lambda _client: True),
        max_request_bytes=1_048_576,
        max_message_chars=50_000,
        max_response_chars=100_000,
        max_parts=20,
        max_raw_part_bytes=1_048_576,
    )
    sent = {}
    monkeypatch.setattr(handler, "send_response", lambda status: sent.setdefault("status", status))
    monkeypatch.setattr(handler, "send_header", lambda key, value: None)
    monkeypatch.setattr(handler, "end_headers", lambda: None)
    return handler, sent


def read_response(handler):
    return json.loads(handler.wfile.getvalue().decode())


def make_native_push_handler(monkeypatch, body, *, token="token"):
    handler = object.__new__(server.A2ARequestHandler)
    raw = json.dumps(body).encode()
    headers = {"Content-Length": str(len(raw))}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    handler.headers = FakeHeaders(headers)
    handler.rfile = io.BytesIO(raw)
    handler.wfile = io.BytesIO()
    handler.client_address = ("127.0.0.1", 12345)
    handler.server = MagicMock(
        auth_token="token",
        require_auth=True,
        limiter=MagicMock(allow=lambda _client: True),
        max_request_bytes=1_048_576,
        max_message_chars=50_000,
        max_response_chars=100_000,
        max_parts=20,
        max_raw_part_bytes=1_048_576,
    )
    sent = {}
    monkeypatch.setattr(handler, "send_response", lambda status: sent.setdefault("status", status))
    monkeypatch.setattr(handler, "send_header", lambda key, value: None)
    monkeypatch.setattr(handler, "end_headers", lambda: None)
    return handler, sent


def test_tasks_notify_updates_background_task_and_exchange(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    task_store.create_task("local-1", direction="outbound", agent_name="reviewer", url="http://agent.local", state="submitted", remote_task_id="remote-1", notify_requested=True, push_token="expected-token")
    updated = {}
    monkeypatch.setattr("plugin.persistence.update_exchange", lambda **kwargs: updated.setdefault("exchange", kwargs) or True)
    monkeypatch.setattr(server, "_trigger_webhook", lambda task_id="": updated.setdefault("wake", task_id))

    handler, sent = make_handler(
        monkeypatch,
        "TaskNotification",
        {
            "id": "remote-1",
            "from": "reviewer",
            "status": {"state": "completed", "message": {"parts": [{"kind": "text", "text": "done"}]}},
        },
    )

    handler.headers["X-A2A-Notification-Token"] = "expected-token"

    handler.do_POST()
    data = read_response(handler)
    record = task_store.get_task("local-1")

    assert sent["status"] == 200
    assert data["result"]["status"]["state"] == "completed"
    assert record["state"] == "completed"
    assert record["response"] == "done"
    assert updated["exchange"]["task_id"] == "local-1"
    assert updated["wake"] == "local-1"


def test_tasks_notify_requires_auth(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    handler, sent = make_handler(monkeypatch, "tasks/notify", {"id": "remote-1"}, token="")

    handler.do_POST()
    data = read_response(handler)

    assert sent["status"] == 401
    assert data["error"]["message"] == "Unauthorized"


def test_duplicate_notify_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    task_store.create_task("local-1", direction="outbound", agent_name="reviewer", url="http://agent.local", state="submitted", remote_task_id="remote-1", notify_requested=True)

    for text in ("first", "late"):
        handler, _sent = make_handler(monkeypatch, "tasks/notify", {"id": "remote-1", "from": "reviewer", "state": "completed", "message": {"parts": [{"text": text}]}})
        handler.do_POST()

    record = task_store.get_task("local-1")
    assert record["state"] == "completed"
    assert record["response"] == "first"


def test_notify_from_wrong_agent_cannot_update_task(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    task_store.create_task("local-1", direction="outbound", agent_name="reviewer", url="http://agent.local", state="submitted", remote_task_id="remote-1", notify_requested=True)
    handler, sent = make_handler(monkeypatch, "tasks/notify", {"id": "remote-1", "from": "other", "state": "completed", "message": {"parts": [{"text": "hijack"}]}})

    handler.do_POST()
    data = read_response(handler)

    assert sent["status"] == 404
    assert data["error"]["code"] == -32001
    assert task_store.get_task("local-1")["state"] == "submitted"

def test_native_status_update_push_payload_updates_background_task(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    task_store.create_task("local-1", direction="outbound", agent_name="reviewer", url="http://agent.local", state="submitted", remote_task_id="remote-1", notify_requested=True, push_token="expected-token")
    updated = {}
    monkeypatch.setattr("plugin.persistence.update_exchange", lambda **kwargs: updated.setdefault("exchange", kwargs) or True)
    monkeypatch.setattr(server, "_trigger_webhook", lambda task_id="": updated.setdefault("wake", task_id))

    handler, sent = make_native_push_handler(monkeypatch, {
        "statusUpdate": {
            "taskId": "remote-1",
            "contextId": "ctx-1",
            "status": {"state": "TASK_STATE_COMPLETED", "message": {"parts": [{"kind": "text", "text": "done"}]}},
        }
    })
    handler.headers["X-A2A-Notification-Token"] = "expected-token"

    handler.do_POST()
    data = read_response(handler)
    record = task_store.get_task("local-1")

    assert sent["status"] == 200
    assert data["status"] == "ok"
    assert data["task_id"] == "local-1"
    assert record["state"] == "completed"
    assert record["response"] == "done"
    assert updated["exchange"]["task_id"] == "local-1"
    assert updated["wake"] == "local-1"


def test_native_task_push_payload_updates_background_task(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    task_store.create_task("local-1", direction="outbound", agent_name="reviewer", url="http://agent.local", state="submitted", remote_task_id="remote-1", notify_requested=True, push_token="expected-token")
    monkeypatch.setattr("plugin.persistence.update_exchange", lambda **kwargs: True)
    monkeypatch.setattr(server, "_trigger_webhook", lambda task_id="": None)

    handler, sent = make_native_push_handler(monkeypatch, {
        "task": {
            "id": "remote-1",
            "contextId": "ctx-1",
            "status": {"state": "completed"},
            "artifacts": [{"parts": [{"text": "artifact done"}]}],
        }
    })

    handler.headers["X-A2A-Notification-Token"] = "expected-token"

    handler.do_POST()
    record = task_store.get_task("local-1")

    assert sent["status"] == 200
    assert record["state"] == "completed"
    assert record["response"] == "artifact done"




def test_native_push_with_expected_notification_token_does_not_require_global_bearer(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    task_store.create_task(
        "local-1",
        direction="outbound",
        agent_name="reviewer",
        url="http://agent.local",
        state="submitted",
        remote_task_id="remote-1",
        notify_requested=True,
        push_token="expected-token",
    )
    monkeypatch.setattr("plugin.persistence.update_exchange", lambda **kwargs: True)
    monkeypatch.setattr(server, "_trigger_webhook", lambda task_id="": None)

    handler, sent = make_native_push_handler(monkeypatch, {
        "statusUpdate": {
            "taskId": "remote-1",
            "status": {"state": "completed", "message": {"parts": [{"text": "done"}]}},
        }
    }, token="")
    handler.headers["X-A2A-Notification-Token"] = "expected-token"

    handler.do_POST()
    data = read_response(handler)
    record = task_store.get_task("local-1")

    assert sent["status"] == 200
    assert data["status"] == "ok"
    assert record["state"] == "completed"
    assert record["response"] == "done"


def test_native_push_payload_requires_auth(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    task_store.create_task(
        "local-1",
        direction="outbound",
        agent_name="reviewer",
        url="http://agent.local",
        state="submitted",
        remote_task_id="remote-1",
        notify_requested=True,
        push_token="expected-token",
    )
    handler, sent = make_native_push_handler(monkeypatch, {"statusUpdate": {"taskId": "remote-1"}}, token="")

    handler.do_POST()
    data = read_response(handler)

    assert sent["status"] == 401
    assert data["error"]["message"] == "Unauthorized"


def test_native_push_unknown_task_is_404(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    handler, sent = make_native_push_handler(monkeypatch, {"statusUpdate": {"taskId": "missing", "status": {"state": "completed"}}})

    handler.do_POST()
    data = read_response(handler)

    assert sent["status"] == 404
    assert data["error"] == "Task not found"


def test_native_push_duplicate_does_not_overwrite_terminal_response(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    task_store.create_task("local-1", direction="outbound", agent_name="reviewer", url="http://agent.local", state="submitted", remote_task_id="remote-1", notify_requested=True, push_token="expected-token")
    monkeypatch.setattr("plugin.persistence.update_exchange", lambda **kwargs: True)
    monkeypatch.setattr(server, "_trigger_webhook", lambda task_id="": None)

    for text in ("first", "late"):
        handler, _sent = make_native_push_handler(monkeypatch, {
            "statusUpdate": {
                "taskId": "remote-1",
                "status": {"state": "completed", "message": {"parts": [{"text": text}]}},
            }
        })
        handler.headers["X-A2A-Notification-Token"] = "expected-token"
        handler.do_POST()

    record = task_store.get_task("local-1")
    assert record["state"] == "completed"
    assert record["response"] == "first"

def test_native_push_unknown_source_cannot_update_when_notification_token_expected(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    task_store.create_task(
        "local-1",
        direction="outbound",
        agent_name="reviewer",
        url="http://agent.local",
        state="submitted",
        remote_task_id="remote-1",
        notify_requested=True,
    )
    task_store.update_task("local-1", push_token="expected-token")
    monkeypatch.setattr("plugin.persistence.update_exchange", lambda **kwargs: True)
    monkeypatch.setattr(server, "_trigger_webhook", lambda task_id="": None)

    handler, sent = make_native_push_handler(monkeypatch, {
        "statusUpdate": {
            "taskId": "remote-1",
            "status": {"state": "completed", "message": {"parts": [{"text": "hijack"}]}},
        }
    })

    handler.do_POST()
    data = read_response(handler)
    record = task_store.get_task("local-1")

    assert sent["status"] == 401
    assert data["error"]["message"] == "Unauthorized"
    assert record["state"] == "submitted"
    assert record["response"] == ""


def test_native_push_with_expected_notification_token_updates_task(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    task_store.create_task(
        "local-1",
        direction="outbound",
        agent_name="reviewer",
        url="http://agent.local",
        state="submitted",
        remote_task_id="remote-1",
        notify_requested=True,
    )
    task_store.update_task("local-1", push_token="expected-token")
    monkeypatch.setattr("plugin.persistence.update_exchange", lambda **kwargs: True)
    monkeypatch.setattr(server, "_trigger_webhook", lambda task_id="": None)

    handler, sent = make_native_push_handler(monkeypatch, {
        "statusUpdate": {
            "taskId": "remote-1",
            "status": {"state": "completed", "message": {"parts": [{"text": "done"}]}},
        }
    })
    handler.headers["X-A2A-Notification-Token"] = "expected-token"

    handler.do_POST()
    record = task_store.get_task("local-1")

    assert sent["status"] == 200
    assert record["state"] == "completed"
    assert record["response"] == "done"


def test_native_artifact_update_does_not_complete_task_before_status(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    task_store.create_task("local-1", direction="outbound", agent_name="reviewer", url="http://agent.local", state="submitted", remote_task_id="remote-1", notify_requested=True, push_token="expected-token")
    monkeypatch.setattr("plugin.persistence.update_exchange", lambda **kwargs: True)
    monkeypatch.setattr(server, "_trigger_webhook", lambda task_id="": None)

    artifact_handler, artifact_sent = make_native_push_handler(monkeypatch, {
        "artifactUpdate": {
            "taskId": "remote-1",
            "contextId": "ctx-1",
            "artifact": {"parts": [{"text": "chunk 1"}]},
            "lastChunk": False,
        }
    })
    artifact_handler.headers["X-A2A-Notification-Token"] = "expected-token"

    artifact_handler.do_POST()
    record = task_store.get_task("local-1")

    assert artifact_sent["status"] == 200
    assert record["state"] in {"submitted", "working"}
    assert record["response"] == "chunk 1"

    status_handler, _status_sent = make_native_push_handler(monkeypatch, {
        "statusUpdate": {
            "taskId": "remote-1",
            "status": {"state": "completed", "message": {"parts": [{"text": "final"}]}},
        }
    })
    status_handler.headers["X-A2A-Notification-Token"] = "expected-token"

    status_handler.do_POST()
    record = task_store.get_task("local-1")

    assert record["state"] == "completed"
    assert record["response"] == "final"
