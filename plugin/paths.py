"""Profile-aware filesystem paths for the A2A plugin."""

from __future__ import annotations

import os
from pathlib import Path


def hermes_home() -> Path:
    """Return the active Hermes home/profile directory.

    Hermes profiles isolate config, env, plugin state, and memory under
    HERMES_HOME. Prefer Hermes' own resolver when available, then the
    environment, and only then fall back to ~/.hermes for standalone tests.
    """
    try:
        from hermes_constants import get_hermes_home

        home = get_hermes_home()
        if home:
            return Path(home).expanduser().resolve()
    except Exception:
        pass

    env_home = os.getenv("HERMES_HOME")
    if env_home:
        return Path(env_home).expanduser().resolve()

    return (Path.home() / ".hermes").resolve()


def config_path() -> Path:
    return hermes_home() / "config.yaml"


def plugin_state_dir() -> Path:
    return hermes_home()


def conversation_dir() -> Path:
    return plugin_state_dir() / "a2a_conversations"


def audit_log_path() -> Path:
    return plugin_state_dir() / "a2a_audit.jsonl"


def dashboard_meta_path() -> Path:
    return plugin_state_dir() / "a2a_dashboard_meta.json"
