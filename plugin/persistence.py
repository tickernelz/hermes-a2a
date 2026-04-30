"""A2A conversation persistence — stores interactions to disk so compaction can't erase them.

Format matches ~/inbox/conversations/{agent}/{date}.md for consistency.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from .paths import conversation_dir
from .config import get_security_config
from .security import filter_outbound

_lock = Lock()


def _response_limit() -> int:
    try:
        return get_security_config().max_response_chars
    except Exception:
        return 100_000


def _truncate_text(text: str, max_chars: int | None = None) -> str:
    limit = max_chars if isinstance(max_chars, int) and max_chars > 0 else _response_limit()
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[truncated by A2A max_response_chars]"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def save_exchange(
    agent_name: str,
    task_id: str,
    inbound_text: str,
    outbound_text: str,
    metadata: dict | None = None,
    direction: str = "inbound",
) -> Path:
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    timestamp = now.strftime("%H:%M:%S")
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in agent_name.lower())
    directory = conversation_dir() / safe_name
    filepath = directory / f"{today}.md"

    inbound_text = _truncate_text(filter_outbound(inbound_text))
    outbound_text = _truncate_text(filter_outbound(outbound_text))
    intent = (metadata or {}).get("intent", "")
    reply_to = (metadata or {}).get("reply_to_task_id", "")

    entry_lines = [f"## {timestamp} | task:{task_id}"]
    if intent:
        entry_lines[0] += f" | {intent}"
    if reply_to:
        entry_lines[0] += f" | reply_to:{reply_to}"
    entry_lines.append("")

    if direction == "outbound":
        entry_lines.append(f"**→ me:** {outbound_text}")
        entry_lines.append("")
        entry_lines.append(f"**← {safe_name}:** {inbound_text}")
    else:
        entry_lines.append(f"**← {safe_name}:** {inbound_text}")
        entry_lines.append("")
        entry_lines.append(f"**→ reply:** {outbound_text}")

    entry_lines.append("")
    entry_lines.append("---")
    entry_lines.append("")

    entry = "\n".join(entry_lines)

    with _lock:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(entry)

    return filepath


def update_exchange(
    agent_name: str,
    task_id: str,
    inbound_text: str,
) -> bool:
    """Append a final update event for an existing or cross-day background exchange."""
    inbound_text = _truncate_text(filter_outbound(inbound_text))
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in agent_name.lower())
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    timestamp = now.strftime("%H:%M:%S")
    filepath = conversation_dir() / safe_name / f"{today}.md"
    entry = "\n".join(
        [
            f"## {timestamp} | task:{task_id} | update:completed",
            "",
            f"**← {safe_name}:** {inbound_text}",
            "",
            "---",
            "",
        ]
    )
    with _lock:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(entry)
    return True
