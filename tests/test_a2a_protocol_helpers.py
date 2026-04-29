import json

import pytest

from plugin import protocol


def test_legacy_and_native_text_parts_normalize_to_prompt_text():
    normalized = protocol.normalize_inbound_message(
        {
            "role": "user",
            "parts": [
                {"type": "text", "text": "legacy hello"},
                {"text": "native hello"},
            ],
        },
        max_message_chars=50_000,
        max_parts=20,
        max_raw_part_bytes=256,
    )

    assert normalized.prompt_text == "legacy hello\nnative hello"
    assert [part["type"] for part in normalized.safe_parts] == ["text", "text"]


def test_json_data_part_is_serialized_without_object_placeholder():
    normalized = protocol.normalize_inbound_message(
        {"parts": [{"data": {"ticket": "REQ-1", "priority": 2}}]},
        max_message_chars=50_000,
        max_parts=20,
        max_raw_part_bytes=256,
    )

    assert "[A2A structured data]" in normalized.prompt_text
    assert '"ticket": "REQ-1"' in normalized.prompt_text
    assert "[object Object]" not in normalized.prompt_text
    assert normalized.safe_parts[0]["type"] == "json"


def test_url_file_part_is_represented_as_attachment_reference_without_fetching():
    normalized = protocol.normalize_inbound_message(
        {
            "parts": [
                {
                    "url": "https://example.com/a.png",
                    "filename": "a.png",
                    "mediaType": "image/png",
                    "metadata": {"width": 100},
                }
            ]
        },
        max_message_chars=50_000,
        max_parts=20,
        max_raw_part_bytes=256,
    )

    assert "[A2A attachment references]" in normalized.prompt_text
    assert "a.png" in normalized.prompt_text
    assert "image/png" in normalized.prompt_text
    assert "url_origin: https://example.com" in normalized.prompt_text
    assert "https://example.com/a.png" not in normalized.prompt_text
    assert normalized.safe_parts[0]["type"] == "file"
    assert normalized.safe_parts[0]["url"] == "https://example.com/a.png"


def test_non_http_attachment_url_is_rejected():
    with pytest.raises(protocol.ProtocolError, match="Unsupported attachment URL scheme"):
        protocol.normalize_inbound_message(
            {"parts": [{"url": "file:///etc/passwd", "filename": "passwd"}]},
            max_message_chars=50_000,
            max_parts=20,
            max_raw_part_bytes=256,
        )


def test_raw_part_over_limit_is_rejected_before_decoding():
    with pytest.raises(protocol.ProtocolError, match="raw part exceeds"):
        protocol.normalize_inbound_message(
            {"parts": [{"raw": "A" * 257, "filename": "x.bin"}]},
            max_message_chars=50_000,
            max_parts=20,
            max_raw_part_bytes=256,
        )


def test_too_many_parts_is_rejected():
    with pytest.raises(protocol.ProtocolError, match="Too many message parts"):
        protocol.normalize_inbound_message(
            {"parts": [{"text": str(i)} for i in range(3)]},
            max_message_chars=50_000,
            max_parts=2,
            max_raw_part_bytes=256,
        )


def test_state_mapping_accepts_native_and_legacy_names():
    assert protocol.normalize_state("TASK_STATE_COMPLETED") == "completed"
    assert protocol.normalize_state("completed") == "completed"
    assert protocol.to_native_state("working") == "working"
    assert protocol.to_native_state("input-required") == "input-required"
    assert protocol.to_legacy_state("TASK_STATE_CANCELED") == "canceled"
    assert protocol.to_legacy_state("TASK_STATE_INPUT_REQUIRED") == "working"


def test_build_task_result_supports_legacy_and_native_shapes():
    legacy = protocol.build_task_result("t1", "completed", "done", native=False)
    native = protocol.build_task_result("t1", "completed", "done", native=True, context_id="ctx-1")

    assert legacy["id"] == "t1"
    assert legacy["status"]["state"] == "completed"
    assert legacy["artifacts"][0]["parts"][0]["text"] == "done"
    assert native["kind"] == "task"
    assert native["id"] == "t1"
    assert native["contextId"] == "ctx-1"
    assert native["status"]["state"] == "completed"
    assert native["status"]["message"]["kind"] == "message"
    assert native["status"]["message"]["parts"][0] == {"kind": "text", "text": "done"}
    assert native["artifacts"][0]["artifactId"] == "t1-artifact-0"
    assert native["artifacts"][0]["parts"][0] == {"kind": "text", "text": "done"}


def test_extract_task_response_text_handles_legacy_native_and_non_text_artifacts():
    legacy = {
        "id": "t1",
        "status": {"state": "completed"},
        "artifacts": [{"parts": [{"type": "text", "text": "hello"}]}],
    }
    native = {
        "task": {
            "id": "t1",
            "status": {"state": "completed"},
            "artifacts": [
                {"parts": [{"text": "world"}]},
                {"parts": [{"file": {"name": "out.png", "mimeType": "image/png"}}]},
            ],
        }
    }

    assert protocol.extract_task_id(legacy, "fallback") == "t1"
    assert protocol.extract_task_state(native) == "completed"
    assert protocol.extract_response_text(legacy) == "hello"
    assert "world" in protocol.extract_response_text(native)
    assert "out.png" in protocol.extract_response_text(native)


def test_extract_task_response_prefers_status_message_over_duplicate_artifact():
    task = protocol.build_task_result("t1", "completed", "done", native=True)

    assert protocol.extract_response_text(task) == "done"


def test_extract_direct_message_result_is_completed_and_readable():
    message = {"kind": "message", "messageId": "m1", "role": "agent", "parts": [{"kind": "text", "text": "hi"}]}

    assert protocol.extract_task_state(message) == "completed"
    assert protocol.extract_response_text(message) == "hi"


def test_extract_task_response_text_reads_status_message_parts():
    task = {
        "id": "t1",
        "status": {
            "state": "completed",
            "message": {"role": "agent", "parts": [{"text": "status text"}]},
        },
    }

    assert protocol.extract_response_text(task) == "status text"
