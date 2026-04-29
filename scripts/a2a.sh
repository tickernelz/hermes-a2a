#!/usr/bin/env bash
set -euo pipefail

DEFAULT_REPO="tickernelz/hermes-a2a"
DEFAULT_REF="main"

usage() {
  cat <<'EOF'
Usage:
  curl -fsSL https://raw.githubusercontent.com/tickernelz/hermes-a2a/main/scripts/a2a.sh | bash -s -- install [install.sh args]
  curl -fsSL https://raw.githubusercontent.com/tickernelz/hermes-a2a/main/scripts/a2a.sh | bash -s -- update [install.sh args]
  curl -fsSL https://raw.githubusercontent.com/tickernelz/hermes-a2a/main/scripts/a2a.sh | bash -s -- uninstall [uninstall.sh args]

Commands:
  install     Download repo archive and run install.sh.
  update      Same as install; re-runs the idempotent installer from the requested ref.
  uninstall   Download repo archive and run uninstall.sh.

Target selection:
  Auto-detects Hermes profiles. In a TTY, prompts when multiple profiles exist.
  --profile NAME or --hermes-home PATH can override the target.

Environment:
  HERMES_HOME        Optional. Target Hermes profile directory override.
  HERMES_A2A_REPO   Optional. GitHub repo, default tickernelz/hermes-a2a.
  HERMES_A2A_REF    Optional. Branch/tag/commit, default main.
  HERMES_A2A_CACHE  Optional. Archive cache directory.
EOF
}

if [ "$#" -lt 1 ]; then
  usage >&2
  exit 2
fi

COMMAND="$1"
shift

case "$COMMAND" in
  install|update|uninstall) ;;
  -h|--help|help)
    usage
    exit 0
    ;;
  *)
    echo "Unknown command: $COMMAND" >&2
    usage >&2
    exit 2
    ;;
esac

REPO="${HERMES_A2A_REPO:-$DEFAULT_REPO}"
REF="${HERMES_A2A_REF:-$DEFAULT_REF}"
CACHE_ROOT="${HERMES_A2A_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/hermes-a2a}"
ARCHIVE_URL="https://codeload.github.com/${REPO}/tar.gz/${REF}"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/hermes-a2a.XXXXXX")"
cleanup() {
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

mkdir -p "$CACHE_ROOT"
ARCHIVE="$CACHE_ROOT/${REPO//\//-}-${REF//\//-}.tar.gz"

if command -v curl >/dev/null 2>&1; then
  curl -fsSL "$ARCHIVE_URL" -o "$ARCHIVE"
elif command -v wget >/dev/null 2>&1; then
  wget -qO "$ARCHIVE" "$ARCHIVE_URL"
else
  echo "curl or wget is required" >&2
  exit 1
fi

tar -xzf "$ARCHIVE" -C "$WORK_DIR" --strip-components=1
cd "$WORK_DIR"

case "$COMMAND" in
  install|update)
    exec ./install.sh "$@"
    ;;
  uninstall)
    exec ./uninstall.sh "$@"
    ;;
esac
