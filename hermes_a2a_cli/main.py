from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:
    yaml = None

from . import __version__ as CLI_VERSION
from .installer import InstallError, install_profile, uninstall_profile

SCHEMA_VERSION = 1
STATE_DIR_NAME = "a2a"
STATE_FILE_NAME = "state.json"
DEFAULT_PLUGIN_VERSION = "0.0.0"


class CliError(RuntimeError):
    pass


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def default_home() -> Path:
    return Path.home() / ".hermes"


def profile_home(name: str) -> Path:
    if name in {"default", "main"}:
        return default_home()
    return default_home() / "profiles" / name


def find_profiles() -> list[dict[str, str]]:
    profiles: list[dict[str, str]] = []
    home = default_home()
    if (home / "config.yaml").exists():
        profiles.append({"name": "default", "home": str(home)})
    profiles_root = home / "profiles"
    if profiles_root.is_dir():
        for child in sorted(profiles_root.iterdir()):
            if child.is_dir() and (child / "config.yaml").exists():
                profiles.append({"name": child.name, "home": str(child)})
    return profiles


def resolve_home(args: argparse.Namespace) -> Path:
    explicit = getattr(args, "hermes_home", None) or os.environ.get("HERMES_HOME")
    if explicit:
        return Path(explicit).expanduser().resolve()
    profile = getattr(args, "profile", None)
    if profile:
        return profile_home(profile).expanduser().resolve()
    profiles = find_profiles()
    if not profiles:
        raise CliError("No Hermes profiles found. Use --profile NAME or --hermes-home PATH.")
    if len(profiles) == 1:
        return Path(profiles[0]["home"]).expanduser().resolve()
    if not sys.stdin.isatty():
        raise CliError("Refusing to choose automatically: multiple Hermes profiles found in non-interactive mode. Use --profile NAME or --hermes-home PATH.")
    print("Select target Hermes profile:", file=sys.stderr)
    for index, entry in enumerate(profiles, 1):
        print(f"  [{index}] {entry['name']} -> {entry['home']}", file=sys.stderr)
    choice = input("Profile number: ").strip()
    if not choice.isdigit():
        raise CliError("Invalid selection")
    selected = int(choice)
    if selected < 1 or selected > len(profiles):
        raise CliError("Invalid selection")
    return Path(profiles[selected - 1]["home"]).expanduser().resolve()


def load_yaml(path: Path) -> dict[str, Any]:
    if yaml is None:
        raise CliError("PyYAML is required")
    if not path.exists():
        raise CliError(f"Refusing to operate: {path} not found")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise CliError(f"{path} must contain a mapping")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CliError(f"Invalid state file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise CliError(f"State file {path} must contain an object")
    return data


def read_plugin_version(plugin_dir: Path) -> str | None:
    plugin_yaml = plugin_dir / "plugin.yaml"
    if not plugin_yaml.exists() or yaml is None:
        return None
    data = yaml.safe_load(plugin_yaml.read_text(encoding="utf-8")) or {}
    if isinstance(data, dict) and data.get("version"):
        return str(data["version"])
    return None


def source_plugin_version() -> str:
    return read_plugin_version(repo_root() / "plugin") or DEFAULT_PLUGIN_VERSION


def git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root(),
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return None
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def profile_payload(home: Path) -> dict[str, Any]:
    return {"home": str(home), "name": profile_name_for_home(home)}


def profile_name_for_home(home: Path) -> str:
    default = default_home().resolve()
    if home == default:
        return "default"
    try:
        rel = home.relative_to(default / "profiles")
        if len(rel.parts) == 1:
            return rel.parts[0]
    except ValueError:
        pass
    return home.name


def inspect_status(home: Path) -> dict[str, Any]:
    config = load_yaml(home / "config.yaml")
    plugin_dir = home / "plugins" / "a2a"
    state_path = home / STATE_DIR_NAME / STATE_FILE_NAME
    state = load_json(state_path)
    a2a_config = config.get("a2a") if isinstance(config.get("a2a"), dict) else {}
    server = a2a_config.get("server") if isinstance(a2a_config.get("server"), dict) else {}
    plugins = config.get("plugins") if isinstance(config.get("plugins"), dict) else {}
    enabled_plugins = plugins.get("enabled") if isinstance(plugins.get("enabled"), list) else []
    return {
        "profile": profile_payload(home),
        "installed": plugin_dir.exists(),
        "plugin_dir": str(plugin_dir),
        "plugin_version": read_plugin_version(plugin_dir),
        "state_path": str(state_path),
        "state": state,
        "config": {
            "plugin_enabled": "a2a" in enabled_plugins,
            "a2a_enabled": bool(a2a_config.get("enabled", "a2a" in enabled_plugins)),
            "server_port": server.get("port"),
            "public_url": server.get("public_url"),
            "require_auth": server.get("require_auth"),
        },
    }


def print_result(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, sort_keys=True))
        return
    command = payload.get("command")
    home = payload.get("profile", {}).get("home")
    print(f"hermes-a2a {command or 'status'}")
    if home:
        print(f"profile: {home}")
    if "installed" in payload:
        print(f"installed: {payload['installed']}")
    if payload.get("plugin_version"):
        print(f"plugin_version: {payload['plugin_version']}")
    for item in payload.get("plan", []):
        print(f"- {item}")
    if payload.get("restart_required"):
        print("restart_required: true")


def backup_path(path: Path, backup_root: Path) -> Path | None:
    if not path.exists():
        return None
    backup_root.mkdir(parents=True, exist_ok=True)
    target = backup_root / path.name
    if path.is_dir():
        shutil.copytree(path, target)
    else:
        shutil.copy2(path, target)
    return target


def install_payload(home: Path, *, dry_run: bool) -> dict[str, Any]:
    config_path = home / "config.yaml"
    load_yaml(config_path)
    plugin_source = repo_root() / "plugin"
    dashboard_source = repo_root() / "dashboard"
    state_path = home / STATE_DIR_NAME / STATE_FILE_NAME
    version = source_plugin_version()
    plan = ["install plugin payload", "update profile config/env", "write profile state manifest", "no gateway restart"]
    result = install_profile(home, plugin_source, dashboard_source, dry_run=dry_run)
    if dry_run:
        return {
            "command": "install",
            "dry_run": True,
            "changed": False,
            "restart_required": True,
            "profile": profile_payload(home),
            "plan": plan,
            "messages": result["messages"],
        }
    stamp = time.strftime("%Y%m%d%H%M%S")
    backup_root = home / STATE_DIR_NAME / "backups" / stamp
    backed_up = []
    target = backup_path(state_path, backup_root)
    if target is not None:
        backed_up.append(str(target))
    state = {
        "schema_version": SCHEMA_VERSION,
        "installed_version": version,
        "source": {"type": "local_checkout", "path": str(repo_root()), "commit": git_commit()},
        "migration_version": version,
        "last_backup": stamp if backed_up else None,
        "migration_ledger": [{"id": f"install_{version.replace('.', '_')}", "from": None, "to": version, "status": "success", "backup_id": stamp if backed_up else None}],
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    write_json(state_path, state)
    return {
        "command": "install",
        "dry_run": False,
        "changed": True,
        "restart_required": True,
        "profile": profile_payload(home),
        "plan": plan,
        "state_path": str(state_path),
        "backup_id": stamp if backed_up else None,
        "messages": result["messages"],
    }


def command_status(args: argparse.Namespace) -> int:
    home = resolve_home(args)
    payload = inspect_status(home)
    payload["command"] = "status"
    print_result(payload, args.json)
    return 0


def command_doctor(args: argparse.Namespace) -> int:
    home = resolve_home(args)
    status = inspect_status(home)
    checks = []
    checks.append({"name": "config", "ok": (home / "config.yaml").exists()})
    checks.append({"name": "plugin", "ok": bool(status["installed"])})
    checks.append({"name": "state", "ok": (home / STATE_DIR_NAME / STATE_FILE_NAME).exists()})
    ok = all(check["ok"] for check in checks)
    payload = {"command": "doctor", "profile": profile_payload(home), "ok": ok, "checks": checks}
    print_result(payload, args.json)
    return 0 if ok else 1


def command_install(args: argparse.Namespace) -> int:
    home = resolve_home(args)
    if not args.dry_run and not args.yes and not sys.stdin.isatty():
        raise CliError("Refusing destructive install in non-interactive mode without --yes")
    payload = install_payload(home, dry_run=args.dry_run)
    print_result(payload, args.json or True)
    return 0


def command_update(args: argparse.Namespace) -> int:
    return command_install(args)


def command_uninstall(args: argparse.Namespace) -> int:
    home = resolve_home(args)
    if not (home / "config.yaml").exists():
        raise CliError(f"Refusing to uninstall: {home / 'config.yaml'} not found")
    plan = ["remove plugin payload", "preserve config/env/state", "no gateway restart"]
    if args.dry_run:
        result = uninstall_profile(home, dry_run=True)
        payload = {"command": "uninstall", "dry_run": True, "changed": False, "profile": profile_payload(home), "plugin_dir": result["plugin_dir"], "plan": plan, "messages": result["messages"]}
        print_result(payload, args.json or True)
        return 0
    if not args.yes and not sys.stdin.isatty():
        raise CliError("Refusing destructive uninstall in non-interactive mode without --yes")
    result = uninstall_profile(home, dry_run=False)
    payload = {"command": "uninstall", "dry_run": False, "changed": result["changed"], "profile": profile_payload(home), "plugin_dir": result["plugin_dir"], "plan": plan, "restart_required": True, "messages": result["messages"]}
    print_result(payload, args.json or True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes-a2a")
    parser.add_argument("--version", action="version", version=f"hermes-a2a {CLI_VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, handler in [
        ("status", command_status),
        ("doctor", command_doctor),
        ("install", command_install),
        ("update", command_update),
        ("uninstall", command_uninstall),
    ]:
        cmd = sub.add_parser(name)
        cmd.add_argument("--profile")
        cmd.add_argument("--hermes-home")
        cmd.add_argument("--json", action="store_true")
        if name in {"install", "update", "uninstall"}:
            cmd.add_argument("--dry-run", action="store_true")
            cmd.add_argument("--yes", action="store_true")
        if name == "update":
            cmd.add_argument("--to", dest="target_version")
        cmd.set_defaults(func=handler)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (CliError, InstallError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
