"""A2A HTTP server — runs in a background thread, no asyncio.

Handles inbound A2A JSON-RPC requests. Messages are queued and picked up
by the pre_llm_call hook; responses are captured by post_llm_call and
returned to the caller.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
import time
import uuid
import builtins
import re
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from threading import Event, Lock
from collections import OrderedDict
from typing import Optional
import urllib.request
import urllib.error

from .config import get_security_config, get_server_config
from .protocol import (
    ProtocolError,
    build_task_result,
    extract_response_text,
    extract_task_state,
    method_kind,
    normalize_inbound_message,
    transition_state,
    wrap_native_rpc_result,
)
from . import task_store
from .security import RateLimiter, audit, filter_outbound, sanitize_inbound

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 41731
_TASK_CACHE_MAX = 1000
_MAX_PENDING = 10
_RESPONSE_TIMEOUT = 120  # seconds to wait for agent response
_STATE_KEY = "_hermes_a2a_runtime_state"

try:
    from hermes_cli import __version__ as HERMES_VERSION
except Exception:
    HERMES_VERSION = "0.0.0"


_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.:@-]+")


def _safe_id(value: object, *, fallback: str | None = None, max_length: int = 96) -> str:
    raw = str(value or "").strip()
    if not raw and fallback is not None:
        raw = fallback
    raw = _SAFE_ID_RE.sub("-", raw).strip("-._:@")
    return (raw[:max_length] or (fallback or str(uuid.uuid4())))


class _PendingTask:
    __slots__ = ("task_id", "text", "metadata", "response", "ready", "created_at", "state", "owner")

    def __init__(self, task_id: str, text: str, metadata: dict, owner: str = ""):
        self.task_id = task_id
        self.text = text
        self.metadata = metadata
        self.response: Optional[str] = None
        self.ready = Event()
        self.created_at = time.time()
        self.state = "submitted"
        self.owner = owner


class TaskQueue:
    """Thread-safe queue for pending A2A tasks."""

    def __init__(self):
        self._pending: OrderedDict[str, _PendingTask] = OrderedDict()
        self._completed: OrderedDict[str, _PendingTask] = OrderedDict()
        self._processing: set[str] = set()
        self._lock = Lock()

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def enqueue(self, task_id: str, text: str, metadata: dict, owner: str = "") -> _PendingTask | None:
        task = _PendingTask(task_id, text, metadata, owner)
        with self._lock:
            if task_id in self._pending or task_id in self._completed:
                return None
            self._pending[task_id] = task
            while len(self._pending) > _TASK_CACHE_MAX:
                _, old = self._pending.popitem(last=False)
                self._processing.discard(old.task_id)
                old.state = "failed"
                old.response = "(dropped — queue overflow)"
                old.ready.set()
                self._completed[old.task_id] = old
        return task

    def drain_pending(self, exclude: set[str] | None = None) -> list[_PendingTask]:
        with self._lock:
            skip = set(exclude or ()) | self._processing
            return [t for t in self._pending.values() if t.task_id not in skip]

    def get_pending(self, task_id: str) -> _PendingTask | None:
        with self._lock:
            if task_id in self._processing:
                return None
            return self._pending.get(task_id)

    def mark_processing(self, task_id: str) -> None:
        with self._lock:
            task = self._pending.get(task_id)
            if task:
                task.state = "processing"
                self._processing.add(task_id)

    def _cache_completed(self, task: _PendingTask) -> None:
        self._completed[task.task_id] = task
        while len(self._completed) > _TASK_CACHE_MAX:
            self._completed.popitem(last=False)

    def complete(self, task_id: str, response: str) -> None:
        with self._lock:
            if task_id in self._completed:
                return
            self._processing.discard(task_id)
            task = self._pending.pop(task_id, None)
            if task:
                task.state = transition_state(task.state, "completed")
                if task.state == "completed":
                    task.response = response
                task.ready.set()
                self._cache_completed(task)

    def fail(self, task_id: str, response: str) -> None:
        with self._lock:
            if task_id in self._completed:
                return
            self._processing.discard(task_id)
            task = self._pending.pop(task_id, None)
            if task:
                task.state = transition_state(task.state, "failed")
                if task.state == "failed":
                    task.response = response
                task.ready.set()
                self._cache_completed(task)

    def cancel(self, task_id: str) -> None:
        with self._lock:
            if task_id in self._completed:
                return
            self._processing.discard(task_id)
            task = self._pending.pop(task_id, None)
            if task:
                task.state = transition_state(task.state, "canceled")
                if task.state == "canceled":
                    task.response = "(canceled)"
                task.ready.set()
                self._cache_completed(task)

    def owner_for(self, task_id: str) -> str:
        with self._lock:
            task = self._pending.get(task_id) or self._completed.get(task_id)
            return getattr(task, "owner", "") if task else ""

    def get_status(self, task_id: str) -> dict:
        with self._lock:
            task = self._pending.get(task_id)
            if task:
                return {"state": task.state}
            task = self._completed.get(task_id)
            if task:
                data = {"state": task.state}
                if task.response is not None:
                    data["response"] = truncate_response_text(filter_outbound(task.response))
                return data
        return {"state": "unknown"}


def _runtime_state() -> dict:
    """Return process-wide A2A runtime state that survives plugin reloads."""
    state = getattr(builtins, _STATE_KEY, None)
    if not isinstance(state, dict):
        state = {}
        setattr(builtins, _STATE_KEY, state)

    queue = state.get("task_queue")
    if not _is_usable_task_queue(queue):
        state["task_queue"] = TaskQueue()
    state.setdefault("server", None)
    state.setdefault("thread", None)
    state.setdefault("owner_module", __name__)
    return state


def _is_usable_task_queue(queue) -> bool:
    """Accept queue objects created before plugin reload changed class identity."""
    return all(
        callable(getattr(queue, name, None))
        for name in (
            "pending_count",
            "enqueue",
            "drain_pending",
            "mark_processing",
            "complete",
            "cancel",
            "get_status",
        )
    )


task_queue = _runtime_state()["task_queue"]




def response_limit() -> int:
    try:
        return get_security_config().max_response_chars
    except Exception:
        return 100_000


def truncate_response_text(text: object, max_chars: int | None = None) -> str:
    raw = text if isinstance(text, str) else str(text)
    limit = max_chars if isinstance(max_chars, int) and max_chars > 0 else response_limit()
    if len(raw) <= limit:
        return raw
    return raw[:limit] + "\n[truncated by A2A max_response_chars]"

def _get_pending_task(queue, task_id: str):
    getter = getattr(queue, "get_pending", None)
    if callable(getter):
        return getter(task_id)
    pending = queue.drain_pending(exclude=set())
    for task in pending:
        if getattr(task, "task_id", None) == task_id:
            return task
    return None


def get_runtime_state() -> dict:
    """Expose the process-wide runtime state to the plugin loader."""
    return _runtime_state()


def set_runtime_server(server, thread) -> None:
    state = _runtime_state()
    state["server"] = server
    state["thread"] = thread
    state["owner_module"] = __name__


def clear_runtime_server(server=None) -> None:
    state = _runtime_state()
    if server is not None and state.get("server") is not server:
        return
    state["server"] = None
    state["thread"] = None


def _trigger_webhook(task_id: str = ""):
    """POST to the internal webhook to trigger an agent turn."""
    secret = os.getenv("A2A_WEBHOOK_SECRET", "")
    if not secret:
        return

    port = int(os.getenv("WEBHOOK_PORT", "47644"))
    body = json.dumps({"event_type": "a2a_inbound", "task_id": task_id}).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/webhooks/a2a_trigger",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": sig,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            logger.debug("[A2A] Webhook trigger: %d", resp.status)
    except Exception as e:
        logger.debug("[A2A] Webhook trigger failed: %s", e)


def _task_failed(task_id: str, message: str, *, native: bool = False, context_id: str = "") -> dict:
    return build_task_result(task_id, "failed", message, native=native, context_id=context_id)




def _background_requested(params: dict, message: dict, metadata: dict) -> bool:
    return any(bool(value) for value in (
        params.get("background"),
        params.get("notify"),
        message.get("background"),
        message.get("notify"),
        metadata.get("background"),
        metadata.get("notify"),
    ))


def _notify_text(params: dict) -> str:
    if isinstance(params.get("message"), dict):
        text = extract_response_text(params.get("message", {}))
        if text:
            return text
    if isinstance(params.get("status"), dict):
        text = extract_response_text({"status": params.get("status")})
        if text:
            return text
    if "response" in params:
        return str(params.get("response") or "")
    return ""


def _notify_state(params: dict) -> str:
    if "state" in params:
        return str(params.get("state") or "completed")
    if isinstance(params.get("status"), dict):
        return extract_task_state({"status": params.get("status")})
    return "completed"


class A2ARequestHandler(BaseHTTPRequestHandler):
    """Handles A2A HTTP requests."""

    server: "A2AServer"

    def log_message(self, format, *args):
        logger.debug("A2A HTTP: %s", format % args)

    def _send_json(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _check_auth(self) -> bool:
        token = self.server.auth_token
        if not token:
            if self.server.require_auth:
                logger.warning("[A2A] Rejecting unauthenticated request because A2A_REQUIRE_AUTH is enabled")
                return False
            remote = self.client_address[0]
            allowed = remote in ("127.0.0.1", "::1")
            if allowed:
                logger.warning(
                    "[A2A] Allowing unauthenticated localhost request; set "
                    "A2A_AUTH_TOKEN and A2A_REQUIRE_AUTH=true in production"
                )
            return allowed
        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return False
        return hmac.compare_digest(auth_header[7:].strip(), token)

    def _is_native_push_payload(self, body: Any) -> bool:
        return isinstance(body, dict) and any(key in body for key in ("task", "message", "statusUpdate", "artifactUpdate"))

    def do_GET(self) -> None:
        if self.path == "/.well-known/agent.json":
            self._send_json(self.server.build_agent_card())
        elif self.path == "/health":
            self._send_json({
                "status": "ok",
                "agent": self.server.agent_name,
                "version": HERMES_VERSION,
            })
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_POST(self) -> None:
        bearer_auth_ok = self._check_auth()

        if not self.server.limiter.allow(self.client_address[0]):
            audit.log("rate_limited", {"client": self.client_address[0]})
            self._send_json(
                {"jsonrpc": "2.0", "error": {"code": -32000, "message": "Rate limit exceeded"}, "id": None},
                429,
            )
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
        except (ValueError, TypeError):
            self._send_json(
                {"jsonrpc": "2.0", "error": {"code": -32600, "message": "Invalid Content-Length"}, "id": None},
                400,
            )
            return

        if length <= 0 or length > self.server.max_request_bytes:
            self._send_json(
                {"jsonrpc": "2.0", "error": {"code": -32600, "message": f"Content-Length must be 1-{self.server.max_request_bytes}, got {length}"}, "id": None},
                413 if length > self.server.max_request_bytes else 400,
            )
            return

        try:
            body = json.loads(self.rfile.read(length))
        except Exception:
            self._send_json(
                {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": None},
                400,
            )
            return

        if not isinstance(body, dict):
            self._send_json({"jsonrpc": "2.0", "error": {"code": -32600, "message": "Invalid Request"}, "id": None}, 400)
            return

        is_native_push = self._is_native_push_payload(body)
        if not bearer_auth_ok and not is_native_push:
            self._send_json(
                {"jsonrpc": "2.0", "error": {"code": -32000, "message": "Unauthorized"}, "id": None},
                401,
            )
            return

        if is_native_push:
            status, result = self._handle_native_push_payload(body)
            self._send_json(result, status)
            return

        method = body.get("method", "")
        params = body.get("params", {})
        rpc_id = body.get("id")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            self._send_json({"jsonrpc": "2.0", "error": {"code": -32602, "message": "Invalid params"}, "id": rpc_id}, 400)
            return

        audit.log("rpc_request", {"method": method, "client": self.client_address[0]})
        kind, native = method_kind(method)

        if kind == "send":
            result = self._handle_task_send(params, native=native)
        elif kind == "get":
            raw_tid = params.get("id") or params.get("taskId") or params.get("task_id")
            if not raw_tid:
                self._send_json({"jsonrpc": "2.0", "error": {"code": -32602, "message": "Missing task id"}, "id": rpc_id}, 400)
                return
            tid = _safe_id(raw_tid, fallback="")
            if not self._owns_task(tid):
                self._send_json({"jsonrpc": "2.0", "error": {"code": -32003, "message": "Forbidden"}, "id": rpc_id}, 403)
                return
            status = task_queue.get_status(tid)
            result = build_task_result(tid, status["state"], status.get("response", ""), native=native, context_id=tid)
        elif kind == "cancel":
            raw_tid = params.get("id") or params.get("taskId") or params.get("task_id")
            if not raw_tid:
                self._send_json({"jsonrpc": "2.0", "error": {"code": -32602, "message": "Missing task id"}, "id": rpc_id}, 400)
                return
            tid = _safe_id(raw_tid, fallback="")
            if not self._owns_task(tid):
                self._send_json({"jsonrpc": "2.0", "error": {"code": -32003, "message": "Forbidden"}, "id": rpc_id}, 403)
                return
            task_queue.cancel(tid)
            task_store.update_task(tid, state="canceled", response="(canceled)")
            result = build_task_result(tid, "canceled", "(canceled)", native=native, context_id=tid)
        elif kind == "notify":
            raw_tid = params.get("id") or params.get("taskId") or params.get("task_id")
            if not raw_tid:
                self._send_json({"jsonrpc": "2.0", "error": {"code": -32602, "message": "Missing task id"}, "id": rpc_id}, 400)
                return
            result = self._handle_task_notify(params, native=native)
            if result is None:
                self._send_json({"jsonrpc": "2.0", "error": {"code": -32001, "message": "Task not found"}, "id": rpc_id}, 404)
                return
        else:
            self._send_json({
                "jsonrpc": "2.0",
                "error": {"code": -32601, "message": f"Method not found: {method}"},
                "id": rpc_id,
            })
            return

        if native and kind in {"send", "get", "cancel"}:
            result = wrap_native_rpc_result(result)
        self._send_json({"jsonrpc": "2.0", "result": result, "id": rpc_id})

    def _owner_id(self) -> str:
        return f"{self.client_address[0]}:{bool(self.server.auth_token)}"

    def _owns_task(self, task_id: str) -> bool:
        owner_getter = getattr(task_queue, "owner_for", None)
        if not callable(owner_getter):
            status = task_queue.get_status(task_id)
            return status.get("state") == "unknown"
        owner = owner_getter(task_id)
        return not owner or hmac.compare_digest(owner, self._owner_id())

    def _task_id_from_params(self, params: dict, message: dict) -> str:
        return _safe_id(
            message.get("taskId")
            or message.get("task_id")
            or params.get("id")
            or params.get("taskId")
            or params.get("task_id")
            or message.get("messageId")
            or message.get("message_id"),
            fallback=str(uuid.uuid4()),
        )

    def _handle_task_send(self, params: dict, *, native: bool = False) -> dict:
        message = params.get("message", {})
        if not isinstance(message, dict):
            task_id = _safe_id(params.get("id") or params.get("taskId") or params.get("task_id"), fallback=str(uuid.uuid4()))
            return _task_failed(task_id, "Invalid message", native=native)

        task_id = self._task_id_from_params(params, message)
        context_id = _safe_id(message.get("contextId") or message.get("context_id") or params.get("contextId") or params.get("context_id"), fallback="", max_length=96)

        try:
            normalized = normalize_inbound_message(
                message,
                max_message_chars=self.server.max_message_chars,
                max_parts=self.server.max_parts,
                max_raw_part_bytes=self.server.max_raw_part_bytes,
            )
        except ProtocolError as exc:
            return _task_failed(task_id, str(exc), native=native, context_id=context_id)

        user_text = sanitize_inbound(normalized.prompt_text, max_length=self.server.max_message_chars)
        metadata = message.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        metadata = dict(metadata)
        metadata.update(normalized.metadata)
        if context_id:
            metadata["context_id"] = context_id
        message_id = message.get("messageId") or message.get("message_id")
        if message_id:
            metadata["message_id"] = _safe_id(message_id, fallback="", max_length=96)
        if "sender_name" not in metadata:
            from_field = params.get("from") or (params.get("sender") if isinstance(params.get("sender"), dict) else {}).get("name")
            metadata["sender_name"] = from_field or metadata.get("agent_name", f"agent-{self.client_address[0]}")
        raw_name = str(metadata.get("sender_name") or "")
        metadata["sender_name"] = "".join(c for c in raw_name if c.isalnum() or c in "-_.@ ")[:64] or "remote"
        metadata["reply_to_task_id"] = _safe_id(
            metadata.get("reply_to_task_id") or message.get("replyToTaskId") or message.get("reply_to_task_id") or params.get("reply_to_task_id") or "",
            fallback="",
            max_length=64,
        ) if (metadata.get("reply_to_task_id") or message.get("replyToTaskId") or message.get("reply_to_task_id") or params.get("reply_to_task_id")) else ""

        audit.log("task_received", {"task_id": task_id, "length": len(user_text), "parts": len(normalized.safe_parts)})

        if task_queue.pending_count() >= _MAX_PENDING:
            return _task_failed(task_id, "Agent busy — too many pending tasks", native=native, context_id=context_id)

        background = _background_requested(params, message, metadata)
        try:
            task = task_queue.enqueue(task_id, user_text, metadata, owner=self._owner_id())
        except TypeError:
            task = task_queue.enqueue(task_id, user_text, metadata)
            if task is not None:
                try:
                    task.owner = self._owner_id()
                except Exception:
                    pass
        if task is None:
            if not self._owns_task(task_id):
                return _task_failed(task_id, "Forbidden — task id belongs to another caller", native=native, context_id=context_id or task_id)
            status = task_queue.get_status(task_id)
            return build_task_result(task_id, status["state"], status.get("response", ""), native=native, context_id=context_id or task_id)

        if background:
            task_store.create_task(
                task_id,
                direction="inbound",
                agent_name=str(metadata.get("sender_name") or "remote"),
                url=f"http://{self.client_address[0]}",
                state="submitted",
                context_id=context_id,
                local_task_id=task_id,
                notify_requested=bool(metadata.get("notify") or params.get("notify")),
            )

        threading.Thread(target=_trigger_webhook, args=(task_id,), daemon=True).start()

        if background:
            return build_task_result(task_id, "submitted", "(submitted — poll with GetTask/tasks/get)", native=native, context_id=context_id)

        task.ready.wait(timeout=_RESPONSE_TIMEOUT)

        if task.response is None:
            return build_task_result(task_id, "working", "(processing — poll with tasks/get)", native=native, context_id=context_id)

        filtered = truncate_response_text(filter_outbound(task.response), self.server.max_response_chars)
        audit.log("task_completed", {"task_id": task_id, "response_length": len(filtered)})

        state = task.state if task.state in {"completed", "failed", "canceled"} else "completed"
        task_store.update_task(task_id, state=state, response=filtered)
        return build_task_result(task_id, state, filtered, native=native, context_id=context_id)

    def _handle_task_notify(self, params: dict, *, native: bool = False) -> dict | None:
        raw_tid = params.get("id") or params.get("taskId") or params.get("task_id")
        agent_name = str(params.get("from") or params.get("agent_name") or "").strip()
        record = task_store.find_task(str(raw_tid), agent_name=agent_name) or (task_store.find_task(str(raw_tid)) if not agent_name else None)
        if not record:
            return None
        text = truncate_response_text(filter_outbound(_notify_text(params)), self.server.max_response_chars)
        state = _notify_state(params)
        updated = self._update_background_record(record, state=state, text=text, agent_name=agent_name)
        return build_task_result(updated["task_id"], updated.get("state", state), updated.get("response", text), native=native, context_id=updated.get("context_id") or updated["task_id"])

    def _update_background_record(self, record: dict, *, state: str, text: str, agent_name: str = "") -> dict:
        old_state = str(record.get("state") or "")
        updated = task_store.update_task(record["task_id"], state=state, response=text) or record
        if updated.get("state") != old_state or (text and updated.get("response") == text and not old_state):
            try:
                from .persistence import update_exchange
                update_exchange(agent_name=updated.get("agent_name") or agent_name or "remote", task_id=updated["task_id"], inbound_text=updated.get("response", text))
            except Exception:
                logger.debug("[A2A] Failed to update notified exchange", exc_info=True)
            threading.Thread(target=_trigger_webhook, args=(updated["task_id"],), daemon=True).start()
        return updated

    def _native_push_task_id(self, payload: dict) -> str:
        if isinstance(payload.get("task"), dict):
            return _safe_id(payload["task"].get("id") or payload["task"].get("taskId"), fallback="")
        if isinstance(payload.get("statusUpdate"), dict):
            return _safe_id(payload["statusUpdate"].get("taskId") or payload["statusUpdate"].get("id"), fallback="")
        if isinstance(payload.get("artifactUpdate"), dict):
            return _safe_id(payload["artifactUpdate"].get("taskId") or payload["artifactUpdate"].get("id"), fallback="")
        if isinstance(payload.get("message"), dict):
            return _safe_id(payload["message"].get("taskId") or payload["message"].get("task_id") or payload["message"].get("id"), fallback="")
        return ""

    def _native_push_state(self, payload: dict) -> str:
        if isinstance(payload.get("task"), dict):
            return extract_task_state(payload["task"])
        if isinstance(payload.get("statusUpdate"), dict):
            status = payload["statusUpdate"].get("status", {})
            return extract_task_state({"status": status}) if isinstance(status, dict) else "completed"
        if isinstance(payload.get("artifactUpdate"), dict):
            return "working"
        if isinstance(payload.get("message"), dict):
            return "working"
        return "working"

    def _native_push_text(self, payload: dict) -> str:
        if isinstance(payload.get("task"), dict):
            return extract_response_text(payload["task"])
        if isinstance(payload.get("message"), dict):
            return extract_response_text({"message": payload["message"]})
        if isinstance(payload.get("statusUpdate"), dict):
            update = payload["statusUpdate"]
            status = update.get("status", {}) if isinstance(update, dict) else {}
            return extract_response_text({"status": status})
        if isinstance(payload.get("artifactUpdate"), dict):
            artifact = payload["artifactUpdate"].get("artifact", {})
            return extract_response_text({"artifacts": [artifact]}) if isinstance(artifact, dict) else ""
        return ""

    def _handle_native_push_payload(self, payload: dict) -> tuple[int, dict]:
        raw_tid = self._native_push_task_id(payload)
        if not raw_tid:
            return 404, {"error": "Task not found"}
        record = task_store.find_task(raw_tid)
        if not record:
            return 404, {"error": "Task not found"}
        if record.get("direction") != "outbound" or not record.get("notify_requested"):
            return 404, {"error": "Task not found"}
        expected_token = str(record.get("push_token") or "").strip()
        if not expected_token:
            return 401, {"error": {"message": "Unauthorized"}}
        supplied_token = str(self.headers.get("X-A2A-Notification-Token", "")).strip()
        if not hmac.compare_digest(supplied_token, expected_token):
            return 401, {"error": {"message": "Unauthorized"}}
        text = truncate_response_text(filter_outbound(self._native_push_text(payload)), self.server.max_response_chars)
        state = self._native_push_state(payload)
        updated = self._update_background_record(record, state=state, text=text, agent_name=str(record.get("agent_name") or ""))
        return 200, {"status": "ok", "task_id": updated["task_id"], "state": updated.get("state", state)}


class A2AServer(ThreadingHTTPServer):
    """Threaded HTTP server with A2A configuration.

    Each request runs in its own thread so tasks/send can block waiting
    for agent response without starving health checks and agent card requests.
    """

    daemon_threads = True

    def __init__(self, host: str, port: int):
        server_cfg = get_server_config()
        security_cfg = get_security_config()
        self.agent_name = os.getenv("A2A_AGENT_NAME", "hermes-agent")
        self.agent_description = os.getenv("A2A_AGENT_DESCRIPTION", "A self-improving AI agent powered by Hermes")
        self.auth_token = os.getenv("A2A_AUTH_TOKEN", "")
        self.require_auth = server_cfg.require_auth
        self.public_url = server_cfg.public_url
        self.max_message_chars = security_cfg.max_message_chars
        self.max_response_chars = security_cfg.max_response_chars
        self.max_request_bytes = security_cfg.max_request_bytes
        self.max_raw_part_bytes = security_cfg.max_raw_part_bytes
        self.max_parts = security_cfg.max_parts
        self.limiter = RateLimiter(max_requests=security_cfg.rate_limit_per_minute)
        if self.require_auth and not self.auth_token:
            logger.warning("[A2A] A2A_REQUIRE_AUTH is enabled but A2A_AUTH_TOKEN is missing; POST requests will be rejected")
        super().__init__((host, port), A2ARequestHandler)

    def build_agent_card(self) -> dict:
        public_url = self.public_url
        if not public_url:
            host, port = self.server_address
            public_url = f"http://{host}:{port}"
        return {
            "name": self.agent_name,
            "description": self.agent_description,
            "url": public_url,
            "version": HERMES_VERSION,
            "protocol": "a2a",
            "protocolVersion": "0.3.0",
            "preferredTransport": "JSONRPC",
            "supportedInterfaces": [
                {
                    "url": public_url,
                    "protocolBinding": "JSONRPC",
                    "protocolVersion": "0.3.0",
                }
            ],
            "additionalInterfaces": [
                {
                    "url": public_url,
                    "transport": "JSONRPC",
                }
            ],
            "defaultInputModes": ["text/plain", "application/json"],
            "defaultOutputModes": ["text/plain", "application/json"],
            "capabilities": {
                "streaming": False,
                "pushNotifications": bool(self.public_url and self.auth_token),
                "multiTurn": False,
                "stateTransitionHistory": False,
                "structuredMetadata": True,
                "extensions": [
                    {
                        "uri": "https://github.com/tickernelz/hermes-a2a/extensions/multimodal-reference/v1",
                        "description": "Accepts non-text parts as safe prompt references; remote URLs are not fetched automatically.",
                        "required": False,
                    }
                ],
            },
            "skills": [
                {
                    "id": "general",
                    "name": "General Assistant",
                    "description": "General-purpose AI assistant with tool use, web search, and more",
                    "tags": ["assistant", "hermes", "tools"],
                    "inputModes": ["text/plain", "application/json"],
                    "outputModes": ["text/plain", "application/json"],
                }
            ],
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                }
            } if self.auth_token else {},
            "security": [{"bearerAuth": []}] if self.auth_token else [],
            "authentication": {
                "schemes": ["bearer"] if self.auth_token else [],
            },
        }
