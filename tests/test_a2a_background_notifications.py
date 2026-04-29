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


def test_tasks_notify_updates_background_task_and_exchange(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    task_store.create_task("local-1", direction="outbound", agent_name="reviewer", url="http://agent.local", state="submitted", remote_task_id="remote-1")
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
    task_store.create_task("local-1", direction="outbound", agent_name="reviewer", url="http://agent.local", state="submitted", remote_task_id="remote-1")

    for text in ("first", "late"):
        handler, _sent = make_handler(monkeypatch, "tasks/notify", {"id": "remote-1", "from": "reviewer", "state": "completed", "message": {"parts": [{"text": text}]}})
        handler.do_POST()

    record = task_store.get_task("local-1")
    assert record["state"] == "completed"
    assert record["response"] == "first"


def test_notify_from_wrong_agent_cannot_update_task(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    task_store.create_task("local-1", direction="outbound", agent_name="reviewer", url="http://agent.local", state="submitted", remote_task_id="remote-1")
    handler, sent = make_handler(monkeypatch, "tasks/notify", {"id": "remote-1", "from": "other", "state": "completed", "message": {"parts": [{"text": "hijack"}]}})

    handler.do_POST()
    data = read_response(handler)

    assert sent["status"] == 404
    assert data["error"]["code"] == -32001
    assert task_store.get_task("local-1")["state"] == "submitted"
