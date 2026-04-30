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
from .installer import InstallError, install_profile, resolve_wake_session, uninstall_profile
from .migrations import build_migration_plan, migrate_config_unify
from .multi_install import HermesProfile, build_generated_profiles, discover_profiles, infer_wake_session_from_history, preview_generated_profiles
from .migrations.registry import MigrationPlanError
from .state import StateError, build_install_state, load_state, state_path, write_state
from .wizard import collect_wizard_answers

SCHEMA_VERSION = 1
STATE_DIR_NAME = "a2a"
STATE_FILE_NAME = "state.json"
DEFAULT_PLUGIN_VERSION = "0.0.0"


class CliError(RuntimeError):
    pass


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def default_home() -> Path:
    return Path(os.environ.get("HERMES_A2A_ROOT_HOME", str(Path.home() / ".hermes"))).expanduser()


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
    path = state_path(home)
    state = load_state(home)
    a2a_config = config.get("a2a") if isinstance(config.get("a2a"), dict) else {}
    server = a2a_config.get("server") if isinstance(a2a_config.get("server"), dict) else {}
    wake = a2a_config.get("wake") if isinstance(a2a_config.get("wake"), dict) else {}
    session = resolve_wake_session(a2a_config)
    ref = wake.get("session_ref") if isinstance(wake.get("session_ref"), dict) else {}
    plugins = config.get("plugins") if isinstance(config.get("plugins"), dict) else {}
    enabled_plugins = plugins.get("enabled") if isinstance(plugins.get("enabled"), list) else []
    return {
        "profile": profile_payload(home),
        "installed": plugin_dir.exists(),
        "plugin_dir": str(plugin_dir),
        "plugin_version": read_plugin_version(plugin_dir),
        "state_path": str(path),
        "state": state,
        "config": {
            "canonical": bool(a2a_config.get("identity") and server and wake),
            "plugin_enabled": "a2a" in enabled_plugins,
            "a2a_enabled": bool(a2a_config.get("enabled", "a2a" in enabled_plugins)),
            "server_port": server.get("port"),
            "public_url": server.get("public_url"),
            "require_auth": server.get("require_auth"),
            "wake_port": wake.get("port"),
            "wake_platform": session.get("platform") or ref.get("platform"),
            "wake_chat_id": session.get("chat_id") or ref.get("chat_id"),
            "wake_session_ref": wake.get("session_ref"),
            "wake_actor": (session.get("actor") or {}).get("name") if isinstance(session.get("actor"), dict) else None,
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
    if payload.get("mode"):
        print(f"mode: {payload['mode']}")
    if "installed" in payload:
        print(f"installed: {payload['installed']}")
    if payload.get("plugin_version"):
        print(f"plugin_version: {payload['plugin_version']}")
    if payload.get("preview"):
        print("preview:")
        for item in payload["preview"]:
            print(f"- {item}")
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


def _prompt(question: str, default: str) -> str:
    suffix = f" [{default}]" if default else ""
    return input(f"{question}{suffix}: ")


def _confirm(question: str, default: bool) -> bool:
    suffix = "Y/n" if default else "y/N"
    value = input(f"{question} [{suffix}]: ").strip().lower()
    if not value:
        return default
    return value in {"y", "yes", "1", "true", "on"}


def install_payload(home: Path, *, dry_run: bool, answers=None, migration_state: dict[str, Any] | None = None, write_state_manifest: bool = True) -> dict[str, Any]:
    config_path = home / "config.yaml"
    load_yaml(config_path)
    plugin_source = repo_root() / "plugin"
    dashboard_source = repo_root() / "dashboard"
    path = state_path(home)
    version = source_plugin_version()
    plan = ["install plugin payload", "update profile config/env", "write profile state manifest", "no gateway restart"]
    result = install_profile(home, plugin_source, dashboard_source, dry_run=dry_run, answers=answers)
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
    backed_up = []
    if write_state_manifest:
        backup_root = home / STATE_DIR_NAME / "backups" / stamp
        target = backup_path(path, backup_root)
        if target is not None:
            backed_up.append(str(target))
        state = build_install_state(
            installed_version=version,
            source={"type": "local_checkout", "path": str(repo_root()), "commit": git_commit()},
            backup_id=stamp if backed_up else None,
        )
        if migration_state:
            state["migration_version"] = migration_state.get("migration_version", state["migration_version"])
            state["migration_ledger"] = migration_state.get("migration_ledger", state["migration_ledger"])
        write_state(home, state)
    return {
        "command": "install",
        "dry_run": False,
        "changed": True,
        "restart_required": True,
        "profile": profile_payload(home),
        "plan": plan,
        "state_path": str(path),
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
    checks.append({"name": "canonical_a2a_config", "ok": bool(status["config"].get("canonical"))})
    checks.append({"name": "plugin", "ok": bool(status["installed"])})
    checks.append({"name": "state", "ok": (home / STATE_DIR_NAME / STATE_FILE_NAME).exists()})
    ok = all(check["ok"] for check in checks)
    payload = {"command": "doctor", "profile": profile_payload(home), "ok": ok, "checks": checks}
    print_result(payload, args.json)
    return 0 if ok else 1


def _parse_selection(raw: str, profiles: list[HermesProfile]) -> list[HermesProfile]:
    selected: list[HermesProfile] = []
    by_name = {profile.name.lower(): profile for profile in profiles}
    tokens = [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]
    if not tokens:
        return selected
    if any(token.lower() == "all" for token in tokens):
        return list(profiles)
    for token in tokens:
        match = None
        if token.isdigit():
            index = int(token) - 1
            if 0 <= index < len(profiles):
                match = profiles[index]
        if match is None:
            match = by_name.get(token.lower())
        if match is not None and match not in selected:
            selected.append(match)
    return selected


def _prompt_multi_profile_selection(profiles: list[HermesProfile]) -> list[HermesProfile]:
    print("Select Hermes profiles to connect with A2A:", file=sys.stderr)
    for index, profile in enumerate(profiles, 1):
        print(f"  [{index}] {profile.name} -> {profile.home}", file=sys.stderr)
    print("Tip: choose 2+ profiles for a connected topology, e.g. '1,2'. Type 'all' to select all shown profiles.", file=sys.stderr)
    print("Safety: only selected profiles are changed; no gateway restart is performed.", file=sys.stderr)
    selected = _parse_selection(input("Profiles to configure: ").strip(), profiles)
    if not selected:
        raise CliError("No profiles selected")
    return selected


def _wake_defaults_from_prompt(inferred: dict[str, str] | None = None) -> dict[str, str]:
    inferred = inferred or {}
    if inferred:
        print("Detected a recent Hermes gateway session for A2A wake routing:", file=sys.stderr)
        print(
            f"  platform={inferred.get('platform')} chat_id={inferred.get('chat_id')} "
            f"thread_id={inferred.get('thread_id') or '-'} actor_id={inferred.get('actor_id')} "
            f"actor_name={inferred.get('actor_name')}",
            file=sys.stderr,
        )
        if _confirm("Use this detected wake session", True):
            return inferred
    if not _confirm("Configure one shared wake session for selected profiles", True):
        return {}
    print("Wake session = where Hermes receives the internal A2A wake event.", file=sys.stderr)
    print("chat/channel ID selects the room/thread; actor ID selects the Hermes session owner and is not auth/allowlist.", file=sys.stderr)
    platform = _prompt("Wake platform (discord/telegram/custom/none)", inferred.get("platform", "discord")).strip()
    if platform == "none":
        return {}
    defaults = {
        "platform": platform,
        "chat_id": _prompt("Wake chat/channel ID", inferred.get("chat_id", "")).strip(),
        "chat_type": _prompt("Wake chat type", inferred.get("chat_type", "group" if platform == "discord" else "dm")).strip() or "dm",
        "actor_id": _prompt("Wake actor ID (your Discord/Telegram user ID; session selector, not auth)", inferred.get("actor_id", "")).strip(),
        "actor_name": _prompt("Wake actor name", inferred.get("actor_name", "user")).strip() or "user",
    }
    if platform == "telegram":
        defaults["thread_id"] = _prompt("Telegram thread/topic ID (optional)", inferred.get("thread_id", "")).strip()
    elif inferred.get("thread_id"):
        defaults["thread_id"] = inferred["thread_id"]
    return defaults


def command_install(args: argparse.Namespace) -> int:
    if args.multi:
        return command_install_multi(args)
    home = resolve_home(args)
    if not args.dry_run and not args.yes and not sys.stdin.isatty():
        raise CliError("Refusing destructive install in non-interactive mode without --yes")
    answers = None
    if not args.yes and sys.stdin.isatty():
        answers = collect_wizard_answers(
            profile_name=profile_name_for_home(home),
            default_port=int(getattr(args, "a2a_port", None) or os.environ.get("A2A_PORT") or 41731),
            default_webhook_port=int(getattr(args, "webhook_port", None) or os.environ.get("WEBHOOK_PORT") or 47644),
            prompt_fn=_prompt,
            confirm_fn=_confirm,
        )
    payload = install_payload(home, dry_run=args.dry_run, answers=answers)
    print_result(payload, args.json or True)
    return 0


def command_install_multi(args: argparse.Namespace) -> int:
    if not args.dry_run and not args.yes and not sys.stdin.isatty():
        raise CliError("Refusing destructive multi-profile install in non-interactive mode without --yes")
    root_home = default_home().resolve()
    profiles = discover_profiles(root_home)
    if not profiles:
        raise CliError("No Hermes profiles found")
    if args.profile:
        wanted = {item.strip() for item in args.profile.split(",") if item.strip()}
        selected = [profile for profile in profiles if profile.name in wanted]
    elif sys.stdin.isatty() and not args.yes:
        selected = _prompt_multi_profile_selection(profiles)
    else:
        selected = profiles if len(profiles) <= 2 else [profile for profile in profiles if profile.name == "default"]
    if not selected:
        raise CliError("No matching Hermes profiles selected")
    inferred_wake = {}
    for profile in selected:
        inferred_wake = infer_wake_session_from_history(profile.home)
        if inferred_wake:
            break
    wake_defaults = _wake_defaults_from_prompt(inferred_wake) if sys.stdin.isatty() and not args.yes else inferred_wake
    generated = build_generated_profiles(selected, wake_defaults=wake_defaults)
    preview = preview_generated_profiles(generated)
    if sys.stdin.isatty() and not args.yes:
        print("A2A install preview:", file=sys.stderr)
        for line in preview:
            print(f"  - {line}", file=sys.stderr)
        if not args.dry_run and not _confirm("Apply this multi-profile A2A config", False):
            raise CliError("Install cancelled")
    results = []
    for item in generated:
        results.append(install_payload(item.source.home, dry_run=args.dry_run, answers=item.answers, write_state_manifest=not args.dry_run))
    payload = {
        "command": "install",
        "mode": "multi-profile",
        "dry_run": args.dry_run,
        "changed": any(result.get("changed") for result in results),
        "restart_required": not args.dry_run,
        "profiles": [profile_payload(item.source.home) for item in generated],
        "preview": preview,
        "results": results,
    }
    print_result(payload, args.json or True)
    return 0


def run_update_migrations(home: Path, target_version: str, *, dry_run: bool) -> dict[str, Any]:
    state = load_state(home)
    from_version = str(state.get("migration_version") or state.get("installed_version") or "0.2.2")
    try:
        plan = build_migration_plan(from_version, target_version)
    except MigrationPlanError:
        return {"changed": False, "migration_steps": [], "from_version": from_version, "to_version": target_version}
    changed = False
    outputs = []
    for step in plan:
        step.precheck(home)
        if dry_run:
            outputs.append({"id": step.id, "dry_run": True})
            continue
        backup_id = time.strftime("%Y%m%d%H%M%S")
        step.apply(home, backup_id)
        step.verify(home)
        outputs.append({"id": step.id, "backup_id": backup_id})
        changed = True
    if changed:
        new_state = dict(state)
        new_state.setdefault("schema_version", SCHEMA_VERSION)
        new_state["migration_version"] = target_version
        new_state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        ledger = new_state.setdefault("migration_ledger", [])
        if isinstance(ledger, list):
            for item in outputs:
                ledger.append({"id": item["id"], "from": from_version, "to": target_version, "status": "success", "backup_id": item.get("backup_id")})
        write_state(home, new_state)
    return {
        "changed": changed,
        "migration_steps": outputs,
        "from_version": from_version,
        "to_version": target_version,
        "state": new_state if changed else state,
    }


def command_update(args: argparse.Namespace) -> int:
    home = resolve_home(args)
    target_version = args.target_version or source_plugin_version()
    migration_result = run_update_migrations(home, target_version, dry_run=args.dry_run)
    if args.dry_run:
        payload = install_payload(home, dry_run=True, answers=None)
        payload["command"] = "update"
        payload["migrations"] = migration_result
        print_result(payload, args.json or True)
        return 0
    install_result = install_payload(home, dry_run=False, answers=None, migration_state=migration_result.get("state"))
    install_result["command"] = "update"
    install_result["migrations"] = migration_result
    print_result(install_result, args.json or True)
    return 0


def command_migrate(args: argparse.Namespace) -> int:
    home = resolve_home(args)
    if args.migration != "config-unify":
        raise CliError(f"Unknown migration: {args.migration}")
    if not args.dry_run and not args.yes and not sys.stdin.isatty():
        raise CliError("Refusing destructive migration in non-interactive mode without --yes")
    result = migrate_config_unify(home, dry_run=args.dry_run)
    payload = {"command": "migrate config-unify", "profile": profile_payload(home), **result, "restart_required": not args.dry_run}
    print_result(payload, args.json or True)
    return 0


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
        if name == "install":
            cmd.add_argument("--multi", action="store_true", help="configure multiple Hermes profiles and connect them")
        if name == "update":
            cmd.add_argument("--to", dest="target_version")
        cmd.set_defaults(func=handler)
    migrate = sub.add_parser("migrate")
    migrate.add_argument("migration")
    migrate.add_argument("--profile")
    migrate.add_argument("--hermes-home")
    migrate.add_argument("--json", action="store_true")
    migrate.add_argument("--dry-run", action="store_true")
    migrate.add_argument("--yes", action="store_true")
    migrate.set_defaults(func=command_migrate)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (CliError, InstallError, StateError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
