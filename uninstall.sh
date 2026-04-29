#!/usr/bin/env bash
set -euo pipefail

DRY_RUN=false
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=true ;;
    -h|--help)
      echo "Usage: HERMES_HOME=/path/to/profile ./uninstall.sh [--dry-run]"
      exit 0
      ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

if [ -z "${HERMES_HOME:-}" ]; then
  echo "Refusing to uninstall: set HERMES_HOME explicitly to the target Hermes profile" >&2
  exit 1
fi
HERMES_HOME="$HERMES_HOME"
PLUGIN_DIR="$HERMES_HOME/plugins/a2a"

if [ "$DRY_RUN" = true ]; then
  echo "DRY RUN: would remove $PLUGIN_DIR"
  exit 0
fi

if [ -d "$PLUGIN_DIR" ]; then
  rm -rf "$PLUGIN_DIR"
  echo "Removed $PLUGIN_DIR"
else
  echo "Plugin not found at $PLUGIN_DIR"
fi

echo "No config/env cleanup performed. Remove A2A entries from $HERMES_HOME/config.yaml and $HERMES_HOME/.env manually if desired, then restart the target Hermes gateway."
