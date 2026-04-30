#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${HERMES_PYTHON:-${PYTHON:-}}"
if [ -z "$PYTHON" ]; then
  for candidate in \
    "$SCRIPT_DIR/.venv/bin/python" \
    "$SCRIPT_DIR/venv/bin/python" \
    "${HERMES_HOME:-}/hermes-agent/venv/bin/python" \
    "${HERMES_HOME:-}/hermes-agent/.venv/bin/python" \
    "$HOME/.hermes/hermes-agent/venv/bin/python" \
    "$HOME/.hermes/hermes-agent/.venv/bin/python"; do
    if [ -x "$candidate" ]; then
      PYTHON="$candidate"
      break
    fi
  done
  if [ -z "$PYTHON" ]; then
    PYTHON="$(command -v python3)"
  fi
fi

export PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON" -m hermes_a2a_cli uninstall "$@"
