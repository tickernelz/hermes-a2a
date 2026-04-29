#!/usr/bin/env bash
set -euo pipefail

DRY_RUN=false
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=true ;;
    -h|--help)
      echo "Usage: HERMES_HOME=/path/to/profile ./install.sh [--dry-run]"
      exit 0
      ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_DIR="$SCRIPT_DIR/plugin"
DASHBOARD_DIR="$SCRIPT_DIR/dashboard"
if [ -z "${HERMES_HOME:-}" ]; then
  echo "Refusing to install: set HERMES_HOME explicitly to the target Hermes profile" >&2
  exit 1
fi
HERMES_HOME="$HERMES_HOME"
CONFIG_FILE="$HERMES_HOME/config.yaml"
ENV_FILE="$HERMES_HOME/.env"
PLUGIN_DIR="$HERMES_HOME/plugins/a2a"

if [ ! -d "$SOURCE_DIR" ]; then
  echo "plugin/ directory not found: $SOURCE_DIR" >&2
  exit 1
fi

if [ ! -d "$HERMES_HOME" ]; then
  echo "HERMES_HOME does not exist: $HERMES_HOME" >&2
  exit 1
fi

if [ ! -f "$CONFIG_FILE" ]; then
  echo "Refusing to install: $CONFIG_FILE not found. Set HERMES_HOME to the target Hermes profile and run setup first." >&2
  exit 1
fi

_pick_python() {
  if [ -n "${HERMES_PYTHON:-}" ] && [ -x "$HERMES_PYTHON" ]; then
    echo "$HERMES_PYTHON"
    return
  fi
  for candidate in \
    "$HOME/.hermes/hermes-agent/venv/bin/python" \
    "$HOME/.hermes/hermes-agent/.venv/bin/python" \
    "$HERMES_HOME/hermes-agent/venv/bin/python" \
    "$HERMES_HOME/hermes-agent/.venv/bin/python"; do
    if [ -x "$candidate" ]; then
      echo "$candidate"
      return
    fi
  done
  command -v python3
}

PYTHON="$(_pick_python)"

export HERMES_HOME CONFIG_FILE ENV_FILE PLUGIN_DIR SOURCE_DIR DASHBOARD_DIR DRY_RUN
export A2A_PORT="${A2A_PORT:-8081}"
export A2A_HOST="${A2A_HOST:-127.0.0.1}"
export A2A_PUBLIC_URL="${A2A_PUBLIC_URL:-http://${A2A_HOST}:${A2A_PORT}}"
export A2A_AGENT_NAME="${A2A_AGENT_NAME:-hermes-agent}"
export A2A_AGENT_DESCRIPTION="${A2A_AGENT_DESCRIPTION:-Hermes A2A profile}"
export A2A_REQUIRE_AUTH="${A2A_REQUIRE_AUTH:-true}"
export A2A_HOME_PLATFORM="${A2A_HOME_PLATFORM:-}"
export A2A_HOME_CHAT_TYPE="${A2A_HOME_CHAT_TYPE:-dm}"
export A2A_HOME_CHAT_ID="${A2A_HOME_CHAT_ID:-}"
export A2A_HOME_USER_ID="${A2A_HOME_USER_ID:-}"
export A2A_HOME_USER_NAME="${A2A_HOME_USER_NAME:-user}"
export A2A_REMOTE_NAME="${A2A_REMOTE_NAME:-}"
export A2A_REMOTE_URL="${A2A_REMOTE_URL:-}"
export A2A_REMOTE_DESCRIPTION="${A2A_REMOTE_DESCRIPTION:-}"
export A2A_REMOTE_TOKEN_ENV="${A2A_REMOTE_TOKEN_ENV:-}"

"$PYTHON" <<'PY'
from __future__ import annotations

import os
import secrets
import shutil
import sys
import time
from pathlib import Path

try:
    import yaml
except Exception as exc:
    print(f"PyYAML is required to safely update config.yaml: {exc}", file=sys.stderr)
    sys.exit(1)

home = Path(os.environ["HERMES_HOME"]).expanduser().resolve()
config_path = Path(os.environ["CONFIG_FILE"]).expanduser().resolve()
env_path = Path(os.environ["ENV_FILE"]).expanduser().resolve()
plugin_dir = Path(os.environ["PLUGIN_DIR"]).expanduser().resolve()
source_dir = Path(os.environ["SOURCE_DIR"]).expanduser().resolve()
dashboard_dir = Path(os.environ["DASHBOARD_DIR"]).expanduser().resolve()
dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"

if dry_run:
    print("DRY RUN: no files will be modified")

try:
    plugin_dir.relative_to(home)
except ValueError:
    print(f"Refusing to install outside HERMES_HOME: {plugin_dir}", file=sys.stderr)
    sys.exit(1)

if config_path.parent != home:
    print(f"Refusing to edit config outside HERMES_HOME: {config_path}", file=sys.stderr)
    sys.exit(1)

if env_path.parent != home:
    print(f"Refusing to edit .env outside HERMES_HOME: {env_path}", file=sys.stderr)
    sys.exit(1)

if not config_path.exists():
    print(f"Refusing to install: {config_path} not found", file=sys.stderr)
    sys.exit(1)


def backup(path: Path) -> None:
    if dry_run or not path.exists():
        return
    stamp = time.strftime("%Y%m%d%H%M%S")
    target = path.with_name(f"{path.name}.bak.{stamp}")
    counter = 1
    while target.exists():
        target = path.with_name(f"{path.name}.bak.{stamp}.{counter}")
        counter += 1
    if path.is_dir():
        shutil.copytree(path, target)
    else:
        shutil.copy2(path, target)
    print(f"Backed up {path} -> {target}")


def write_text(path: Path, text: str) -> None:
    if dry_run:
        print(f"Would write {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def load_config() -> dict:
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise SystemExit("config.yaml must contain a mapping")
    return data


def ensure_list(container: dict, key: str) -> list:
    value = container.get(key)
    if not isinstance(value, list):
        value = []
        container[key] = value
    return value


def append_unique(items: list, value: str) -> None:
    if value not in items:
        items.append(value)


def bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def ensure_env(lines: list[str], key: str, value: str) -> None:
    prefix = f"{key}="
    if any(line.startswith(prefix) for line in lines):
        return
    lines.append(f"{key}={value}")


def env_value(lines: list[str], key: str, default_factory) -> str:
    prefix = f"{key}="
    for line in lines:
        if line.startswith(prefix):
            return line.split("=", 1)[1]
    return default_factory()

if dry_run:
    print(f"Would install plugin to {plugin_dir}")
else:
    backup(plugin_dir)
    if plugin_dir.exists():
        shutil.rmtree(plugin_dir)
    shutil.copytree(source_dir, plugin_dir, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if dashboard_dir.exists():
        shutil.copytree(dashboard_dir, plugin_dir / "dashboard", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    print(f"Installed plugin to {plugin_dir}")

existing_env = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
secret = env_value(existing_env, "A2A_WEBHOOK_SECRET", lambda: secrets.token_hex(24))
auth_token = env_value(existing_env, "A2A_AUTH_TOKEN", lambda: secrets.token_hex(24))
remote_token_env = os.environ.get("A2A_REMOTE_TOKEN_ENV", "").strip()
remote_token = env_value(existing_env, remote_token_env, lambda: secrets.token_hex(24)) if remote_token_env else ""

env_lines = list(existing_env)
ensure_env(env_lines, "A2A_ENABLED", "true")
ensure_env(env_lines, "A2A_HOST", os.environ["A2A_HOST"])
ensure_env(env_lines, "A2A_PORT", os.environ["A2A_PORT"])
ensure_env(env_lines, "A2A_PUBLIC_URL", os.environ["A2A_PUBLIC_URL"].rstrip("/"))
ensure_env(env_lines, "A2A_AGENT_NAME", os.environ["A2A_AGENT_NAME"])
ensure_env(env_lines, "A2A_AGENT_DESCRIPTION", os.environ["A2A_AGENT_DESCRIPTION"])
ensure_env(env_lines, "A2A_REQUIRE_AUTH", os.environ["A2A_REQUIRE_AUTH"])
ensure_env(env_lines, "A2A_AUTH_TOKEN", auth_token)
ensure_env(env_lines, "A2A_WEBHOOK_SECRET", secret)
ensure_env(env_lines, "WEBHOOK_ENABLED", "true")
if remote_token_env:
    ensure_env(env_lines, remote_token_env, remote_token)

cfg = load_config()
plugins = cfg.setdefault("plugins", {})
if not isinstance(plugins, dict):
    plugins = {}
    cfg["plugins"] = plugins
append_unique(ensure_list(plugins, "enabled"), "a2a")

platform = os.environ.get("A2A_HOME_PLATFORM", "").strip()
if platform:
    platform_toolsets = cfg.setdefault("platform_toolsets", {})
    if not isinstance(platform_toolsets, dict):
        platform_toolsets = {}
        cfg["platform_toolsets"] = platform_toolsets
    append_unique(ensure_list(platform_toolsets, platform), "a2a")

    known = cfg.setdefault("known_plugin_toolsets", {})
    if not isinstance(known, dict):
        known = {}
        cfg["known_plugin_toolsets"] = known
    append_unique(ensure_list(known, platform), "a2a")

webhook = cfg.setdefault("webhook", {})
if not isinstance(webhook, dict):
    webhook = {}
    cfg["webhook"] = webhook
webhook["enabled"] = True
webhook_extra = webhook.setdefault("extra", {})
if not isinstance(webhook_extra, dict):
    webhook_extra = {}
    webhook["extra"] = webhook_extra
webhook_extra.setdefault("port", 8644)
webhook_extra.setdefault("secret", secret)
routes = webhook_extra.setdefault("routes", {})
if not isinstance(routes, dict):
    routes = {}
    webhook_extra["routes"] = routes

route = {
    "secret": secret,
    "prompt": "[A2A trigger]",
}
chat_id = os.environ.get("A2A_HOME_CHAT_ID", "").strip()
if platform and chat_id:
    route["deliver"] = platform
    route["deliver_extra"] = {"chat_id": chat_id}
    route["source"] = {
        "platform": platform,
        "chat_type": os.environ.get("A2A_HOME_CHAT_TYPE", "dm").strip() or "dm",
        "chat_id": chat_id,
        "user_id": os.environ.get("A2A_HOME_USER_ID", "").strip() or chat_id,
        "user_name": os.environ.get("A2A_HOME_USER_NAME", "").strip() or "user",
    }
routes["a2a_trigger"] = route

platforms = cfg.setdefault("platforms", {})
if not isinstance(platforms, dict):
    platforms = {}
    cfg["platforms"] = platforms
platform_webhook = platforms.setdefault("webhook", {})
if not isinstance(platform_webhook, dict):
    platform_webhook = {}
    platforms["webhook"] = platform_webhook
platform_webhook_extra = platform_webhook.setdefault("extra", {})
if not isinstance(platform_webhook_extra, dict):
    platform_webhook_extra = {}
    platform_webhook["extra"] = platform_webhook_extra
platform_routes = platform_webhook_extra.setdefault("routes", {})
if not isinstance(platform_routes, dict):
    platform_routes = {}
    platform_webhook_extra["routes"] = platform_routes
platform_routes["a2a_trigger"] = dict(route)

a2a = cfg.setdefault("a2a", {})
if not isinstance(a2a, dict):
    a2a = {}
    cfg["a2a"] = a2a
a2a["enabled"] = True
a2a["server"] = {
    **(a2a.get("server") if isinstance(a2a.get("server"), dict) else {}),
    "host": os.environ["A2A_HOST"],
    "port": int(os.environ["A2A_PORT"]),
    "public_url": os.environ["A2A_PUBLIC_URL"].rstrip("/"),
    "require_auth": bool_env("A2A_REQUIRE_AUTH", True),
}
security = a2a.setdefault("security", {})
if not isinstance(security, dict):
    security = {}
    a2a["security"] = security
security.setdefault("allow_unconfigured_urls", False)
security.setdefault("redact_outbound", True)
security.setdefault("max_message_chars", 50000)
security.setdefault("max_response_chars", 100000)
security.setdefault("rate_limit_per_minute", 20)

remote_name = os.environ.get("A2A_REMOTE_NAME", "").strip()
remote_url = os.environ.get("A2A_REMOTE_URL", "").strip().rstrip("/")
if remote_name and remote_url:
    agents = a2a.setdefault("agents", [])
    if not isinstance(agents, list):
        agents = []
        a2a["agents"] = agents
    new_agent = {
        "name": remote_name,
        "url": remote_url,
        "description": os.environ.get("A2A_REMOTE_DESCRIPTION", "").strip(),
        "auth_token_env": remote_token_env,
        "enabled": True,
        "tags": ["local"],
        "trust_level": "trusted",
    }
    agents[:] = [agent for agent in agents if not (isinstance(agent, dict) and agent.get("name") == remote_name)]
    agents.append(new_agent)

if not dry_run:
    backup(config_path)
    backup(env_path)
    write_text(config_path, yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))
    write_text(env_path, "\n".join(env_lines).rstrip() + "\n")
else:
    print(f"Would update {config_path}")
    print(f"Would update {env_path}")

print("A2A install complete for", home)
print("No restart performed. Restart the target Hermes gateway manually after reviewing the diff/config.")
PY
