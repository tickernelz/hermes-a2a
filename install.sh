#!/usr/bin/env bash
set -euo pipefail

DRY_RUN=false
PROFILE_NAME=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=true ;;
    --hermes-home)
      [ "$#" -ge 2 ] || { echo "--hermes-home requires a path" >&2; exit 2; }
      HERMES_HOME="$2"
      shift
      ;;
    --profile)
      [ "$#" -ge 2 ] || { echo "--profile requires a profile name" >&2; exit 2; }
      PROFILE_NAME="$2"
      shift
      ;;
    -h|--help)
      echo "Usage: ./install.sh [--dry-run] [--profile NAME | --hermes-home PATH]"
      exit 0
      ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_DIR="$SCRIPT_DIR/plugin"
DASHBOARD_DIR="$SCRIPT_DIR/dashboard"
_default_home() {
  printf '%s/.hermes' "$HOME"
}

_home_for_profile() {
  case "$1" in
    default|main) _default_home ;;
    *) printf '%s/.hermes/profiles/%s' "$HOME" "$1" ;;
  esac
}

_find_profile_homes() {
  if [ -f "$HOME/.hermes/config.yaml" ]; then
    printf 'default:%s/.hermes\n' "$HOME"
  fi
  if [ -d "$HOME/.hermes/profiles" ]; then
    for profile_dir in "$HOME"/.hermes/profiles/*; do
      [ -d "$profile_dir" ] || continue
      [ -f "$profile_dir/config.yaml" ] || continue
      printf '%s:%s\n' "$(basename "$profile_dir")" "$profile_dir"
    done
  fi
}

_resolve_hermes_home() {
  if [ -n "${HERMES_HOME:-}" ]; then
    printf '%s\n' "$HERMES_HOME"
    return
  fi
  if [ -n "$PROFILE_NAME" ]; then
    _home_for_profile "$PROFILE_NAME"
    return
  fi

  mapfile -t profiles < <(_find_profile_homes)
  if [ "${#profiles[@]}" -eq 0 ]; then
    echo "No Hermes profiles found. Use --hermes-home PATH or set HERMES_HOME." >&2
    exit 1
  fi
  if [ "${#profiles[@]}" -eq 1 ]; then
    printf '%s\n' "${profiles[0]#*:}"
    return
  fi
  if [ ! -t 0 ]; then
    echo "Refusing to choose automatically: multiple Hermes profiles found in non-interactive mode. Use --profile NAME or --hermes-home PATH." >&2
    return 1
  fi

  echo "Select target Hermes profile:" >&2
  local i=1
  local entry
  for entry in "${profiles[@]}"; do
    echo "  [$i] ${entry%%:*} -> ${entry#*:}" >&2
    i=$((i + 1))
  done
  printf 'Profile number: ' >&2
  read -r choice
  case "$choice" in
    ''|*[!0-9]*) echo "Invalid selection" >&2; exit 1 ;;
  esac
  if [ "$choice" -lt 1 ] || [ "$choice" -gt "${#profiles[@]}" ]; then
    echo "Invalid selection" >&2
    exit 1
  fi
  printf '%s\n' "${profiles[$((choice - 1))]#*:}"
}

HERMES_HOME="$(_resolve_hermes_home)"
HERMES_HOME="$(cd "$HERMES_HOME" && pwd)"
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
    "${PYTHON:-}" \
    "$SCRIPT_DIR/.venv/bin/python" \
    "$SCRIPT_DIR/venv/bin/python" \
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

A2A_EXPLICIT_KEYS=""
for key in A2A_PORT A2A_HOST A2A_PUBLIC_URL A2A_AGENT_NAME A2A_AGENT_DESCRIPTION A2A_REQUIRE_AUTH; do
  if [ "${!key+x}" = "x" ] && [ -n "${!key}" ]; then
    A2A_EXPLICIT_KEYS="${A2A_EXPLICIT_KEYS:+$A2A_EXPLICIT_KEYS,}$key"
  fi
done

export HERMES_HOME CONFIG_FILE ENV_FILE PLUGIN_DIR SOURCE_DIR DASHBOARD_DIR DRY_RUN A2A_EXPLICIT_KEYS
export A2A_PORT="${A2A_PORT:-41731}"
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
export A2A_WEBHOOK_PORT="${A2A_WEBHOOK_PORT:-}"
export WEBHOOK_PORT="${WEBHOOK_PORT:-${A2A_WEBHOOK_PORT}}"

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
explicit_keys = {key for key in os.environ.get("A2A_EXPLICIT_KEYS", "").split(",") if key}

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


def env_has_key(lines: list[str], key: str) -> bool:
    prefix = f"{key}="
    return any(line.startswith(prefix) for line in lines)


def ensure_env(lines: list[str], key: str, value: str, *, overwrite: bool = False) -> None:
    prefix = f"{key}="
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            if overwrite:
                lines[index] = f"{key}={value}"
            return
    lines.append(f"{key}={value}")


def env_or_config(lines: list[str], key: str, current, default: str) -> str:
    env_value = os.environ.get(key)
    if key in explicit_keys and env_value not in (None, ""):
        return str(env_value)
    prefix = f"{key}="
    for line in lines:
        if line.startswith(prefix):
            return line.split("=", 1)[1]
    if current not in (None, ""):
        return str(current)
    return default


def bool_value(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}



def choose_webhook_port(home: Path, config: dict, existing_env: list[str]) -> int:
    raw = os.environ.get("WEBHOOK_PORT", "").strip()
    if raw:
        return int(raw)

    for section_path in (("platforms", "webhook", "extra"), ("webhook", "extra")):
        current = config
        for key in section_path:
            current = current.get(key) if isinstance(current, dict) else None
        if isinstance(current, dict) and current.get("port") not in (None, ""):
            return int(current["port"])

    for line in existing_env:
        if line.startswith("WEBHOOK_PORT=") and line.split("=", 1)[1].strip():
            return int(line.split("=", 1)[1])

    import socket

    base_port = 47644
    if home.name != ".hermes":
        profile_name = home.name
        if profile_name:
            base_port += 1 + (sum(profile_name.encode("utf-8")) % 1000)

    for port in range(base_port, min(base_port + 1000, 65535)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port

    raise RuntimeError("could not find an available local webhook port")

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

cfg = load_config()
existing_env = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
existing_a2a = cfg.get("a2a", {}) if isinstance(cfg.get("a2a"), dict) else {}
existing_server = existing_a2a.get("server", {}) if isinstance(existing_a2a.get("server"), dict) else {}

a2a_host = env_or_config(existing_env, "A2A_HOST", existing_server.get("host"), os.environ["A2A_HOST"])
a2a_port = env_or_config(existing_env, "A2A_PORT", existing_server.get("port"), os.environ["A2A_PORT"])
a2a_public_url = env_or_config(existing_env, "A2A_PUBLIC_URL", existing_server.get("public_url"), f"http://{a2a_host}:{a2a_port}").rstrip("/")
a2a_agent_name = env_or_config(existing_env, "A2A_AGENT_NAME", None, os.environ["A2A_AGENT_NAME"])
a2a_agent_description = env_or_config(existing_env, "A2A_AGENT_DESCRIPTION", None, os.environ["A2A_AGENT_DESCRIPTION"])
a2a_require_auth = env_or_config(existing_env, "A2A_REQUIRE_AUTH", existing_server.get("require_auth"), os.environ["A2A_REQUIRE_AUTH"])

webhook_port = choose_webhook_port(home, cfg, existing_env)
os.environ["WEBHOOK_PORT"] = str(webhook_port)

secret = env_value(existing_env, "A2A_WEBHOOK_SECRET", lambda: secrets.token_hex(24))
auth_token = env_value(existing_env, "A2A_AUTH_TOKEN", lambda: secrets.token_hex(24))
remote_token_env = os.environ.get("A2A_REMOTE_TOKEN_ENV", "").strip()
remote_token = env_value(existing_env, remote_token_env, lambda: secrets.token_hex(24)) if remote_token_env else ""

env_lines = list(existing_env)
ensure_env(env_lines, "A2A_ENABLED", "true")
ensure_env(env_lines, "A2A_HOST", a2a_host, overwrite="A2A_HOST" in explicit_keys)
ensure_env(env_lines, "A2A_PORT", a2a_port, overwrite="A2A_PORT" in explicit_keys)
ensure_env(env_lines, "A2A_PUBLIC_URL", a2a_public_url, overwrite="A2A_PUBLIC_URL" in explicit_keys)
ensure_env(env_lines, "A2A_AGENT_NAME", a2a_agent_name, overwrite="A2A_AGENT_NAME" in explicit_keys)
ensure_env(env_lines, "A2A_AGENT_DESCRIPTION", a2a_agent_description, overwrite="A2A_AGENT_DESCRIPTION" in explicit_keys)
ensure_env(env_lines, "A2A_REQUIRE_AUTH", a2a_require_auth, overwrite="A2A_REQUIRE_AUTH" in explicit_keys)
ensure_env(env_lines, "A2A_AUTH_TOKEN", auth_token)
ensure_env(env_lines, "A2A_WEBHOOK_SECRET", secret)
ensure_env(env_lines, "WEBHOOK_ENABLED", "true")
ensure_env(env_lines, "WEBHOOK_PORT", os.environ["WEBHOOK_PORT"])
if remote_token_env:
    ensure_env(env_lines, remote_token_env, remote_token)

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
webhook_extra["port"] = webhook_port
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
routes["a2a_dashboard"] = {"secret": secret, "prompt": "[A2A dashboard]"}

platforms = cfg.setdefault("platforms", {})
if not isinstance(platforms, dict):
    platforms = {}
    cfg["platforms"] = platforms
platform_webhook = platforms.setdefault("webhook", {})
if not isinstance(platform_webhook, dict):
    platform_webhook = {}
    platforms["webhook"] = platform_webhook
platform_webhook["enabled"] = True
platform_webhook_extra = platform_webhook.setdefault("extra", {})
if not isinstance(platform_webhook_extra, dict):
    platform_webhook_extra = {}
    platform_webhook["extra"] = platform_webhook_extra
platform_webhook_extra["port"] = webhook_port
platform_routes = platform_webhook_extra.setdefault("routes", {})
if not isinstance(platform_routes, dict):
    platform_routes = {}
    platform_webhook_extra["routes"] = platform_routes
platform_routes["a2a_trigger"] = dict(route)
platform_routes["a2a_dashboard"] = {"secret": secret, "prompt": "[A2A dashboard]"}

a2a = cfg.setdefault("a2a", {})
if not isinstance(a2a, dict):
    a2a = {}
    cfg["a2a"] = a2a
a2a["enabled"] = True
a2a["server"] = {
    **(a2a.get("server") if isinstance(a2a.get("server"), dict) else {}),
    "host": a2a_host,
    "port": int(a2a_port),
    "public_url": a2a_public_url,
    "require_auth": bool_value(a2a_require_auth, True),
}
security = a2a.setdefault("security", {})
if not isinstance(security, dict):
    security = {}
    a2a["security"] = security
security.setdefault("allow_unconfigured_urls", False)
security.setdefault("redact_outbound", True)
security.setdefault("max_message_chars", 50000)
security.setdefault("max_response_chars", 100000)
security.setdefault("max_request_bytes", 1048576)
security.setdefault("max_raw_part_bytes", 262144)
security.setdefault("max_parts", 20)
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
