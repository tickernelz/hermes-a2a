import io
import json
import socket
import urllib.error
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import plugin as a2a_plugin
from plugin import server, task_store, tools
from plugin.security import RateLimiter


class FakeHeaders(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def make_native_handler(body, *, token="token"):
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
    handler.send_response = lambda status: sent.setdefault("status", status)
    handler.send_header = lambda key, value: None
    handler.end_headers = lambda: None
    return handler, sent


def read_response(handler):
    return json.loads(handler.wfile.getvalue().decode())


def test_json_rpc_message_request_is_not_misclassified_as_native_push():
    body = {
        "jsonrpc": "2.0",
        "id": "rpc-1",
        "method": "message/send",
        "message": {"taskId": "remote-1", "parts": [{"text": "not a push"}]},
        "params": {"id": "remote-1"},
    }

    assert server.A2ARequestHandler._is_native_push_payload(None, body) is False


def test_top_level_message_without_task_id_is_not_native_push():
    body = {"message": {"role": "agent", "parts": [{"text": "hello"}]}}

    assert server.A2ARequestHandler._is_native_push_payload(None, body) is False


def test_native_message_push_requires_task_id():
    body = {"message": {"taskId": "remote-1", "parts": [{"text": "done"}]}}

    assert server.A2ARequestHandler._is_native_push_payload(None, body) is True


def test_http_request_blocks_dns_rebinding_between_validation_and_open(monkeypatch):
    calls = []

    def fake_getaddrinfo(hostname, *args, **kwargs):
        calls.append(hostname)
        if len(calls) == 1:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443))]

    class ShouldNotConnect:
        def __init__(self, *args, **kwargs):
            raise AssertionError("request opened after DNS target changed")

    monkeypatch.setattr(tools.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(tools.http.client, "HTTPConnection", ShouldNotConnect)

    pinned = tools._validate_target_url("http://rebind.example", allow_private=False)

    with pytest.raises(ValueError, match="changed to a private"):
        tools._http_request("GET", pinned, allow_private=False)


def test_unconfigured_agent_card_discovery_revalidates_path_url(monkeypatch):
    def fake_getaddrinfo(hostname, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 80))]

    class ShouldNotConnect:
        def __init__(self, *args, **kwargs):
            raise AssertionError("request opened to private target")

    monkeypatch.setattr(tools.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(tools.http.client, "HTTPConnection", ShouldNotConnect)

    card = tools._discover_card("http://rebind.example", {}, allow_private=False)

    assert card is None


def test_handle_get_unconfigured_discovery_blocks_rebound_private_ip(monkeypatch):
    monkeypatch.setattr(tools, "_resolve_target", lambda name, url: ("http://rebind.example", "", False))

    def fake_getaddrinfo(hostname, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 80))]

    class ShouldNotConnect:
        def __init__(self, *args, **kwargs):
            raise AssertionError("request opened to private target")

    monkeypatch.setattr(tools.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(tools.http.client, "HTTPConnection", ShouldNotConnect)

    result = json.loads(tools.handle_get({"url": "http://rebind.example", "task_id": "task-1"}))

    assert "error" in result
    assert "private or link-local" in result["error"]


def test_handle_cancel_unconfigured_discovery_blocks_rebound_private_ip(monkeypatch):
    monkeypatch.setattr(tools, "_resolve_target", lambda name, url: ("http://rebind.example", "", False))

    def fake_getaddrinfo(hostname, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 80))]

    class ShouldNotConnect:
        def __init__(self, *args, **kwargs):
            raise AssertionError("request opened to private target")

    monkeypatch.setattr(tools.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(tools.http.client, "HTTPConnection", ShouldNotConnect)

    result = json.loads(tools.handle_cancel({"url": "http://rebind.example", "task_id": "task-1"}))

    assert "error" in result
    assert "private or link-local" in result["error"]


def test_https_request_pins_ip_but_preserves_tls_hostname(monkeypatch):
    created = {}

    def fake_getaddrinfo(hostname, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    class Response:
        status = 200

        def read(self, _size=-1):
            return b'{"ok": true}'

    class FakeHTTPSConnection:
        def __init__(self, host, port, timeout):
            created["host"] = host
            created["port"] = port
            created["timeout"] = timeout

        def request(self, method, path, body=None, headers=None):
            created["headers"] = headers
            created["socket"] = self._create_connection((created["host"], created["port"]))

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr(tools.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(tools.socket, "create_connection", lambda address, timeout=None, source_address=None: {"address": address})
    monkeypatch.setattr(tools.http.client, "HTTPSConnection", FakeHTTPSConnection)

    result = tools._http_request("GET", "https://agent.example/.well-known/agent.json", allow_private=False)

    assert result == {"ok": True}
    assert created["host"] == "agent.example"
    assert created["headers"]["Host"] == "agent.example"
    assert created["socket"] == {"address": ("93.184.216.34", 443)}


def test_polling_loop_reports_last_poll_error_instead_of_silent_timeout(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(tools, "_POLL_INTERVAL", 0)
    monkeypatch.setattr(tools, "_POLL_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(tools, "_load_configured_agents", lambda: [{"name": "remote", "url": "http://agent.local", "auth_token": ""}])
    monkeypatch.setattr(tools, "_discover_card", lambda *args, **kwargs: None)

    calls = []

    def fake_http(method, url, json_body=None, headers=None, **kwargs):
        calls.append(json_body.get("method") if isinstance(json_body, dict) else method)
        if len(calls) == 1:
            return {"jsonrpc": "2.0", "result": {"id": "remote-1", "status": {"state": "working"}}}
        raise RuntimeError("HTTP 401")

    monkeypatch.setattr(tools, "_http_request", fake_http)

    result = json.loads(tools.handle_call({"name": "remote", "message": "hello"}))

    assert "error" in result
    assert "HTTP 401" in result["error"]


def test_polling_loop_reports_json_rpc_poll_error(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(tools, "_POLL_INTERVAL", 0)
    monkeypatch.setattr(tools, "_POLL_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(tools, "_load_configured_agents", lambda: [{"name": "remote", "url": "http://agent.local", "auth_token": ""}])
    monkeypatch.setattr(tools, "_discover_card", lambda *args, **kwargs: None)

    calls = []

    def fake_http(method, url, json_body=None, headers=None, **kwargs):
        calls.append(json_body.get("method") if isinstance(json_body, dict) else method)
        if len(calls) == 1:
            return {"jsonrpc": "2.0", "result": {"id": "remote-1", "status": {"state": "working"}}}
        return {"jsonrpc": "2.0", "error": {"code": -32001, "message": "Task not found"}, "id": "rpc-2"}

    monkeypatch.setattr(tools, "_http_request", fake_http)

    result = json.loads(tools.handle_call({"name": "remote", "message": "hello"}))

    assert "error" in result
    assert "Task not found" in result["error"]


def test_rate_limiter_prunes_empty_buckets(monkeypatch):
    now = [1_000.0]
    limiter = RateLimiter(max_requests=5, window_seconds=60)
    monkeypatch.setattr("plugin.security.time.time", lambda: now[0])

    assert limiter.allow("client-1") is True
    now[0] = 2_000.0
    assert limiter.allow("client-2") is True

    assert "client-1" not in limiter._buckets
    assert "client-2" in limiter._buckets


def test_post_llm_fails_extra_active_tasks_instead_of_dropping_them(monkeypatch):
    completed = []
    failed = []
    fake_queue = MagicMock()
    fake_queue.complete.side_effect = lambda task_id, response: completed.append((task_id, response))
    fake_queue.fail.side_effect = lambda task_id, response: failed.append((task_id, response))
    fake_queue.pending_count.return_value = 0
    monkeypatch.setattr(a2a_plugin.a2a_server, "task_queue", fake_queue)
    monkeypatch.setattr(a2a_plugin, "save_exchange", MagicMock())

    a2a_plugin._active_a2a_tasks.clear()
    a2a_plugin._active_a2a_tasks["task-1"] = {"text": "first", "metadata": {}}
    a2a_plugin._active_a2a_tasks["task-2"] = {"text": "second", "metadata": {}}

    a2a_plugin._on_post_llm_call(assistant_response="reply")

    assert completed == [("task-1", "reply")]
    assert failed == [("task-2", "(discarded duplicate active A2A task)")]
    assert a2a_plugin._active_a2a_tasks == {}
    a2a_plugin._active_a2a_tasks.clear()


def test_trigger_webhook_ignores_invalid_wake_port(monkeypatch):
    opened = []
    monkeypatch.setattr(
        server,
        "get_wake_config",
        lambda: type(
            "WakeCfg",
            (),
            {"secret": "secret", "port": 70000, "route": "a2a_trigger"},
        )(),
    )
    monkeypatch.setattr(server.urllib.request, "urlopen", lambda *args, **kwargs: opened.append(args) or None)

    server._trigger_webhook("task-1")

    assert opened == []
