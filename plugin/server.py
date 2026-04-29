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
from .security import RateLimiter, audit, filter_outbound, sanitize_inbound

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8081
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
    __slots__ = ("task_id", "text", "metadata", "response", "ready", "created_at", "state")

    def __init__(self, task_id: str, text: str, metadata: dict):
        self.task_id = task_id
        self.text = text
        self.metadata = metadata
        self.response: Optional[str] = None
        self.ready = Event()
        self.created_at = time.time()
        self.state = "pending"


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

    def enqueue(self, task_id: str, text: str, metadata: dict) -> _PendingTask | None:
        task = _PendingTask(task_id, text, metadata)
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
            self._processing.discard(task_id)
            task = self._pending.pop(task_id, None)
            if task:
                task.state = "completed"
                task.response = response
                task.ready.set()
                self._cache_completed(task)

    def fail(self, task_id: str, response: str) -> None:
        with self._lock:
            self._processing.discard(task_id)
            task = self._pending.pop(task_id, None)
            if task:
                task.state = "failed"
                task.response = response
                task.ready.set()
                self._cache_completed(task)

    def cancel(self, task_id: str) -> None:
        with self._lock:
            self._processing.discard(task_id)
            task = self._pending.pop(task_id, None)
            if task:
                task.state = "canceled"
                task.response = "(canceled)"
                task.ready.set()
                self._cache_completed(task)

    def get_status(self, task_id: str) -> dict:
        with self._lock:
            task = self._pending.get(task_id)
            if task:
                return {"state": task.state if task.state != "pending" else "working"}
            task = self._completed.get(task_id)
            if task:
                data = {"state": task.state}
                if task.response is not None:
                    data["response"] = filter_outbound(task.response)
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

    port = int(os.getenv("WEBHOOK_PORT", "8644"))
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


def _task_failed(task_id: str, message: str) -> dict:
    return {
        "id": task_id,
        "status": {"state": "failed"},
        "artifacts": [{"parts": [{"type": "text", "text": message}], "index": 0}],
    }


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
                    "A2A_AUTH_TOKEN and A2A_REQUIRE_AUTH=true"
                )
            return allowed
        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return False
        return hmac.compare_digest(auth_header[7:].strip(), token)

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
        if not self._check_auth():
            self._send_json(
                {"jsonrpc": "2.0", "error": {"code": -32000, "message": "Unauthorized"}, "id": None},
                401,
            )
            return

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

        if length <= 0 or length > 65536:
            self._send_json(
                {"jsonrpc": "2.0", "error": {"code": -32600, "message": f"Content-Length must be 1-65536, got {length}"}, "id": None},
                413 if length > 65536 else 400,
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

        method = body.get("method", "")
        params = body.get("params", {})
        rpc_id = body.get("id")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            self._send_json({"jsonrpc": "2.0", "error": {"code": -32602, "message": "Invalid params"}, "id": rpc_id}, 400)
            return

        audit.log("rpc_request", {"method": method, "client": self.client_address[0]})

        if method == "tasks/send":
            result = self._handle_task_send(params)
        elif method == "tasks/get":
            tid = _safe_id(params.get("id", ""), fallback="")
            status = task_queue.get_status(tid)
            result = {"id": tid, "status": {"state": status["state"]}}
            if status.get("response"):
                result["artifacts"] = [{"parts": [{"type": "text", "text": status["response"]}], "index": 0}]
        elif method == "tasks/cancel":
            tid = _safe_id(params.get("id", ""), fallback="")
            task_queue.cancel(tid)
            result = {"id": tid, "status": {"state": "canceled"}}
        else:
            self._send_json({
                "jsonrpc": "2.0",
                "error": {"code": -32601, "message": f"Method not found: {method}"},
                "id": rpc_id,
            })
            return

        self._send_json({"jsonrpc": "2.0", "result": result, "id": rpc_id})

    def _handle_task_send(self, params: dict) -> dict:
        task_id = _safe_id(params.get("id"), fallback=str(uuid.uuid4()))
        message = params.get("message", {})
        if not isinstance(message, dict):
            return _task_failed(task_id, "Invalid message")

        parts = message.get("parts", [])
        if not isinstance(parts, list):
            return _task_failed(task_id, "Invalid message parts")

        text_parts = []
        for part in parts:
            if isinstance(part, dict) and part.get("type") == "text":
                text_parts.append(str(part.get("text", "")))
        user_text = "\n".join(text_parts)

        if not user_text.strip():
            return _task_failed(task_id, "Empty message")

        user_text = sanitize_inbound(user_text, max_length=self.server.max_message_chars)
        metadata = message.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        if "sender_name" not in metadata:
            from_field = params.get("from") or (params.get("sender") if isinstance(params.get("sender"), dict) else {}).get("name")
            metadata["sender_name"] = from_field or metadata.get("agent_name", f"agent-{self.client_address[0]}")
        raw_name = str(metadata.get("sender_name") or "")
        metadata["sender_name"] = "".join(c for c in raw_name if c.isalnum() or c in "-_.@ ")[:64] or "remote"
        metadata["reply_to_task_id"] = _safe_id(metadata.get("reply_to_task_id", ""), fallback="", max_length=64) if metadata.get("reply_to_task_id") else ""

        audit.log("task_received", {"task_id": task_id, "length": len(user_text)})

        if task_queue.pending_count() >= _MAX_PENDING:
            return _task_failed(task_id, "Agent busy — too many pending tasks")

        task = task_queue.enqueue(task_id, user_text, metadata)
        if task is None:
            return _task_failed(task_id, "Task ID already in use")

        threading.Thread(target=_trigger_webhook, args=(task_id,), daemon=True).start()

        task.ready.wait(timeout=_RESPONSE_TIMEOUT)

        if task.response is None:
            return {
                "id": task_id,
                "status": {"state": "working"},
                "artifacts": [{"parts": [{"type": "text", "text": "(processing — poll with tasks/get)"}], "index": 0}],
            }

        filtered = filter_outbound(task.response)
        audit.log("task_completed", {"task_id": task_id, "response_length": len(filtered)})

        state = task.state if task.state in {"completed", "failed", "canceled"} else "completed"
        return {
            "id": task_id,
            "status": {"state": state},
            "artifacts": [{"parts": [{"type": "text", "text": filtered}], "index": 0}],
        }


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
            "protocolVersion": "0.2.0",
            "capabilities": {
                "streaming": False,
                "pushNotifications": False,
                "multiTurn": False,
                "structuredMetadata": True,
            },
            "skills": [
                {
                    "id": "general",
                    "name": "General Assistant",
                    "description": "General-purpose AI assistant with tool use, web search, and more",
                }
            ],
            "authentication": {
                "schemes": ["bearer"] if self.auth_token else [],
            },
        }
