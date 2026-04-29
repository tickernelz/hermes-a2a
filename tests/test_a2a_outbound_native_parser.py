import json

from plugin import tools


def test_parse_legacy_rpc_task_response_extracts_text():
    response = {
        "result": {
            "id": "t1",
            "status": {"state": "TASK_STATE_COMPLETED"},
            "artifacts": [{"parts": [{"type": "text", "text": "legacy ok"}]}],
        }
    }

    parsed = tools._parse_rpc_task_response(response, "fallback")

    assert parsed["task_id"] == "t1"
    assert parsed["state"] == "completed"
    assert parsed["text"] == "legacy ok"


def test_parse_native_task_response_extracts_text_and_artifact_summary():
    response = {
        "result": {
            "task": {
                "id": "t2",
                "status": {"state": "TASK_STATE_COMPLETED"},
                "artifacts": [
                    {"parts": [{"text": "native ok"}]},
                    {"parts": [{"file": {"name": "report.pdf", "mimeType": "application/pdf"}}]},
                ],
            }
        }
    }

    parsed = tools._parse_rpc_task_response(response, "fallback")

    assert parsed["task_id"] == "t2"
    assert parsed["state"] == "completed"
    assert "native ok" in parsed["text"]
    assert "report.pdf" in parsed["text"]


def test_parse_native_message_response_shape():
    response = {
        "result": {
            "message": {
                "taskId": "t3",
                "parts": [
                    {"text": "message text"},
                    {"file": {"name": "screenshot.png", "mimeType": "image/png"}},
                ],
            }
        }
    }

    parsed = tools._parse_rpc_task_response(response, "fallback")

    assert parsed["task_id"] == "t3"
    assert parsed["state"] == "completed"
    assert "message text" in parsed["text"]
    assert "screenshot.png" in parsed["text"]


def test_working_native_state_is_pollable(monkeypatch):
    calls = []

    def fake_http(method, url, json_body=None, headers=None):
        calls.append(json_body["method"])
        if json_body["method"] == "tasks/send":
            return {"result": {"task": {"id": "t4", "status": {"state": "TASK_STATE_WORKING"}}}}
        return {"result": {"task": {"id": "t4", "status": {"state": "TASK_STATE_COMPLETED"}, "artifacts": [{"parts": [{"text": "done"}]}]}}}

    monkeypatch.setattr(tools, "_http_request", fake_http)
    monkeypatch.setattr(tools, "_resolve_target", lambda name, url: ("http://agent", "token"))
    monkeypatch.setattr(tools, "_consume_rate_limit", lambda: True)
    monkeypatch.setattr(tools.time, "sleep", lambda _seconds: None)

    result = json.loads(tools.handle_call({"name": "agent", "message": "hello", "task_id": "t4"}))

    assert calls == ["tasks/send", "tasks/get"]
    assert result["state"] == "completed"
    assert result["response"] == "done"


def test_handle_call_can_send_structured_parts(monkeypatch):
    captured = {}

    def fake_http(method, url, json_body=None, headers=None):
        captured["payload"] = json_body
        return {"result": {"id": "t5", "status": {"state": "TASK_STATE_COMPLETED"}, "artifacts": [{"parts": [{"type": "text", "text": "ok"}]}]}}

    monkeypatch.setattr(tools, "_http_request", fake_http)
    monkeypatch.setattr(tools, "_resolve_target", lambda name, url: ("http://agent", "token"))
    monkeypatch.setattr(tools, "_consume_rate_limit", lambda: True)

    result = json.loads(
        tools.handle_call(
            {
                "name": "agent",
                "message": "see attached",
                "parts": [{"url": "https://example.com/a.png", "filename": "a.png", "mediaType": "image/png"}],
            }
        )
    )

    parts = captured["payload"]["params"]["message"]["parts"]
    assert parts[0]["type"] == "text"
    assert parts[1]["url"] == "https://example.com/a.png"
    assert result["response"] == "ok"


def test_is_native_card_detects_current_agent_card():
    assert tools._is_native_card({"preferredTransport": "JSONRPC", "protocolVersion": "0.3.0"}) is True
    assert tools._is_native_card({"preferredTransport": "JSONRPC", "protocolVersion": "0.10.0"}) is True
    assert tools._is_native_card({"additionalInterfaces": {"transport": "JSONRPC"}, "supportedInterfaces": "bad"}) is False
    assert tools._is_native_card({"protocolVersion": "0.2.0"}) is False


def test_build_message_parts_native_redacts_and_limits_extra_parts(monkeypatch):
    monkeypatch.setattr(tools, "get_security_config", lambda: type("Cfg", (), {"max_parts": 3, "max_raw_part_bytes": 16, "max_request_bytes": 2048})())
    parts = tools._build_message_parts(
        "hello",
        [{"type": "data", "data": {"token": "super-secret-token-value", "note": "ok"}}],
        native=True,
    )

    assert parts[0] == {"kind": "text", "text": "hello"}
    assert parts[1]["kind"] == "data"
    assert "super-secret-token-value" not in json.dumps(parts)


def test_build_message_parts_rejects_too_many_parts(monkeypatch):
    monkeypatch.setattr(tools, "get_security_config", lambda: type("Cfg", (), {"max_parts": 2, "max_raw_part_bytes": 16, "max_request_bytes": 2048})())

    try:
        tools._build_message_parts("hello", [{"text": "a"}, {"text": "b"}], native=True)
    except ValueError as exc:
        assert "Too many outbound" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_handle_call_discovers_native_card_and_uses_message_send(monkeypatch, tmp_path):
    calls = []

    def fake_http(method, url, json_body=None, headers=None):
        calls.append({"method": method, "url": url, "json_body": json_body, "headers": headers or {}})
        if method == "GET":
            return {"preferredTransport": "JSONRPC", "protocolVersion": "0.3.0"}
        assert json_body["method"] == "SendMessage"
        assert json_body["params"]["message"]["kind"] == "message"
        assert json_body["params"]["message"]["parts"][0] == {"kind": "text", "text": "hello"}
        return {
            "result": {
                "kind": "task",
                "id": "remote-task",
                "contextId": "remote-task",
                "status": {
                    "state": "completed",
                    "message": {"role": "agent", "parts": [{"kind": "text", "text": "ok"}]},
                },
            }
        }

    monkeypatch.setattr(tools, "_resolve_target", lambda name, url: ("http://agent.local", "secret"))
    monkeypatch.setattr(tools, "_consume_rate_limit", lambda: True)
    monkeypatch.setattr(tools, "_http_request", fake_http)
    monkeypatch.setattr(tools, "get_security_config", lambda: type("Cfg", (), {"max_parts": 20, "max_raw_part_bytes": 262_144, "max_request_bytes": 1_048_576})())
    monkeypatch.setattr("plugin.persistence.save_exchange", lambda **kwargs: None)
    monkeypatch.setattr("plugin.persistence.update_exchange", lambda **kwargs: None)

    result = json.loads(tools.handle_call({"name": "native", "message": "hello", "task_id": "local-task"}))

    assert result["state"] == "completed"
    assert result["response"] == "ok"
    assert calls[0]["method"] == "GET"
    assert calls[1]["method"] == "POST"
    assert calls[1]["headers"]["Authorization"] == "Bearer secret"


def test_handle_call_falls_back_to_legacy_send_for_unknown_card(monkeypatch):
    posts = []

    def fake_http(method, url, json_body=None, headers=None):
        if method == "GET":
            return {"protocolVersion": "0.2.0"}
        posts.append(json_body)
        return {"result": {"id": "remote-task", "status": {"state": "completed"}, "artifacts": [{"parts": [{"type": "text", "text": "legacy-ok"}]}]}}

    monkeypatch.setattr(tools, "_resolve_target", lambda name, url: ("http://agent.local", ""))
    monkeypatch.setattr(tools, "_consume_rate_limit", lambda: True)
    monkeypatch.setattr(tools, "_http_request", fake_http)
    monkeypatch.setattr(tools, "get_security_config", lambda: type("Cfg", (), {"max_parts": 20, "max_raw_part_bytes": 262_144, "max_request_bytes": 1_048_576})())
    monkeypatch.setattr("plugin.persistence.save_exchange", lambda **kwargs: None)
    monkeypatch.setattr("plugin.persistence.update_exchange", lambda **kwargs: None)

    result = json.loads(tools.handle_call({"url": "http://agent.local", "message": "hello", "task_id": "local-task"}))

    assert result["response"] == "legacy-ok"
    assert posts[0]["method"] == "tasks/send"
    assert posts[0]["params"]["message"]["parts"][0] == {"type": "text", "text": "hello"}


def test_build_message_parts_rejects_unsafe_reference_url(monkeypatch):
    monkeypatch.setattr(tools, "get_security_config", lambda: type("Cfg", (), {"max_parts": 3, "max_raw_part_bytes": 16, "max_request_bytes": 2048})())

    try:
        tools._build_message_parts("hello", [{"url": "file:///tmp/x"}], native=True)
    except ValueError as exc:
        assert "Unsupported outbound attachment URL scheme" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_build_message_parts_rejects_aggregate_size(monkeypatch):
    monkeypatch.setattr(tools, "get_security_config", lambda: type("Cfg", (), {"max_parts": 3, "max_raw_part_bytes": 256, "max_request_bytes": 40})())

    try:
        tools._build_message_parts("hello", [{"data": "x" * 100}], native=True)
    except ValueError as exc:
        assert "exceed max_request_bytes" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_handle_call_uses_native_gettask_when_polling(monkeypatch):
    calls = []

    def fake_http(method, url, json_body=None, headers=None):
        calls.append(json_body if method == "POST" else {"method": "GET"})
        if method == "GET":
            return {"preferredTransport": "JSONRPC", "protocolVersion": "0.3.0"}
        if json_body["method"] == "SendMessage":
            return {"result": {"kind": "task", "id": "remote-task", "contextId": "remote-task", "status": {"state": "working"}}}
        assert json_body["method"] == "GetTask"
        return {"result": {"kind": "task", "id": "remote-task", "contextId": "remote-task", "status": {"state": "completed", "message": {"parts": [{"kind": "text", "text": "done"}]}}}}

    monkeypatch.setattr(tools, "_resolve_target", lambda name, url: ("http://agent.local", ""))
    monkeypatch.setattr(tools, "_consume_rate_limit", lambda: True)
    monkeypatch.setattr(tools, "_http_request", fake_http)
    monkeypatch.setattr(tools.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(tools, "get_security_config", lambda: type("Cfg", (), {"max_parts": 20, "max_raw_part_bytes": 262_144, "max_request_bytes": 1_048_576})())
    monkeypatch.setattr("plugin.persistence.save_exchange", lambda **kwargs: None)
    monkeypatch.setattr("plugin.persistence.update_exchange", lambda **kwargs: None)

    result = json.loads(tools.handle_call({"url": "http://agent.local", "message": "hello", "task_id": "local-task"}))

    assert result["response"] == "done"
    assert [call.get("method") for call in calls if call.get("method") != "GET"] == ["SendMessage", "GetTask"]


def test_build_message_parts_rejects_nested_unsafe_reference_url(monkeypatch):
    monkeypatch.setattr(tools, "get_security_config", lambda: type("Cfg", (), {"max_parts": 3, "max_raw_part_bytes": 16, "max_request_bytes": 2048})())

    for part in (
        {"file": {"url": "file:///etc/passwd", "name": "x"}},
        {"file": {"uri": "ftp://example.com/x", "name": "x"}},
        {"blob": {"url": "javascript:alert(1)", "name": "x"}},
    ):
        try:
            tools._build_message_parts("hello", [part], native=True)
        except ValueError as exc:
            assert "Unsupported outbound attachment URL scheme" in str(exc)
        else:
            raise AssertionError(f"expected ValueError for {part}")


def test_build_message_parts_allows_nested_http_reference_url(monkeypatch):
    monkeypatch.setattr(tools, "get_security_config", lambda: type("Cfg", (), {"max_parts": 3, "max_raw_part_bytes": 16, "max_request_bytes": 2048})())

    parts = tools._build_message_parts("hello", [{"file": {"url": "https://example.com/a.png", "name": "a.png"}}], native=True)

    assert parts[1]["file"]["url"] == "https://example.com/a.png"


def test_handle_call_background_does_not_poll(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    calls = []

    def fake_http(method, url, json_body=None, headers=None):
        if method == "GET":
            return {"preferredTransport": "JSONRPC", "protocolVersion": "0.3.0"}
        calls.append(json_body["method"])
        if len(calls) > 1:
            raise AssertionError("background call must not poll")
        return {"result": {"kind": "task", "id": "remote-bg", "contextId": "ctx", "status": {"state": "working"}}}

    monkeypatch.setattr(tools, "_resolve_target", lambda name, url: ("http://agent.local", ""))
    monkeypatch.setattr(tools, "_consume_rate_limit", lambda: True)
    monkeypatch.setattr(tools, "_http_request", fake_http)
    monkeypatch.setattr(tools, "get_security_config", lambda: type("Cfg", (), {"max_parts": 20, "max_raw_part_bytes": 262_144, "max_request_bytes": 1_048_576})())
    monkeypatch.setattr("plugin.persistence.save_exchange", lambda **kwargs: None)

    result = json.loads(tools.handle_call({"url": "http://agent.local", "message": "slow", "task_id": "local-bg", "background": True}))

    assert calls == ["SendMessage"]
    assert result["background"] is True
    assert result["task_id"] == "remote-bg"
    assert result["state"] in {"submitted", "working"}


def test_handle_call_background_completed_immediately_returns_completed(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    def fake_http(method, url, json_body=None, headers=None):
        if method == "GET":
            return {"preferredTransport": "JSONRPC", "protocolVersion": "0.3.0"}
        return {"result": {"kind": "task", "id": "remote-bg", "contextId": "ctx", "status": {"state": "completed", "message": {"parts": [{"kind": "text", "text": "done"}]}}}}

    monkeypatch.setattr(tools, "_resolve_target", lambda name, url: ("http://agent.local", ""))
    monkeypatch.setattr(tools, "_consume_rate_limit", lambda: True)
    monkeypatch.setattr(tools, "_http_request", fake_http)
    monkeypatch.setattr(tools, "get_security_config", lambda: type("Cfg", (), {"max_parts": 20, "max_raw_part_bytes": 262_144, "max_request_bytes": 1_048_576})())
    monkeypatch.setattr("plugin.persistence.save_exchange", lambda **kwargs: None)
    monkeypatch.setattr("plugin.persistence.update_exchange", lambda **kwargs: None)

    result = json.loads(tools.handle_call({"url": "http://agent.local", "message": "slow", "task_id": "local-bg", "background": True}))

    assert result["background"] is True
    assert result["state"] == "completed"
    assert result["response"] == "done"


def test_handle_get_polls_remote_task_once(monkeypatch):
    captured = {}

    def fake_http(method, url, json_body=None, headers=None):
        captured["payload"] = json_body
        return {"result": {"kind": "task", "id": "remote-1", "contextId": "ctx", "status": {"state": "completed", "message": {"parts": [{"kind": "text", "text": "done"}]}}}}

    monkeypatch.setattr(tools, "_resolve_target", lambda name, url: ("http://agent.local", "secret"))
    monkeypatch.setattr(tools, "_http_request", fake_http)
    monkeypatch.setattr(tools, "_discover_card", lambda url, headers: {"preferredTransport": "JSONRPC", "protocolVersion": "0.3.0"})

    result = json.loads(tools.handle_get({"name": "reviewer", "task_id": "remote-1"}))

    assert captured["payload"]["method"] == "GetTask"
    assert captured["payload"]["params"]["id"] == "remote-1"
    assert result["state"] == "completed"
    assert result["response"] == "done"


def test_background_persists_before_http_post(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    def fake_http(method, url, json_body=None, headers=None):
        if method == "GET":
            return {"preferredTransport": "JSONRPC", "protocolVersion": "0.3.0"}
        raise TimeoutError("boom")

    monkeypatch.setattr(tools, "_resolve_target", lambda name, url: ("http://agent.local", ""))
    monkeypatch.setattr(tools, "_consume_rate_limit", lambda: True)
    monkeypatch.setattr(tools, "_http_request", fake_http)
    monkeypatch.setattr(tools, "get_security_config", lambda: type("Cfg", (), {"max_parts": 20, "max_raw_part_bytes": 262_144, "max_request_bytes": 1_048_576})())
    monkeypatch.setattr("plugin.persistence.save_exchange", lambda **kwargs: None)

    result = json.loads(tools.handle_call({"url": "http://agent.local", "message": "slow", "task_id": "local-bg", "background": True}))

    assert "error" in result
    record = tools.task_store.get_task("local-bg")
    assert record["state"] == "failed"
    assert record["url"] == "http://agent.local"
