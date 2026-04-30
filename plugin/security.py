"""A2A security utilities — prompt injection filtering, redaction, rate limiting, audit."""

from __future__ import annotations

import json
import logging
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional

from .paths import audit_log_path

logger = logging.getLogger(__name__)

INJECTION_PATTERNS = [
    re.compile(r"(?i)<\s*system\s*>.*?<\s*/\s*system\s*>", re.DOTALL),
    re.compile(r"(?i)\[INST\].*?\[/INST\]", re.DOTALL),
    re.compile(r"(?i)ignore\s+(all\s+)?previous\s+instructions?"),
    re.compile(r"(?i)you\s+are\s+now\s+"),
    re.compile(r"(?i)new\s+system\s+prompt"),
    re.compile(r"(?i)disregard\s+(all\s+)?(prior|earlier|above)"),
    re.compile(r"(?i)override\s+(your\s+)?(instructions?|rules?|guidelines?)"),
    re.compile(r"<\|im_(start|end)\|>"),
    re.compile(r"(?m)^(Human|Assistant|System)\s*:", re.MULTILINE),
]


def sanitize_inbound(text: str, max_length: int = 50_000) -> str:
    if len(text) > max_length:
        text = text[:max_length] + "\n[... message truncated for safety]"
    for pattern in INJECTION_PATTERNS:
        if pattern.search(text):
            logger.warning("Prompt injection pattern detected in A2A message")
            text = pattern.sub("[FILTERED]", text)
    return text


SENSITIVE_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)\S+"),
    re.compile(r"(?i)(api[_-]?key|secret|password|token|credential)\s*[:=]\s*\S+"),
    re.compile(r"(?i)(sk-(?:proj-)?[a-zA-Z0-9_-]{10,})"),
    re.compile(r"(?i)(ghp_[a-zA-Z0-9]{20,})"),
    re.compile(r"(?i)(github_pat_[a-zA-Z0-9_]{20,})"),
    re.compile(r"(?i)(xox[abpcrs]-[a-zA-Z0-9-]+)"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
]
SENSITIVE_KEYS = {"authorization", "api_key", "apikey", "secret", "password", "token", "credential", "push_token", "auth_token"}


def filter_outbound(text: str) -> str:
    for pattern in SENSITIVE_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text.strip()


def redact_data(value: Any, *, max_field_chars: int = 4096) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            safe_key = str(key)
            if safe_key.lower().replace("-", "_") in SENSITIVE_KEYS:
                redacted[safe_key] = "[REDACTED]"
            else:
                redacted[safe_key] = redact_data(item, max_field_chars=max_field_chars)
        return redacted
    if isinstance(value, list):
        return [redact_data(item, max_field_chars=max_field_chars) for item in value[:100]]
    if isinstance(value, tuple):
        return [redact_data(item, max_field_chars=max_field_chars) for item in value[:100]]
    if isinstance(value, str):
        text = filter_outbound(value)
        if len(text) > max_field_chars:
            return text[:max_field_chars] + "...[truncated]"
        return text
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    text = filter_outbound(str(value))
    if len(text) > max_field_chars:
        return text[:max_field_chars] + "...[truncated]"
    return text


class RateLimiter:
    def __init__(self, max_requests: int = 20, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window = window_seconds
        self._buckets: Dict[str, list] = defaultdict(list)
        self._lock = Lock()

    def allow(self, client_id: str) -> bool:
        now = time.time()
        cutoff = now - self.window
        with self._lock:
            for key in list(self._buckets.keys()):
                self._buckets[key] = [ts for ts in self._buckets[key] if ts > cutoff]
                if not self._buckets[key] and key != client_id:
                    del self._buckets[key]
            bucket = self._buckets[client_id]
            if len(bucket) >= self.max_requests:
                return False
            bucket.append(now)
            return True


_AUDIT_MAX_SIZE = 10 * 1024 * 1024
_AUDIT_BACKUP_COUNT = 5


class AuditLogger:
    def __init__(self, log_path: Optional[Path] = None, *, max_bytes: int = _AUDIT_MAX_SIZE, backup_count: int = _AUDIT_BACKUP_COUNT, max_field_chars: int = 4096):
        self.log_path = log_path
        self.max_bytes = max(1024, int(max_bytes))
        self.backup_count = max(0, int(backup_count))
        self.max_field_chars = max(16, int(max_field_chars))
        self._lock = Lock()

    def _path(self) -> Path:
        return self.log_path or audit_log_path()

    def _rotated_path(self, index: int) -> Path:
        return self._path().with_name(self._path().name + f".{index}")

    def _rotate_if_needed(self) -> None:
        path = self._path()
        if not path.exists() or path.stat().st_size <= self.max_bytes:
            return
        if self.backup_count <= 0:
            path.unlink(missing_ok=True)
            return
        oldest = self._rotated_path(self.backup_count)
        if oldest.exists():
            oldest.unlink()
        for index in range(self.backup_count - 1, 0, -1):
            src = self._rotated_path(index)
            if src.exists():
                src.replace(self._rotated_path(index + 1))
        path.replace(self._rotated_path(1))

    def log(self, event_type: str, data: dict) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": str(event_type),
            **(redact_data(data, max_field_chars=self.max_field_chars) if isinstance(data, dict) else {}),
        }
        try:
            with self._lock:
                path = self._path()
                path.parent.mkdir(parents=True, exist_ok=True)
                self._rotate_if_needed()
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        except Exception:
            logger.debug("Failed to write A2A audit log", exc_info=True)


audit = AuditLogger()
