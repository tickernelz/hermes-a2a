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
      echo "Usage: ./uninstall.sh [--dry-run] [--profile NAME | --hermes-home PATH]"
      exit 0
      ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

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
  if [ "${#profiles[@]}" -eq 1 ] || [ ! -t 0 ]; then
    printf '%s\n' "${profiles[0]#*:}"
    return
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
if [ "$HERMES_HOME" = "/" ]; then
  echo "Refusing to uninstall from filesystem root" >&2
  exit 1
fi
if [ ! -f "$HERMES_HOME/config.yaml" ]; then
  echo "Refusing to uninstall: $HERMES_HOME/config.yaml not found" >&2
  exit 1
fi
PLUGIN_DIR="$HERMES_HOME/plugins/a2a"
case "$PLUGIN_DIR" in
  "$HERMES_HOME"/plugins/a2a) ;;
  *) echo "Refusing unsafe plugin path: $PLUGIN_DIR" >&2; exit 1 ;;
esac

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
