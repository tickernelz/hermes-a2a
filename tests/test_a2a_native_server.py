import io
import json
from unittest.mock import MagicMock

from plugin import server


class FakeHeaders(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def make_handler(monkeypatch, method, params):
    handler = object.__new__(server.A2ARequestHandler)
    body = json.dumps({"jsonrpc": "2.0", "id": "rpc-1", "method": method, "params": params}).encode()
    handler.headers = FakeHeaders({"Content-Length": str(len(body)), "Authorization": "Bearer token"})
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


def install_fast_queue(monkeypatch):
    queue = server.TaskQueue()

    def trigger(task_id=""):
        queue.complete(task_id, "native response")

    monkeypatch.setattr(server, "task_queue", queue)
    monkeypatch.setattr(server, "_trigger_webhook", trigger)
    return queue


def test_message_send_accepts_native_shape_and_returns_native_task(monkeypatch):
    install_fast_queue(monkeypatch)
    handler, sent = make_handler(
        monkeypatch,
        "message/send",
        {
            "message": {
                "taskId": "task-native",
                "contextId": "ctx-1",
                "messageId": "msg-1",
                "parts": [{"text": "hello native"}],
                "metadata": {"sender_name": "tester"},
            }
        },
    )

    handler.do_POST()
    data = read_response(handler)

    assert sent["status"] == 200
    assert data["result"]["kind"] == "task"
    assert data["result"]["id"] == "task-native"
    assert data["result"]["contextId"] == "ctx-1"
    assert data["result"]["status"]["state"] == "completed"
    assert data["result"]["status"]["message"]["parts"][0] == {"kind": "text", "text": "native response"}
    assert data["result"]["artifacts"][0]["artifactId"] == "task-native-artifact-0"
    assert data["result"]["artifacts"][0]["parts"][0] == {"kind": "text", "text": "native response"}


def test_sendmessage_alias_accepts_native_shape(monkeypatch):
    install_fast_queue(monkeypatch)
    handler, _sent = make_handler(
        monkeypatch,
        "SendMessage",
        {"message": {"task_id": "task-alias", "parts": [{"text": "hello alias"}]}},
    )

    handler.do_POST()
    data = read_response(handler)

    assert data["result"]["id"] == "task-alias"


def test_tasks_send_legacy_shape_is_preserved(monkeypatch):
    install_fast_queue(monkeypatch)
    handler, _sent = make_handler(
        monkeypatch,
        "tasks/send",
        {"id": "legacy-id", "message": {"parts": [{"type": "text", "text": "hello legacy"}]}},
    )

    handler.do_POST()
    data = read_response(handler)

    assert data["result"]["id"] == "legacy-id"
    assert "task" not in data["result"]
    assert data["result"]["status"]["state"] == "completed"


def test_gettask_returns_native_task_shape(monkeypatch):
    queue = server.TaskQueue()
    monkeypatch.setattr(server, "task_queue", queue)
    task = queue.enqueue("task-1", "hi", {})
    queue.complete("task-1", "done")
    assert task is not None

    handler, _sent = make_handler(monkeypatch, "GetTask", {"id": "task-1"})

    handler.do_POST()
    data = read_response(handler)

    assert data["result"]["kind"] == "task"
    assert data["result"]["id"] == "task-1"
    assert data["result"]["contextId"] == "task-1"
    assert data["result"]["status"]["state"] == "completed"


def test_tasks_get_legacy_shape_is_preserved(monkeypatch):
    queue = server.TaskQueue()
    monkeypatch.setattr(server, "task_queue", queue)
    queue.enqueue("task-1", "hi", {})
    queue.complete("task-1", "done")

    handler, _sent = make_handler(monkeypatch, "tasks/get", {"id": "task-1"})

    handler.do_POST()
    data = read_response(handler)

    assert data["result"]["id"] == "task-1"
    assert "task" not in data["result"]
    assert data["result"]["status"]["state"] == "completed"


def test_multimodal_parts_are_bridged_into_safe_prompt_and_metadata(monkeypatch):
    captured = {}
    queue = server.TaskQueue()

    original_enqueue = queue.enqueue

    def enqueue(task_id, text, metadata):
        captured["text"] = text
        captured["metadata"] = metadata
        task = original_enqueue(task_id, text, metadata)
        queue.complete(task_id, "ok")
        return task

    monkeypatch.setattr(server, "task_queue", queue)
    monkeypatch.setattr(queue, "enqueue", enqueue)
    monkeypatch.setattr(server, "_trigger_webhook", lambda task_id="": None)
    handler, _sent = make_handler(
        monkeypatch,
        "message/send",
        {
            "message": {
                "taskId": "task-multi",
                "contextId": "ctx-1",
                "messageId": "msg-1",
                "parts": [
                    {"text": "please inspect"},
                    {"data": {"ticket": "REQ-1"}},
                    {"url": "https://example.com/screen.png", "filename": "screen.png", "mediaType": "image/png"},
                ],
            }
        },
    )

    handler.do_POST()

    assert "please inspect" in captured["text"]
    assert "[A2A structured data]" in captured["text"]
    assert "[A2A attachment references]" in captured["text"]
    assert captured["metadata"]["context_id"] == "ctx-1"
    assert captured["metadata"]["message_id"] == "msg-1"
    assert captured["metadata"]["a2a_parts"][1]["type"] == "json"
    assert captured["metadata"]["a2a_parts"][2]["type"] == "file"


def test_non_text_only_attachment_is_not_treated_as_empty(monkeypatch):
    install_fast_queue(monkeypatch)
    handler, _sent = make_handler(
        monkeypatch,
        "message/send",
        {"message": {"taskId": "attachment-only", "parts": [{"url": "https://example.com/file.pdf", "filename": "file.pdf"}]}},
    )

    handler.do_POST()
    data = read_response(handler)

    assert data["result"]["status"]["state"] == "completed"


def test_empty_parts_fails_cleanly(monkeypatch):
    install_fast_queue(monkeypatch)
    handler, _sent = make_handler(monkeypatch, "message/send", {"message": {"taskId": "empty", "parts": []}})

    handler.do_POST()
    data = read_response(handler)

    assert data["result"]["status"]["state"] == "failed"
    assert "Empty message" in data["result"]["artifacts"][0]["parts"][0]["text"]


def test_gettask_missing_id_returns_invalid_params(monkeypatch):
    install_fast_queue(monkeypatch)
    handler, sent = make_handler(monkeypatch, "GetTask", {})

    handler.do_POST()
    data = read_response(handler)

    assert sent["status"] == 400
    assert data["error"]["code"] == -32602
    assert data["error"]["message"] == "Missing task id"


def test_canceltask_missing_id_returns_invalid_params(monkeypatch):
    install_fast_queue(monkeypatch)
    handler, sent = make_handler(monkeypatch, "CancelTask", {})

    handler.do_POST()
    data = read_response(handler)

    assert sent["status"] == 400
    assert data["error"]["code"] == -32602
    assert data["error"]["message"] == "Missing task id"


def test_response_is_truncated_by_max_response_chars(monkeypatch):
    install_fast_queue(monkeypatch)
    handler, _sent = make_handler(
        monkeypatch,
        "message/send",
        {"message": {"taskId": "long-response", "parts": [{"text": "hello"}]}},
    )
    handler.server.max_response_chars = 3

    handler.do_POST()
    data = read_response(handler)

    assert data["result"]["status"]["message"]["parts"][0]["text"].startswith("nat")
    assert "truncated by A2A max_response_chars" in data["result"]["status"]["message"]["parts"][0]["text"]


def test_canceltask_returns_native_task_shape(monkeypatch):
    queue = server.TaskQueue()
    monkeypatch.setattr(server, "task_queue", queue)
    queue.enqueue("task-cancel", "hi", {})
    handler, _sent = make_handler(monkeypatch, "CancelTask", {"id": "task-cancel"})

    handler.do_POST()
    data = read_response(handler)

    assert data["result"]["kind"] == "task"
    assert data["result"]["id"] == "task-cancel"
    assert data["result"]["status"]["state"] == "canceled"


def test_gettask_cached_response_is_truncated_by_max_response_chars(monkeypatch):
    queue = server.TaskQueue()
    monkeypatch.setattr(server, "task_queue", queue)
    monkeypatch.setattr(server, "get_security_config", lambda: type("Cfg", (), {"max_response_chars": 4})())
    queue.enqueue("task-long", "hi", {})
    queue.complete("task-long", "abcdef")
    handler, _sent = make_handler(monkeypatch, "GetTask", {"id": "task-long"})
    handler.server.max_response_chars = 4

    handler.do_POST()
    data = read_response(handler)

    text = data["result"]["status"]["message"]["parts"][0]["text"]
    assert text.startswith("abcd")
    assert "truncated by A2A max_response_chars" in text
