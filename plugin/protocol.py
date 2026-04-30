"""Protocol normalization helpers for legacy and native-ish A2A payloads."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

_SAFE_MIME_RE = re.compile(r"[^A-Za-z0-9!#$&^_.+-/]+")
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._@()+,= -]+")
_STATE_ALIASES = {
    "": "unknown",
    "pending": "working",
    "processing": "working",
    "submitted": "submitted",
    "working": "working",
    "input_required": "input-required",
    "input-required": "input-required",
    "auth_required": "auth-required",
    "auth-required": "auth-required",
    "completed": "completed",
    "failed": "failed",
    "rejected": "rejected",
    "canceled": "canceled",
    "cancelled": "canceled",
    "unknown": "unknown",
    "task_state_submitted": "submitted",
    "task_state_working": "working",
    "task_state_input_required": "input-required",
    "task_state_auth_required": "auth-required",
    "task_state_completed": "completed",
    "task_state_failed": "failed",
    "task_state_rejected": "rejected",
    "task_state_canceled": "canceled",
    "task_state_cancelled": "canceled",
    "task_state_unknown": "unknown",
}
_NATIVE_STATES = {state: state for state in {
    "submitted", "working", "input-required", "auth-required", "completed", "failed", "rejected", "canceled", "unknown"
}}
_LEGACY_STATES = {
    "submitted": "working",
    "working": "working",
    "input-required": "working",
    "auth-required": "working",
    "completed": "completed",
    "failed": "failed",
    "rejected": "failed",
    "canceled": "canceled",
    "unknown": "unknown",
}


class ProtocolError(ValueError):
    """Raised when an A2A envelope is invalid or unsafe."""


@dataclass(frozen=True)
class NormalizedMessage:
    prompt_text: str
    safe_parts: list[dict[str, Any]]
    metadata: dict[str, Any]


def normalize_state(state: Any) -> str:
    key = str(state or "").strip().lower().replace("-", "_")
    return _STATE_ALIASES.get(key, key or "unknown")


def to_native_state(state: Any) -> str:
    return _NATIVE_STATES.get(normalize_state(state), "unknown")


def to_legacy_state(state: Any) -> str:
    return _LEGACY_STATES.get(normalize_state(state), "unknown")


_TERMINAL_STATES = {"completed", "failed", "canceled", "rejected", "expired"}
_ALLOWED_TRANSITIONS = {
    "unknown": {"submitted", "working", "completed", "failed", "canceled", "rejected", "expired"},
    "submitted": {"working", "completed", "failed", "canceled", "rejected", "expired"},
    "working": {"completed", "failed", "canceled", "rejected", "expired"},
}


def is_terminal_state(state: Any) -> bool:
    return normalize_state(state) in _TERMINAL_STATES


def transition_state(current: Any, requested: Any) -> str:
    old = normalize_state(current)
    new = normalize_state(requested)
    if is_terminal_state(old):
        return old
    allowed = _ALLOWED_TRANSITIONS.get(old, _ALLOWED_TRANSITIONS["unknown"])
    return new if new in allowed else old


def method_kind(method: str) -> tuple[str | None, bool]:
    name = str(method or "").strip()
    lowered = name.lower()
    if lowered in {"tasks/send", "task/send"}:
        return "send", False
    if lowered in {"message/send", "sendmessage"}:
        return "send", True
    if lowered in {"tasks/get", "task/get"}:
        return "get", False
    if lowered == "gettask":
        return "get", True
    if lowered in {"tasks/cancel", "task/cancel"}:
        return "cancel", False
    if lowered == "canceltask":
        return "cancel", True
    if lowered in {"tasks/notify", "task/notify"}:
        return "notify", False
    if lowered == "tasknotification":
        return "notify", True
    return None, False


def sanitize_filename(value: Any, *, max_length: int = 160) -> str:
    raw = str(value or "").replace("/", "_").replace("\\", "_").strip()
    cleaned = _SAFE_FILENAME_RE.sub("_", raw).strip(" ._")
    return (cleaned[:max_length] or "attachment")


def sanitize_media_type(value: Any, *, default: str = "application/octet-stream") -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return default
    cleaned = _SAFE_MIME_RE.sub("", raw)[:120]
    return cleaned or default


def validate_attachment_url(value: Any) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ProtocolError("Unsupported attachment URL scheme; only http(s) references are accepted")
    return url


def _truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n[truncated by A2A max_message_chars]"


def _json_dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
    except TypeError:
        return json.dumps(str(value), ensure_ascii=False)


def _part_media_type(part: dict[str, Any]) -> str:
    metadata = part.get("metadata") if isinstance(part.get("metadata"), dict) else {}
    type_as_mime = part.get("type") if "/" in str(part.get("type", "")) else None
    kind_as_mime = part.get("kind") if "/" in str(part.get("kind", "")) else None
    return sanitize_media_type(
        part.get("mediaType")
        or part.get("mimeType")
        or part.get("mime_type")
        or type_as_mime
        or kind_as_mime
        or metadata.get("mediaType")
        or metadata.get("mimeType")
    )


def _extract_file_info(part: dict[str, Any]) -> dict[str, Any]:
    file_obj = part.get("file") if isinstance(part.get("file"), dict) else {}
    blob_obj = part.get("blob") if isinstance(part.get("blob"), dict) else {}
    source = file_obj or blob_obj or part
    url = source.get("url") or source.get("uri") or part.get("url") or part.get("uri")
    raw = source.get("raw") or source.get("bytes") or part.get("raw") or part.get("bytes")
    raw_length = len(raw) if isinstance(raw, str) else 0
    return {
        "filename": sanitize_filename(source.get("filename") or source.get("name") or part.get("filename") or part.get("name")),
        "media_type": sanitize_media_type(source.get("mediaType") or source.get("mimeType") or source.get("mime_type") or _part_media_type(part)),
        "url": url,
        "raw": raw,
        "raw_length": raw_length,
        "size": source.get("size") or source.get("sizeBytes") or source.get("size_bytes") or part.get("size") or part.get("sizeBytes"),
        "sha256": str(source.get("sha256") or part.get("sha256") or "").strip(),
    }


def normalize_inbound_message(
    message: dict[str, Any],
    *,
    max_message_chars: int,
    max_parts: int,
    max_raw_part_bytes: int,
) -> NormalizedMessage:
    if not isinstance(message, dict):
        raise ProtocolError("Invalid message")
    parts = message.get("parts")
    if parts is None:
        parts = message.get("content")
    if parts is None:
        parts = []
    if not isinstance(parts, list):
        raise ProtocolError("Invalid message parts")
    if not parts:
        raise ProtocolError("Empty message")
    if len(parts) > max_parts:
        raise ProtocolError(f"Too many message parts: max {max_parts}")

    text_blocks: list[str] = []
    structured_blocks: list[str] = []
    attachment_lines: list[str] = []
    safe_parts: list[dict[str, Any]] = []

    for index, part in enumerate(parts, start=1):
        if not isinstance(part, dict):
            continue
        part_type = str(part.get("type") or part.get("kind") or "").strip().lower()
        metadata = part.get("metadata") if isinstance(part.get("metadata"), dict) else {}

        if "text" in part and isinstance(part.get("text"), (str, int, float, bool)):
            text = str(part.get("text", ""))
            if text:
                text_blocks.append(text)
                safe_parts.append({"type": "text", "text": text})
            continue

        if "data" in part or part_type in {"data", "json"} or metadata.get("mediaType") == "application/json":
            data = part.get("data", part.get("json", part))
            rendered = _json_dumps(data)
            structured_blocks.append(f"[A2A structured data] #{index}\n```json\n{rendered}\n```")
            safe_parts.append({"type": "json", "data": data})
            continue

        if any(key in part for key in ("url", "uri", "file", "blob", "raw", "bytes")) or part_type in {"file", "image", "audio", "video", "artifact", "artifact_ref"}:
            info = _extract_file_info(part)
            if info["url"]:
                info["url"] = validate_attachment_url(info["url"])
            if info["raw_length"] and info["raw_length"] > max_raw_part_bytes:
                raise ProtocolError(f"raw part exceeds max_raw_part_bytes ({max_raw_part_bytes})")
            safe: dict[str, Any] = {
                "type": "file",
                "filename": info["filename"],
                "mediaType": info["media_type"],
            }
            line = f"{index}. file: {info['filename']} ({info['media_type']})"
            if info["url"]:
                safe["url"] = info["url"]
                parsed_url = urlparse(info["url"])
                line += f"\n   url_origin: {parsed_url.scheme}://{parsed_url.netloc}"
            if info["size"] not in (None, ""):
                safe["size"] = info["size"]
                line += f"\n   size: {info['size']} bytes"
            if info["sha256"]:
                safe["sha256"] = info["sha256"][:128]
                line += f"\n   sha256: {safe['sha256']}"
            if info["raw_length"]:
                safe["rawBytesOmitted"] = True
                safe["rawLength"] = info["raw_length"]
                line += f"\n   inline raw bytes omitted: {info['raw_length']} chars"
            attachment_lines.append(line)
            safe_parts.append(safe)
            continue

        rendered = _json_dumps(part)
        structured_blocks.append(f"[A2A unsupported part #{index} represented as JSON]\n```json\n{rendered}\n```")
        safe_parts.append({"type": "json", "data": part})

    prompt_blocks = [block for block in text_blocks if block.strip()]
    prompt_blocks.extend(structured_blocks)
    if attachment_lines:
        prompt_blocks.append("[A2A attachment references]\n" + "\n".join(attachment_lines))
    prompt_text = _truncate_text("\n".join(prompt_blocks).strip(), max_message_chars)
    if not prompt_text:
        raise ProtocolError("Empty message")
    return NormalizedMessage(prompt_text=prompt_text, safe_parts=safe_parts, metadata={"a2a_parts": safe_parts})


def _text_part(text: str, *, native: bool = False) -> dict[str, Any]:
    if native:
        return {"kind": "text", "text": text}
    return {"type": "text", "text": text}


def _native_artifacts(task_id: str, parts_artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for index, artifact in enumerate(parts_artifacts):
        if not isinstance(artifact, dict):
            continue
        parts = []
        for part in artifact.get("parts", []):
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text" and "kind" not in part:
                parts.append({"kind": "text", "text": str(part.get("text", ""))})
            elif "text" in part and "kind" not in part:
                parts.append({"kind": "text", "text": str(part.get("text", ""))})
            else:
                native_part = dict(part)
                if "type" in native_part and "kind" not in native_part:
                    native_part["kind"] = native_part.pop("type")
                parts.append(native_part)
        artifacts.append({
            "artifactId": str(artifact.get("artifactId") or artifact.get("id") or f"{task_id}-artifact-{index}"),
            "name": str(artifact.get("name") or f"artifact-{index}"),
            "parts": parts,
        })
    return artifacts


def is_native_response_wrapper(value: Any) -> bool:
    return isinstance(value, dict) and (isinstance(value.get("task"), dict) or isinstance(value.get("message"), dict))


def wrap_native_rpc_result(value: dict[str, Any]) -> dict[str, Any]:
    if is_native_response_wrapper(value):
        return value
    if isinstance(value, dict) and value.get("kind") == "message":
        return {"message": value}
    return {"task": value}


def build_task_result(
    task_id: str,
    state: Any,
    text: str = "",
    *,
    native: bool = False,
    context_id: str = "",
    artifacts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    canonical_state = normalize_state(state)
    legacy_state = to_legacy_state(canonical_state)
    parts_artifacts = artifacts if artifacts is not None else [{"parts": [_text_part(text, native=native)], "index": 0}]
    if not native:
        result = {"id": task_id, "status": {"state": legacy_state}}
        if text or artifacts is not None:
            result["artifacts"] = parts_artifacts
        return result
    task = {
        "kind": "task",
        "id": task_id,
        "contextId": context_id or task_id,
        "status": {"state": to_native_state(canonical_state)},
    }
    if text:
        task["status"]["message"] = {
            "kind": "message",
            "messageId": f"{task_id}-status",
            "role": "agent",
            "parts": [_text_part(text, native=True)],
        }
    if parts_artifacts and (text or artifacts is not None):
        task["artifacts"] = _native_artifacts(task_id, parts_artifacts)
    return task


def _unwrap_task(result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    if isinstance(result.get("task"), dict):
        return result["task"]
    if isinstance(result.get("message"), dict):
        return result["message"]
    return result


def extract_task_id(result: dict[str, Any], fallback: str = "") -> str:
    task = _unwrap_task(result)
    return str(task.get("id") or task.get("taskId") or task.get("task_id") or fallback or "")


def extract_task_state(result: dict[str, Any]) -> str:
    task = _unwrap_task(result)
    if isinstance(task, dict) and task.get("kind") == "message":
        return "completed"
    status = task.get("status", {}) if isinstance(task, dict) else {}
    if isinstance(status, dict):
        return normalize_state(status.get("state"))
    return normalize_state(task.get("state")) if isinstance(task, dict) else "unknown"


def _part_summary(part: dict[str, Any]) -> str:
    if "text" in part and isinstance(part.get("text"), (str, int, float, bool)):
        return str(part.get("text", ""))
    if part.get("type") == "text" or part.get("kind") == "text":
        return str(part.get("text", ""))
    file_obj = part.get("file") if isinstance(part.get("file"), dict) else None
    if file_obj:
        name = file_obj.get("name") or file_obj.get("filename") or "attachment"
        mime = file_obj.get("mimeType") or file_obj.get("mediaType") or "application/octet-stream"
        return f"[A2A attachment: {name} ({mime})]"
    if (part.get("type") or part.get("kind")) in {"file", "image", "audio", "video"}:
        name = part.get("filename") or part.get("name") or "attachment"
        mime = part.get("mediaType") or part.get("mimeType") or part.get("type") or part.get("kind")
        return f"[A2A attachment: {name} ({mime})]"
    if "data" in part:
        return "[A2A structured data]\n```json\n" + _json_dumps(part.get("data")) + "\n```"
    if part:
        return "[A2A non-text artifact]\n```json\n" + _json_dumps(part) + "\n```"
    return ""


def extract_response_text(result: dict[str, Any]) -> str:
    task = _unwrap_task(result)
    chunks: list[str] = []
    if isinstance(task.get("parts"), list):
        for part in task["parts"]:
            if isinstance(part, dict):
                summary = _part_summary(part)
                if summary:
                    chunks.append(summary)
    status = task.get("status", {}) if isinstance(task, dict) else {}
    if isinstance(status, dict) and isinstance(status.get("message"), dict):
        for part in status["message"].get("parts", []):
            if isinstance(part, dict):
                summary = _part_summary(part)
                if summary:
                    chunks.append(summary)
    if not chunks:
        for artifact in task.get("artifacts", []) if isinstance(task, dict) else []:
            if not isinstance(artifact, dict):
                continue
            for part in artifact.get("parts", []):
                if isinstance(part, dict):
                    summary = _part_summary(part)
                    if summary:
                        chunks.append(summary)
    return "\n".join(chunk for chunk in chunks if chunk).strip()
