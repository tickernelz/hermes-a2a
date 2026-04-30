#!/usr/bin/env bash
set -euo pipefail

DEFAULT_REPO="tickernelz/hermes-a2a"
DEFAULT_REF="main"
usage() {
  cat <<'EOF'
Usage:
  curl -fsSL https://raw.githubusercontent.com/tickernelz/hermes-a2a/main/scripts/a2a.sh | bash
  curl -fsSL https://raw.githubusercontent.com/tickernelz/hermes-a2a/main/scripts/a2a.sh | bash -s -- install [args]

The default command installs the persistent CLI only:
  ~/.local/bin/hermes_a2a

After bootstrap, use:
  hermes_a2a doctor
  hermes_a2a install
  hermes_a2a update
  hermes_a2a uninstall

Optional curl arguments first install/update the CLI, then delegate to hermes_a2a:
  install      Install the A2A plugin into a Hermes profile.
  update       Update the A2A plugin in a Hermes profile.
  uninstall    Uninstall the A2A plugin from a Hermes profile.
  status       Show profile install state.
  doctor       Validate profile/config state.

Environment:
  HERMES_A2A_REPO        Optional. GitHub repo, default tickernelz/hermes-a2a.
  HERMES_A2A_REF         Optional. Branch/tag/commit, default main.
  HERMES_A2A_CACHE       Optional. Download cache directory.
  HERMES_A2A_INSTALL_DIR Optional. Persistent source root, default ~/.local/share/hermes-a2a.
  HERMES_A2A_BIN_DIR     Optional. CLI binary dir, default ~/.local/bin.
  HERMES_A2A_SOURCE_DIR  Optional. Local source directory, for development/tests.
EOF
}

is_valid_command() {
  case "${1:-}" in
    install|update|uninstall|status|doctor|--help|-h|help) return 0 ;;
    *) return 1 ;;
  esac
}

resolve_python() {
  if [ -n "${HERMES_PYTHON:-}" ] && [ -x "${HERMES_PYTHON:-}" ]; then
    printf '%s\n' "$HERMES_PYTHON"
    return 0
  fi
  if [ -n "${PYTHON:-}" ] && [ -x "${PYTHON:-}" ]; then
    printf '%s\n' "$PYTHON"
    return 0
  fi
  for candidate in \
    "${HERMES_HOME:-}/hermes-agent/venv/bin/python" \
    "${HERMES_HOME:-}/hermes-agent/.venv/bin/python" \
    "$HOME/.hermes/hermes-agent/venv/bin/python" \
    "$HOME/.hermes/hermes-agent/.venv/bin/python"; do
    if [ -x "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  command -v python3
}

copy_source() {
  local source_dir="$1"
  local dest_dir="$2"
  rm -rf "$dest_dir.tmp"
  mkdir -p "$dest_dir.tmp"
  tar -C "$source_dir" \
    --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='.pytest_cache' \
    -cf - . | tar -C "$dest_dir.tmp" -xf -
  rm -rf "$dest_dir"
  mv "$dest_dir.tmp" "$dest_dir"
}

download_source() {
  local repo="$1"
  local ref="$2"
  local dest_dir="$3"
  local cache_root="$4"
  local archive_url="https://codeload.github.com/${repo}/tar.gz/${ref}"
  local archive="$cache_root/${repo//\//-}-${ref//\//-}.tar.gz"
  local work_dir
  work_dir="$(mktemp -d "${TMPDIR:-/tmp}/hermes-a2a.XXXXXX")"
  trap 'rm -rf "$work_dir"' RETURN

  mkdir -p "$cache_root"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$archive_url" -o "$archive"
  elif command -v wget >/dev/null 2>&1; then
    wget -qO "$archive" "$archive_url"
  else
    echo "curl or wget is required" >&2
    exit 1
  fi
  tar -xzf "$archive" -C "$work_dir" --strip-components=1
  copy_source "$work_dir" "$dest_dir"
}

shell_single_quote() {
  local value="$1"
  printf "'"
  while [ -n "$value" ]; do
    case "$value" in
      *"'"*)
        printf '%s' "${value%%\'*}"
        printf '%s' "'\\''"
        value="${value#*\'}"
        ;;
      *)
        printf '%s' "$value"
        value=""
        ;;
    esac
  done
  printf "'"
}

install_wrapper() {
  local wrapper_path="$1"
  local current_dir="$2"
  local python_path="$3"
  local quoted_current quoted_python
  quoted_current="$(shell_single_quote "$current_dir")"
  quoted_python="$(shell_single_quote "$python_path")"
  mkdir -p "$(dirname "$wrapper_path")"
  cat > "$wrapper_path" <<EOF
#!/usr/bin/env bash
set -euo pipefail
SOURCE_DIR=$quoted_current
DEFAULT_PYTHON=$quoted_python
PYTHON="\${HERMES_PYTHON:-\${PYTHON:-\$DEFAULT_PYTHON}}"
if [ ! -x "\$PYTHON" ]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON="\$(command -v python3)"
  else
    echo "python3 is required" >&2
    exit 1
  fi
fi
export PYTHONPATH="\$SOURCE_DIR\${PYTHONPATH:+:\$PYTHONPATH}"
exec "\$PYTHON" -m hermes_a2a_cli "\$@"
EOF
  chmod 0755 "$wrapper_path"
}

COMMAND="${1:-}"
if [ -n "$COMMAND" ] && is_valid_command "$COMMAND"; then
  if [ "$COMMAND" = "--help" ] || [ "$COMMAND" = "-h" ] || [ "$COMMAND" = "help" ]; then
    usage
    exit 0
  fi
  shift
elif [ -n "$COMMAND" ]; then
  echo "Unknown command: $COMMAND" >&2
  usage >&2
  exit 2
fi

REPO="${HERMES_A2A_REPO:-$DEFAULT_REPO}"
REF="${HERMES_A2A_REF:-$DEFAULT_REF}"
INSTALL_ROOT="${HERMES_A2A_INSTALL_DIR:-$HOME/.local/share/hermes-a2a}"
BIN_DIR="${HERMES_A2A_BIN_DIR:-$HOME/.local/bin}"
CACHE_ROOT="${HERMES_A2A_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/hermes-a2a}"
CURRENT_DIR="$INSTALL_ROOT/current"
WRAPPER="$BIN_DIR/hermes_a2a"
PYTHON_PATH="$(resolve_python)"

mkdir -p "$INSTALL_ROOT" "$BIN_DIR"
if [ -n "${HERMES_A2A_SOURCE_DIR:-}" ]; then
  if [ ! -d "$HERMES_A2A_SOURCE_DIR/hermes_a2a_cli" ]; then
    echo "HERMES_A2A_SOURCE_DIR does not look like a hermes-a2a checkout: $HERMES_A2A_SOURCE_DIR" >&2
    exit 1
  fi
  copy_source "$HERMES_A2A_SOURCE_DIR" "$CURRENT_DIR"
else
  download_source "$REPO" "$REF" "$CURRENT_DIR" "$CACHE_ROOT"
fi
install_wrapper "$WRAPPER" "$CURRENT_DIR" "$PYTHON_PATH"

cat <<EOF
Installed hermes_a2a to $WRAPPER
Installed source to $CURRENT_DIR

Next:
  hermes_a2a doctor
  hermes_a2a install
EOF

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    cat <<EOF

Warning: $BIN_DIR is not in PATH.
Add this to your shell profile:
  export PATH="$BIN_DIR:\$PATH"
EOF
    ;;
esac

if [ -n "$COMMAND" ]; then
  exec "$WRAPPER" "$COMMAND" "$@"
fi
