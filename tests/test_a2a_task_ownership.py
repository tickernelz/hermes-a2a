import io
import json
from unittest.mock import MagicMock

from plugin import server


class Headers(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def make_handler(body, token="shared-token", remote="10.0.0.1"):
    raw = json.dumps(body).encode()
    handler = object.__new__(server.A2ARequestHandler)
    handler.server = MagicMock(
        auth_token=token,
        require_auth=True,
        limiter=MagicMock(allow=lambda _client: True),
        max_request_bytes=1_048_576,
        max_message_chars=50_000,
        max_response_chars=100_000,
        max_parts=20,
        max_raw_part_bytes=262_144,
    )
    handler.client_address = (remote, 12345)
    handler.headers = Headers({"Content-Length": str(len(raw)), "Authorization": f"Bearer {token}"})
    handler.rfile = io.BytesIO(raw)
    sent = {}
    handler._send_json = lambda data, status=200: sent.update({"data": data, "status": status})
    return handler, sent


def send_background(remote="10.0.0.1", task_id="shared-id"):
    handler, sent = make_handler(
        {"jsonrpc": "2.0", "id": "send", "method": "tasks/send", "params": {"id": task_id, "background": True, "message": {"parts": [{"text": "hello"}]}}},
        remote=remote,
    )
    handler.do_POST()
    return sent


def test_get_task_from_different_owner_is_rejected(monkeypatch):
    queue = server.TaskQueue()
    monkeypatch.setattr(server, "task_queue", queue)
    send_background(remote="10.0.0.1")

    handler_b, sent_b = make_handler(
        {"jsonrpc": "2.0", "id": "get", "method": "tasks/get", "params": {"id": "shared-id"}},
        remote="10.0.0.2",
    )
    handler_b.do_POST()

    assert sent_b["status"] in {403, 404}
    assert sent_b["data"]["error"]["code"] in {-32003, -32001}


def test_cancel_task_from_different_owner_is_rejected(monkeypatch):
    queue = server.TaskQueue()
    monkeypatch.setattr(server, "task_queue", queue)
    send_background(remote="10.0.0.1")

    handler_b, sent_b = make_handler(
        {"jsonrpc": "2.0", "id": "cancel", "method": "tasks/cancel", "params": {"id": "shared-id"}},
        remote="10.0.0.2",
    )
    handler_b.do_POST()

    assert sent_b["status"] in {403, 404}
    assert queue.get_status("shared-id")["state"] == "submitted"


def test_duplicate_send_from_different_owner_cannot_read_existing_response(monkeypatch):
    queue = server.TaskQueue()
    monkeypatch.setattr(server, "task_queue", queue)
    send_background(remote="10.0.0.1")
    queue.complete("shared-id", "secret-response")

    handler_b, sent_b = make_handler(
        {"jsonrpc": "2.0", "id": "send-again", "method": "tasks/send", "params": {"id": "shared-id", "message": {"parts": [{"text": "probe"}]}}},
        remote="10.0.0.2",
    )
    handler_b.do_POST()

    assert sent_b["status"] == 200
    assert sent_b["data"]["result"]["status"]["state"] == "failed"
    assert "Forbidden" in json.dumps(sent_b["data"])
    assert "secret-response" not in json.dumps(sent_b["data"])


class LegacyCompletedQueue:
    def pending_count(self):
        return 0

    def enqueue(self, task_id, text, metadata):
        return None

    def get_status(self, task_id):
        return {"state": "completed", "response": "secret-response"}

    def cancel(self, task_id):
        raise AssertionError("foreign owner must not cancel legacy task")


def test_legacy_queue_without_owner_for_fails_closed_for_existing_task(monkeypatch):
    monkeypatch.setattr(server, "task_queue", LegacyCompletedQueue())

    handler, sent = make_handler(
        {"jsonrpc": "2.0", "id": "send-again", "method": "tasks/send", "params": {"id": "legacy-id", "message": {"parts": [{"text": "probe"}]}}},
        remote="10.0.0.2",
    )
    handler.do_POST()

    assert sent["status"] == 200
    assert sent["data"]["result"]["status"]["state"] == "failed"
    assert "secret-response" not in json.dumps(sent["data"])

    get_handler, get_sent = make_handler(
        {"jsonrpc": "2.0", "id": "get", "method": "tasks/get", "params": {"id": "legacy-id"}},
        remote="10.0.0.2",
    )
    get_handler.do_POST()

    assert get_sent["status"] == 403
    assert "secret-response" not in json.dumps(get_sent["data"])
