from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .bundles import (
    manifest_dict,
    markdown_manifest,
    mix_manifest,
    ranked_manifest,
    stack_manifest,
)
from .code_intelligence import (
    current_directory_is_mix_project,
    ensure_intelligence_index,
    run_dexter_cli,
    run_dexterity_map,
    run_dexterity_query,
)
from .config import (
    AtlasPaths,
    AtlasProfileState,
    AtlasSettings,
    ensure_state,
    get_paths,
    load_profile_state,
    load_settings,
    mark_profile_customized,
    save_profile_state,
    save_settings,
)
from .dashboard import render_dashboard, render_topic_help
from .inbox import (
    InboxEntry,
    create_entry,
    infer_promotion_kind,
    iter_entries,
    promote_auto,
    promote_entry,
    review_daily,
    review_inbox,
)
from .index_watcher import (
    DEFAULT_DEBOUNCE_MS,
    DEFAULT_POLL_INTERVAL_MS,
    DEFAULT_TTL_MS,
    IndexWatchTarget,
    refresh_projects,
    resolve_watch_targets,
    start_watch,
    status_payload,
    stop_watch,
)
from .intelligence_service import (
    serve as serve_intelligence_service,
)
from .intelligence_service import (
    start_service,
    status_service,
    stop_service,
)
from .notes import NoteGraphSyncResult, build_graph, create_note, sync_note_graph
from .profiles import DEFAULT_INSTALL_PROFILE, get_profile, list_profiles, profile_dict
from .ranked_context import (
    RankedContextsSeedResult,
    RankedRuntime,
    ensure_ranked_contexts_config,
    load_prepared_ranked_manifest,
    load_ranked_contexts_payload,
    load_ranked_default_runtime,
    prepare_ranked_manifest,
    prepared_manifest_dict,
    ranked_index_freshness_payload,
    read_ranked_contexts_text,
)
from .registry import (
    ProjectRecord,
    RegistryScanResult,
    add_alias,
    add_root,
    load_registry,
    remove_alias,
    remove_root,
    resolve_or_placeholder,
    resolve_project_ref,
    scan_registry,
    scan_registry_with_stats,
)
from .runtime import (
    AtlasCliError,
    ExitCode,
    append_event,
    map_exception,
    mutation_lock,
    print_json,
    success,
)
from .shell import install_bash_snippet, render_bash_snippet
from .templates import daily_note_template
from .util import collect_note_files, command_exists, now_local, open_in_editor, search_text


@dataclass(frozen=True)
class CommandOutcome:
    command: str
    data: dict[str, Any]
    text: str | None = None


def _write_text(text: str | None) -> None:
    if text is None:
        return
    sys.stdout.write(text)
    if text and not text.endswith("\n"):
        sys.stdout.write("\n")


def _write_progress(message: str) -> None:
    sys.stderr.write(message)
    if not message.endswith("\n"):
        sys.stderr.write("\n")
    sys.stderr.flush()


def _ranked_context_usage() -> str:
    return "Usage: atlas context ranked <config-name>|prepare <config-name>|status <config-name>"


def _strip_global_flag(argv: list[str], flag: str) -> tuple[bool, list[str]]:
    found = False
    filtered: list[str] = []
    for item in argv:
        if item == flag and not found:
            found = True
            continue
        filtered.append(item)
    return found, filtered


def _guess_command(argv: list[str]) -> str:
    if not argv:
        return "dashboard"
    command = argv[0]
    scoped_commands = {
        "registry",
        "review",
        "promote",
        "note",
        "context",
        "index",
        "config",
        "dexter",
    }
    if command in scoped_commands and len(argv) > 1:
        return f"{command}.{argv[1]}"
    return command


def _project_dict(record: ProjectRecord) -> dict[str, Any]:
    return asdict(record)


def _entry_dict(entry: InboxEntry) -> dict[str, Any]:
    data = asdict(entry)
    data["suggested_kind"] = infer_promotion_kind(entry)
    return data


def _sync_dict(result: NoteGraphSyncResult) -> dict[str, Any]:
    return asdict(result)


def _profile_state_dict(state: AtlasProfileState | None) -> dict[str, Any] | None:
    if state is None:
        return None
    return asdict(state)


def _ranked_contexts_dict(result: RankedContextsSeedResult) -> dict[str, Any]:
    return {
        "path": str(result.path),
        "profile": result.profile_name,
        "status": result.status,
    }


def _settings_dict(paths: AtlasPaths) -> dict[str, Any]:
    settings = ensure_state(paths)
    return {
        "data_home": settings.data_home,
        "code_root": settings.code_root,
        "project_roots": settings.project_roots,
        "self_owners": settings.self_owners,
        "auto_sync_relationships": settings.auto_sync_relationships,
        "review_window_days": settings.review_window_days,
    }


def _paths_dict(paths: AtlasPaths) -> dict[str, Any]:
    return {
        "config_home": str(paths.config_home),
        "state_home": str(paths.state_home),
        "data_home": str(paths.data_home),
        "code_root": str(paths.code_root) if paths.code_root is not None else None,
        "settings_path": str(paths.settings_path),
        "profile_state_path": str(paths.profile_state_path),
        "ranked_contexts_path": str(paths.ranked_contexts_path),
        "ranked_contexts_state_path": str(paths.ranked_contexts_state_path),
        "ranked_context_cache_root": str(paths.ranked_context_cache_root),
        "index_watcher_root": str(paths.index_watcher_root),
        "index_watcher_state_path": str(paths.index_watcher_state_path),
        "index_watcher_pid_path": str(paths.index_watcher_pid_path),
        "bash_shell_path": str(paths.bash_shell_path),
    }


def _relationships_meta(paths: AtlasPaths) -> dict[str, Any]:
    if not paths.relationships_path.is_file():
        return {}
    payload = json.loads(paths.relationships_path.read_text(encoding="utf-8"))
    meta = payload.get("meta", {})
    return meta if isinstance(meta, dict) else {}


def _status_data(paths: AtlasPaths) -> dict[str, Any]:
    ensure_state(paths)
    stamp = now_local().strftime("%Y%m%d")
    today_note = paths.docs_root / stamp / "index.md"
    profile_state = load_profile_state(paths)
    open_entries = [entry for entry in iter_entries(paths) if entry.status == "open"]
    today_entries = [entry for entry in iter_entries(paths, day=stamp) if entry.status == "open"]
    auto_entries = [entry for entry in open_entries if infer_promotion_kind(entry) is not None]
    registry = load_registry(paths)
    recent_projects = sorted(
        registry,
        key=lambda record: (
            Path(record.path).stat().st_mtime if Path(record.path).exists() else 0,
            record.name.lower(),
        ),
        reverse=True,
    )[:10]
    return {
        "storage": {
            "config_home": str(paths.config_home),
            "data_home": str(paths.data_home),
            "state_home": str(paths.state_home),
            "events_path": str(paths.events_path),
        },
        "profile": _profile_state_dict(profile_state),
        "settings": _settings_dict(paths),
        "today": {
            "stamp": stamp,
            "note_path": str(today_note),
            "note_exists": today_note.exists(),
        },
        "registry": {
            "project_count": len(registry),
            "recent_projects": [_project_dict(record) for record in recent_projects],
        },
        "inbox": {
            "open_count": len(open_entries),
            "today_open_count": len(today_entries),
            "auto_promotable_count": len(auto_entries),
            "open_entries": [_entry_dict(entry) for entry in open_entries[:20]],
        },
        "indexes": {
            "relationships_meta": _relationships_meta(paths),
        },
    }


def _next_action(paths: AtlasPaths) -> dict[str, Any]:
    status = _status_data(paths)
    registry_count = int(status["registry"]["project_count"])
    today_note_exists = bool(status["today"]["note_exists"])
    auto_count = int(status["inbox"]["auto_promotable_count"])
    open_count = int(status["inbox"]["open_count"])
    roots = list(status["settings"]["project_roots"])

    if roots and registry_count == 0:
        return {
            "action": "registry_scan",
            "command": "atlas registry scan",
            "reason": "The project registry is empty.",
            "priority": 100,
        }
    if auto_count > 0:
        return {
            "action": "promote_auto",
            "command": "atlas promote auto",
            "reason": (
                f"{auto_count} inbox entr{'y' if auto_count == 1 else 'ies'} can be auto-promoted."
            ),
            "priority": 80,
        }
    if not today_note_exists:
        return {
            "action": "today_note",
            "command": "atlas today",
            "reason": "Today does not have a daily note yet.",
            "priority": 75,
        }
    if open_count > 0:
        return {
            "action": "review_inbox",
            "command": "atlas review inbox",
            "reason": f"{open_count} inbox entr{'y' if open_count == 1 else 'ies'} remain open.",
            "priority": 70,
        }
    return {
        "action": "capture",
        "command": 'atlas capture "..."',
        "reason": "Atlas is in a steady state.",
        "priority": 10,
    }


def _render_status_text(data: dict[str, Any]) -> str:
    recent = data["registry"]["recent_projects"]
    recent_lines = (
        "\n".join(f"  - {item['name']} ({item['path']})" for item in recent) or "  - none"
    )
    profile = data["profile"]["name"] if data.get("profile") else "(custom)"
    return (
        "atlas status\n\n"
        f"config:{' ' * 2}{data['storage']['config_home']}\n"
        f"data:  {data['storage']['data_home']}\n"
        f"state: {data['storage']['state_home']}\n\n"
        f"profile: {profile}\n"
        "today: "
        f"{data['today']['stamp']} "
        f"({'present' if data['today']['note_exists'] else 'missing'})\n"
        f"daily note: {data['today']['note_path']}\n"
        f"registry projects: {data['registry']['project_count']}\n"
        f"open inbox: {data['inbox']['open_count']}\n"
        f"auto-promotable: {data['inbox']['auto_promotable_count']}\n"
        "recent projects:\n"
        f"{recent_lines}"
    )


def _read_text_arg(
    args_text: list[str] | None,
    use_stdin: bool,
    *,
    empty_message: str,
) -> str:
    parts: list[str] = []
    if args_text:
        joined = " ".join(args_text).strip()
        if joined:
            parts.append(joined)
    if use_stdin:
        stdin_text = sys.stdin.read().strip()
        if stdin_text:
            parts.append(stdin_text)
    text = "\n\n".join(parts).strip()
    if not text:
        raise SystemExit(empty_message)
    return text


def _copy_bundle(bundle_path: str, output: str | None) -> str:
    source = Path(bundle_path)
    if output is None:
        return source.read_text(encoding="utf-8")
    target = Path(output).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return str(target)


def _apply_settings(
    paths: AtlasPaths,
    settings: AtlasSettings,
    profile_state: AtlasProfileState | None = None,
) -> AtlasSettings:
    save_settings(paths, settings)
    if profile_state is not None:
        save_profile_state(paths, profile_state)
    return load_settings(get_paths())


def _profile_settings(profile_name: str) -> AtlasSettings:
    return get_profile(profile_name).settings


def _dashboard_main(_: list[str], __: bool) -> CommandOutcome:
    paths = get_paths()
    settings = ensure_state(paths)
    registry = load_registry(paths)
    if not registry and settings.project_roots:
        registry = scan_registry(paths, settings)
    data = {
        "storage": {
            "data_home": str(paths.data_home),
            "state_home": str(paths.state_home),
        },
        "settings": _settings_dict(paths),
        "registry": {"project_count": len(registry)},
    }
    return CommandOutcome("dashboard", data, render_dashboard(paths, settings, registry))


def _help_main(argv: list[str], _: bool) -> CommandOutcome:
    parser = argparse.ArgumentParser(prog="atlas help", description="Show atlas help topics.")
    parser.add_argument("topic", nargs="?", default="")
    args = parser.parse_args(argv)
    if not args.topic:
        return _dashboard_main([], False)
    text = render_topic_help(args.topic)
    return CommandOutcome("help", {"topic": args.topic, "text": text}, text)


def _menu_main(argv: list[str], json_mode: bool) -> CommandOutcome:
    argparse.ArgumentParser(
        prog="atlas menu",
        description="Open the atlas interactive menu.",
    ).parse_args(argv)
    options = [
        "Show dashboard",
        "Initialize atlas",
        "Open today's note path",
        "Capture inbox entry",
        "Review inbox",
        "Scan registry",
        "List registry",
        "Resolve project",
        "Quit",
    ]
    if json_mode:
        return CommandOutcome("menu", {"options": options}, None)
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        lines = ["atlas menu", ""]
        for index, option in enumerate(options, start=1):
            lines.append(f"{index}. {option}")
        return CommandOutcome("menu", {"options": options}, "\n".join(lines))

    print("atlas menu")
    for index, option in enumerate(options, start=1):
        print(f"{index}. {option}")
    choice = input("\nSelection [1]: ").strip() or "1"
    if choice == "1":
        return _dashboard_main([], False)
    if choice == "2":
        return _init_main([], False)
    if choice == "3":
        return _today_main(["--print"], False)
    if choice == "4":
        text = input("Note: ").strip()
        if not text:
            raise SystemExit("No note text provided.")
        return _capture_main([text], False)
    if choice == "5":
        return _review_main(["inbox"], False)
    if choice == "6":
        return _registry_main(["scan"], False)
    if choice == "7":
        return _registry_main(["list"], False)
    if choice == "8":
        reference = input("Project ref: ").strip()
        return _resolve_main([reference], False)
    return CommandOutcome("menu", {"options": options, "selection": "quit"}, "")


def _init_main(argv: list[str], _: bool) -> CommandOutcome:
    parser = argparse.ArgumentParser(
        prog="atlas init",
        description="Initialize atlas storage and settings.",
    )
    parser.add_argument(
        "--scan", action="store_true", help="Scan project roots after bootstrapping."
    )
    args = parser.parse_args(argv)
    paths = get_paths()
    with mutation_lock(paths, "state"):
        settings = ensure_state(paths)
        registry = (
            scan_registry_with_stats(paths, settings=settings)
            if args.scan
            else RegistryScanResult(
                projects=load_registry(paths), scanned_roots=[], reused_roots=[]
            )
        )
    data = {
        "state_home": str(paths.state_home),
        "data_home": str(paths.data_home),
        "settings": _settings_dict(paths),
        "registry": {
            "project_count": len(registry.projects),
            "scanned_roots": registry.scanned_roots,
            "reused_roots": registry.reused_roots,
        },
    }
    text = (
        f"Initialized atlas at {paths.state_home} with {len(registry.projects)} projects."
        if args.scan
        else f"Initialized atlas at {paths.state_home}."
    )
    return CommandOutcome("init", data, text)


def _config_main(argv: list[str], _: bool) -> CommandOutcome:
    parser = argparse.ArgumentParser(prog="atlas config", description="Manage atlas settings.")
    subparsers = parser.add_subparsers(dest="action")
    subparsers.add_parser("show")

    set_parser = subparsers.add_parser("set")
    set_parser.add_argument(
        "key",
        choices=("data_home", "code_root", "review_window_days", "auto_sync_relationships"),
    )
    set_parser.add_argument("value")

    roots_parser = subparsers.add_parser("roots")
    roots_subparsers = roots_parser.add_subparsers(dest="roots_action")
    roots_add_parser = roots_subparsers.add_parser("add")
    roots_add_parser.add_argument("path")
    roots_remove_parser = roots_subparsers.add_parser("remove")
    roots_remove_parser.add_argument("path")

    profile_parser = subparsers.add_parser("profile")
    profile_subparsers = profile_parser.add_subparsers(dest="profile_action")
    profile_subparsers.add_parser("list")
    profile_show_parser = profile_subparsers.add_parser("show")
    profile_show_parser.add_argument("name")
    profile_subparsers.add_parser("current")
    profile_use_parser = profile_subparsers.add_parser("use")
    profile_use_parser.add_argument("name")

    shell_parser = subparsers.add_parser("shell")
    shell_subparsers = shell_parser.add_subparsers(dest="shell_action")
    shell_show_parser = shell_subparsers.add_parser("show")
    shell_show_parser.add_argument("--profile")
    shell_install_parser = shell_subparsers.add_parser("install")
    shell_install_parser.add_argument("--profile")
    shell_install_parser.add_argument("--target")

    ranked_parser = subparsers.add_parser("ranked")
    ranked_subparsers = ranked_parser.add_subparsers(dest="ranked_action")
    ranked_subparsers.add_parser("path")
    ranked_subparsers.add_parser("show")
    ranked_install_parser = ranked_subparsers.add_parser("install")
    ranked_install_parser.add_argument("--profile")
    ranked_install_parser.add_argument("--force", action="store_true")

    args = parser.parse_args(argv)
    paths = get_paths()
    ensure_state(paths)

    if args.action in {None, "show"}:
        profile_state = load_profile_state(paths)
        data = {
            "paths": _paths_dict(paths),
            "settings": _settings_dict(paths),
            "profile": _profile_state_dict(profile_state),
        }
        text = (
            "atlas config\n\n"
            f"config: {paths.config_home}\n"
            f"state:  {paths.state_home}\n"
            f"data:   {paths.data_home}\n"
            f"code:   {paths.code_root if paths.code_root is not None else '(unset)'}\n"
            f"profile: {profile_state.name if profile_state is not None else '(custom)'}\n"
        )
        return CommandOutcome("config.show", data, text)

    if args.action == "set":
        settings = load_settings(paths)
        if args.key == "data_home":
            updated = AtlasSettings(
                data_home=str(Path(args.value).expanduser().resolve()),
                code_root=settings.code_root,
                project_roots=settings.project_roots,
                self_owners=settings.self_owners,
                auto_sync_relationships=settings.auto_sync_relationships,
                review_window_days=settings.review_window_days,
            )
        elif args.key == "code_root":
            code_root = args.value.strip()
            updated = AtlasSettings(
                data_home=settings.data_home,
                code_root=str(Path(code_root).expanduser().resolve()) if code_root else None,
                project_roots=settings.project_roots,
                self_owners=settings.self_owners,
                auto_sync_relationships=settings.auto_sync_relationships,
                review_window_days=settings.review_window_days,
            )
        elif args.key == "review_window_days":
            updated = AtlasSettings(
                data_home=settings.data_home,
                code_root=settings.code_root,
                project_roots=settings.project_roots,
                self_owners=settings.self_owners,
                auto_sync_relationships=settings.auto_sync_relationships,
                review_window_days=int(args.value),
            )
        else:
            normalized = args.value.strip().lower()
            if normalized not in {"true", "false", "1", "0", "yes", "no"}:
                raise SystemExit("auto_sync_relationships expects true/false.")
            updated = AtlasSettings(
                data_home=settings.data_home,
                code_root=settings.code_root,
                project_roots=settings.project_roots,
                self_owners=settings.self_owners,
                auto_sync_relationships=normalized in {"true", "1", "yes"},
                review_window_days=settings.review_window_days,
            )
        with mutation_lock(paths, "state"):
            save_settings(paths, updated)
            profile_state = mark_profile_customized(paths, True)
        refreshed = get_paths()
        data = {
            "paths": _paths_dict(refreshed),
            "settings": _settings_dict(refreshed),
            "profile": _profile_state_dict(profile_state),
        }
        return CommandOutcome("config.set", data, f"{args.key}={args.value}")

    if args.action == "roots":
        if args.roots_action is None:
            raise SystemExit("Usage: atlas config roots <add|remove> <path>")
        with mutation_lock(paths, "state"):
            if args.roots_action == "add":
                updated = add_root(paths, args.path)
            else:
                updated = remove_root(paths, args.path)
            profile_state = mark_profile_customized(paths, True)
        data = {
            "settings": asdict(updated),
            "profile": _profile_state_dict(profile_state),
        }
        return CommandOutcome("config.roots", data, "\n".join(updated.project_roots))

    if args.action == "profile":
        if args.profile_action == "list":
            profiles = [profile_dict(profile) for profile in list_profiles()]
            return CommandOutcome("config.profile.list", {"profiles": profiles}, None)
        if args.profile_action == "show":
            profile = get_profile(args.name)
            payload = profile_dict(profile)
            return CommandOutcome(
                "config.profile.show",
                {"profile": payload},
                json.dumps(payload, indent=2, sort_keys=True),
            )
        if args.profile_action == "current":
            state = load_profile_state(paths)
            return CommandOutcome(
                "config.profile.current",
                {"profile": _profile_state_dict(state)},
                state.name if state is not None else "custom",
            )
        if args.profile_action == "use":
            profile = get_profile(args.name)
            with mutation_lock(paths, "state"):
                save_settings(paths, profile.settings)
                state = AtlasProfileState(name=profile.name, source="packaged", customized=False)
                save_profile_state(paths, state)
                ranked_result = ensure_ranked_contexts_config(paths, profile.name)
            refreshed = get_paths()
            return CommandOutcome(
                "config.profile.use",
                {
                    "profile": _profile_state_dict(load_profile_state(refreshed)),
                    "settings": _settings_dict(refreshed),
                    "ranked_contexts": _ranked_contexts_dict(ranked_result),
                },
                f"Using profile: {profile.name}",
            )
        raise SystemExit("Usage: atlas config profile <list|show|current|use>")

    if args.action == "shell":
        active_profile = load_profile_state(paths)
        profile_name = (
            args.profile
            if getattr(args, "profile", None)
            else (active_profile.name if active_profile is not None else None)
        )
        if args.shell_action == "show":
            shell_text = render_bash_snippet(profile_name=profile_name)
            return CommandOutcome(
                "config.shell.show",
                {"profile": profile_name, "shell": "bash", "snippet": shell_text},
                shell_text,
            )
        if args.shell_action == "install":
            target = (
                Path(args.target).expanduser().resolve()
                if args.target
                else (Path.home() / ".bashrc").resolve()
            )
            with mutation_lock(paths, "state"):
                snippet_path = install_bash_snippet(paths, target, profile_name=profile_name)
            shell_data: dict[str, Any] = {
                "profile": profile_name,
                "shell": "bash",
                "target": str(target),
                "snippet_path": str(snippet_path),
            }
            return CommandOutcome("config.shell.install", shell_data, str(snippet_path))
        raise SystemExit("Usage: atlas config shell <show|install>")

    if args.action == "ranked":
        active_profile = load_profile_state(paths)
        profile_name = (
            args.profile
            if getattr(args, "profile", None)
            else (active_profile.name if active_profile is not None else DEFAULT_INSTALL_PROFILE)
        )
        if args.ranked_action == "path":
            ranked_path_data: dict[str, Any] = {"path": str(paths.ranked_contexts_path)}
            return CommandOutcome(
                "config.ranked.path",
                ranked_path_data,
                str(paths.ranked_contexts_path),
            )
        if args.ranked_action == "show":
            payload = load_ranked_contexts_payload(paths)
            return CommandOutcome(
                "config.ranked.show",
                {"path": str(paths.ranked_contexts_path), "config": payload},
                read_ranked_contexts_text(paths),
            )
        if args.ranked_action == "install":
            get_profile(profile_name)
            with mutation_lock(paths, "state"):
                result = ensure_ranked_contexts_config(
                    paths,
                    profile_name,
                    force=args.force,
                )
            data = {"ranked_contexts": _ranked_contexts_dict(result)}
            return CommandOutcome(
                "config.ranked.install",
                data,
                f"{result.status}: {result.path}",
            )
        raise SystemExit("Usage: atlas config ranked <path|show|install>")

    raise SystemExit("Usage: atlas config <show|set|roots|profile|shell|ranked>")


def _install_main(argv: list[str], _: bool) -> CommandOutcome:
    parser = argparse.ArgumentParser(
        prog="atlas install",
        description="Install and configure atlas.",
    )
    parser.add_argument("--profile", default=DEFAULT_INSTALL_PROFILE)
    parser.add_argument("--shell-setup", action="store_true")
    parser.add_argument("--shell-target")
    parser.add_argument("--print-shell", action="store_true")
    args = parser.parse_args(argv)

    profile = get_profile(args.profile)
    paths = get_paths()
    with mutation_lock(paths, "state"):
        ensure_state(paths)
        save_settings(paths, profile.settings)
        profile_state = AtlasProfileState(name=profile.name, source="packaged", customized=False)
        save_profile_state(paths, profile_state)
        ranked_result = ensure_ranked_contexts_config(paths, profile.name)
        snippet_path: Path | None = None
        if args.shell_setup:
            target = (
                Path(args.shell_target).expanduser().resolve()
                if args.shell_target
                else (Path.home() / ".bashrc").resolve()
            )
            snippet_path = install_bash_snippet(paths, target, profile_name=profile.name)
    refreshed = get_paths()
    shell_text = render_bash_snippet(profile_name=profile.name) if args.print_shell else None
    data = {
        "profile": _profile_state_dict(load_profile_state(refreshed)),
        "settings": _settings_dict(refreshed),
        "paths": _paths_dict(refreshed),
        "sample_profile_default": DEFAULT_INSTALL_PROFILE,
        "ranked_contexts": _ranked_contexts_dict(ranked_result),
        "shell_target": args.shell_target or str((Path.home() / ".bashrc").resolve())
        if args.shell_setup
        else None,
        "snippet_path": (
            str(snippet_path) if args.shell_setup and snippet_path is not None else None
        ),
        "shell_snippet": shell_text,
    }
    text_parts = [
        f"Installed atlas with profile: {profile.name}",
        (
            "Commands such as atlas and docday "
            "should work directly on PATH after uv tool install."
        ),
        (
            "This install currently uses the nshkrdotcom sample profile; "
            "you can adapt it with atlas config ..."
            if profile.name == "nshkrdotcom"
            else "You can adapt it with atlas config ..."
        ),
        f"Ranked contexts: {ranked_result.status} at {ranked_result.path}",
    ]
    if args.print_shell and shell_text:
        text_parts.extend(["", shell_text.rstrip()])
    return CommandOutcome("install", data, "\n".join(text_parts))


def _registry_main(argv: list[str], _: bool) -> CommandOutcome:
    parser = argparse.ArgumentParser(
        prog="atlas registry", description="Manage the project registry."
    )
    subparsers = parser.add_subparsers(dest="action")
    scan_parser = subparsers.add_parser("scan")
    scan_parser.add_argument("--changed-only", action="store_true")
    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--owner", choices=("self", "external", "unknown"))
    list_parser.add_argument("--language")
    list_parser.add_argument("--relation", choices=("primary", "fork", "external", "unknown"))
    resolve_parser = subparsers.add_parser("resolve")
    resolve_parser.add_argument("reference")
    show_parser = subparsers.add_parser("show")
    show_parser.add_argument("reference")
    add_root_parser = subparsers.add_parser("root-add")
    add_root_parser.add_argument("path")
    remove_root_parser = subparsers.add_parser("root-remove")
    remove_root_parser.add_argument("path")
    alias_add_parser = subparsers.add_parser("alias-add")
    alias_add_parser.add_argument("reference")
    alias_add_parser.add_argument("alias")
    alias_remove_parser = subparsers.add_parser("alias-remove")
    alias_remove_parser.add_argument("reference")
    alias_remove_parser.add_argument("alias")
    args = parser.parse_args(argv)

    if args.action is None:
        text = render_topic_help("registry")
        return CommandOutcome("registry", {"topic": "registry", "text": text}, text)

    paths = get_paths()
    settings = ensure_state(paths)

    if args.action == "scan":
        with mutation_lock(paths, "registry"):
            result = scan_registry_with_stats(
                paths, settings=settings, changed_only=args.changed_only
            )
        data = {
            "project_count": len(result.projects),
            "projects": [_project_dict(record) for record in result.projects],
            "scanned_roots": result.scanned_roots,
            "reused_roots": result.reused_roots,
            "changed_only": args.changed_only,
        }
        if args.changed_only:
            text = (
                f"Scanned {len(result.projects)} projects "
                "("
                f"scanned_roots={len(result.scanned_roots)} "
                f"reused_roots={len(result.reused_roots)}"
                ")."
            )
        else:
            text = f"Scanned {len(result.projects)} projects."
        return CommandOutcome("registry.scan", data, text)

    if args.action == "list":
        registry = load_registry(paths) or scan_registry(paths, settings)
        if getattr(args, "owner", None):
            registry = [record for record in registry if record.owner_scope == args.owner]
        if getattr(args, "language", None):
            registry = [
                record
                for record in registry
                if args.language.lower() in {language.lower() for language in record.languages}
            ]
        if getattr(args, "relation", None):
            registry = [record for record in registry if record.relation == args.relation]
        data = {
            "project_count": len(registry),
            "projects": [_project_dict(record) for record in registry],
        }
        text = "\n".join(
            f"{record.name}\t{record.path}\t{','.join(record.aliases)}" for record in registry
        )
        return CommandOutcome("registry.list", data, text)

    if args.action == "resolve":
        record = resolve_project_ref(paths, args.reference)
        return CommandOutcome(
            "registry.resolve",
            {"reference": args.reference, "project": _project_dict(record)},
            record.path,
        )

    if args.action == "show":
        record = resolve_project_ref(paths, args.reference)
        text = "\n".join(
            [
                f"name: {record.name}",
                f"path: {record.path}",
                f"root: {record.root}",
                f"aliases: {', '.join(record.aliases)}",
                f"markers: {', '.join(record.markers)}",
                f"languages: {', '.join(record.languages)}",
                f"primary_language: {record.primary_language or 'unknown'}",
                f"owner_scope: {record.owner_scope}",
                f"relation: {record.relation}",
            ]
        )
        return CommandOutcome(
            "registry.show",
            {"reference": args.reference, "project": _project_dict(record)},
            text,
        )

    if args.action == "root-add":
        with mutation_lock(paths, "registry"):
            updated = add_root(paths, args.path)
        return CommandOutcome(
            "registry.root-add",
            {"settings": asdict(updated)},
            "\n".join(updated.project_roots),
        )

    if args.action == "root-remove":
        with mutation_lock(paths, "registry"):
            updated = remove_root(paths, args.path)
        return CommandOutcome(
            "registry.root-remove",
            {"settings": asdict(updated)},
            "\n".join(updated.project_roots),
        )

    if args.action == "alias-add":
        with mutation_lock(paths, "registry"):
            record = add_alias(paths, args.reference, args.alias)
        return CommandOutcome(
            "registry.alias-add",
            {"project": _project_dict(record)},
            f"{record.name}: {', '.join(record.aliases)}",
        )

    with mutation_lock(paths, "registry"):
        record = remove_alias(paths, args.reference, args.alias)
    return CommandOutcome(
        "registry.alias-remove",
        {"project": _project_dict(record)},
        f"{record.name}: {', '.join(record.aliases)}",
    )


def _resolve_main(argv: list[str], _: bool) -> CommandOutcome:
    parser = argparse.ArgumentParser(
        prog="atlas resolve", description="Resolve a project reference."
    )
    parser.add_argument("reference")
    args = parser.parse_args(argv)
    paths = get_paths()
    ensure_state(paths)
    record = resolve_project_ref(paths, args.reference)
    return CommandOutcome(
        "resolve",
        {"reference": args.reference, "project": _project_dict(record)},
        record.path,
    )


def _today_main(argv: list[str], json_mode: bool) -> CommandOutcome:
    parser = argparse.ArgumentParser(prog="atlas today", description="Create or open today's note.")
    parser.add_argument("--print", action="store_true", dest="print_only")
    args = parser.parse_args(argv)
    paths = get_paths()
    stamp = now_local().strftime("%Y%m%d")
    note_path = paths.docs_root / stamp / "index.md"
    created = False
    with mutation_lock(paths, "daily"):
        ensure_state(paths)
        note_path.parent.mkdir(parents=True, exist_ok=True)
        if not note_path.exists():
            note_path.write_text(daily_note_template(stamp), encoding="utf-8")
            created = True
    data = {"day": stamp, "path": str(note_path), "created": created}
    if json_mode or args.print_only:
        return CommandOutcome("today", data, str(note_path))
    return_code = open_in_editor(note_path)
    if return_code != 0:
        raise AtlasCliError(ExitCode.EXTERNAL, "editor_failed", "Failed to open editor.")
    return CommandOutcome("today", data, None)


def _capture_main(argv: list[str], _: bool) -> CommandOutcome:
    parser = argparse.ArgumentParser(
        prog="atlas capture", description="Capture a structured inbox entry."
    )
    parser.add_argument("--project", help="Project registry ref or explicit path-like name.")
    parser.add_argument(
        "--tag", action="append", default=[], help="Tag to associate with the entry."
    )
    parser.add_argument(
        "--kind",
        default="note",
        choices=("note", "decision", "project", "topic", "person"),
        help="Entry kind used for review and auto-promotion.",
    )
    parser.add_argument("--stdin", action="store_true", help="Read capture text from stdin.")
    parser.add_argument("text", nargs="*")
    args = parser.parse_args(argv)
    paths = get_paths()
    ensure_state(paths)
    text = _read_text_arg(
        args.text,
        args.stdin or not args.text,
        empty_message="atlas capture needs text via args or --stdin.",
    )
    project = resolve_or_placeholder(paths, args.project) if args.project else None
    with mutation_lock(paths, "inbox"):
        entry = create_entry(paths, text, project=project, tags=args.tag, kind=args.kind)
    return CommandOutcome("capture", {"entry": _entry_dict(entry)}, entry.source_path)


def _review_main(argv: list[str], _: bool) -> CommandOutcome:
    parser = argparse.ArgumentParser(
        prog="atlas review", description="Review inbox and daily state."
    )
    subparsers = parser.add_subparsers(dest="action")
    inbox_parser = subparsers.add_parser("inbox")
    inbox_parser.add_argument("--date")
    daily_parser = subparsers.add_parser("daily")
    daily_parser.add_argument("--date")
    args = parser.parse_args(argv)
    if args.action is None:
        text = render_topic_help("review")
        return CommandOutcome("review", {"topic": "review", "text": text}, text)

    paths = get_paths()
    ensure_state(paths)
    if args.action == "inbox":
        entries = [entry for entry in iter_entries(paths, day=args.date) if entry.status == "open"]
        data = {
            "day": args.date,
            "open_count": len(entries),
            "entries": [_entry_dict(entry) for entry in entries],
        }
        return CommandOutcome("review.inbox", data, review_inbox(paths, day=args.date))

    stamp = args.date or now_local().strftime("%Y%m%d")
    entries = [entry for entry in iter_entries(paths, day=stamp) if entry.status == "open"]
    auto_entries = [entry for entry in entries if infer_promotion_kind(entry) is not None]
    data = {
        "day": stamp,
        "today_note": str(paths.docs_root / stamp / "index.md"),
        "open_count": len(entries),
        "auto_promotable_count": len(auto_entries),
        "entries": [_entry_dict(entry) for entry in entries],
    }
    return CommandOutcome("review.daily", data, review_daily(paths, day=args.date))


def _promote_main(argv: list[str], _: bool) -> CommandOutcome:
    parser = argparse.ArgumentParser(prog="atlas promote", description="Promote inbox entries.")
    subparsers = parser.add_subparsers(dest="action")
    entry_parser = subparsers.add_parser("entry")
    entry_parser.add_argument("entry_id")
    entry_parser.add_argument("--kind", choices=("note", "decision", "project", "topic", "person"))
    entry_parser.add_argument("--title")
    entry_parser.add_argument("--project")
    auto_parser = subparsers.add_parser("auto")
    auto_parser.add_argument("--date")
    args = parser.parse_args(argv)

    if args.action is None:
        text = render_topic_help("review")
        return CommandOutcome("promote", {"topic": "review", "text": text}, text)

    paths = get_paths()
    ensure_state(paths)
    if args.action == "auto":
        with mutation_lock(paths, "notes"):
            targets = promote_auto(paths, day=args.date)
        data = {"targets": [str(path) for path in targets], "count": len(targets)}
        return CommandOutcome("promote.auto", data, "\n".join(str(path) for path in targets))

    project = resolve_or_placeholder(paths, args.project) if args.project else None
    with mutation_lock(paths, "notes"):
        target = promote_entry(
            paths,
            args.entry_id,
            kind=args.kind,
            title=args.title,
            project=project,
        )
    data = {"entry_id": args.entry_id, "target": str(target), "sync": _relationships_meta(paths)}
    return CommandOutcome("promote.entry", data, str(target))


def _find_note_candidates(paths: AtlasPaths, query: str) -> list[dict[str, Any]]:
    matches = search_text([paths.docs_root, paths.mem_root], query)
    return [
        {"path": str(match.path), "line_number": match.line_number, "line": match.line}
        for match in matches
    ]


def _note_main(argv: list[str], json_mode: bool) -> CommandOutcome:
    parser = argparse.ArgumentParser(
        prog="atlas note", description="Create, find, open, and sync notes."
    )
    subparsers = parser.add_subparsers(dest="action")
    new_parser = subparsers.add_parser("new")
    new_parser.add_argument("title")
    new_parser.add_argument(
        "--kind",
        default="note",
        choices=("note", "decision", "project", "topic", "person"),
    )
    new_parser.add_argument("--project")
    new_parser.add_argument("--tag", action="append", default=[])
    new_parser.add_argument("--body", default="")
    new_parser.add_argument("--body-stdin", action="store_true")
    find_parser = subparsers.add_parser("find")
    find_parser.add_argument("query", nargs="+")
    open_parser = subparsers.add_parser("open")
    open_parser.add_argument("--print", action="store_true", dest="print_only")
    open_parser.add_argument("query", nargs="*")
    sync_parser = subparsers.add_parser("sync")
    sync_parser.add_argument(
        "paths", nargs="*", help="Optional touched note paths for incremental sync."
    )
    args = parser.parse_args(argv)

    if args.action is None:
        text = render_topic_help("note")
        return CommandOutcome("note", {"topic": "note", "text": text}, text)

    paths = get_paths()
    ensure_state(paths)

    if args.action == "find":
        query = " ".join(args.query)
        matches = _find_note_candidates(paths, query)
        text = "\n".join(f"{item['path']}:{item['line_number']}:{item['line']}" for item in matches)
        return CommandOutcome("note.find", {"query": query, "matches": matches}, text)

    if args.action == "open":
        candidates = collect_note_files([paths.docs_root, paths.mem_root])
        if args.query:
            needle = " ".join(args.query).lower()
            candidates = [path for path in candidates if needle in path.as_posix().lower()]
        if not candidates:
            raise SystemExit("No matching notes found.")
        selected = candidates[0]
        data = {"path": str(selected), "query": args.query}
        if json_mode or args.print_only:
            return CommandOutcome("note.open", data, str(selected))
        if command_exists("fzf"):
            result = subprocess.run(
                ["fzf"],
                input="\n".join(str(path) for path in candidates),
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                selected = Path(result.stdout.strip())
                data = {"path": str(selected), "query": args.query}
        return_code = open_in_editor(selected)
        if return_code != 0:
            raise AtlasCliError(ExitCode.EXTERNAL, "editor_failed", "Failed to open editor.")
        return CommandOutcome("note.open", data, None)

    if args.action == "sync":
        touched = [Path(item).expanduser().resolve() for item in args.paths] if args.paths else None
        with mutation_lock(paths, "notes"):
            sync_result = sync_note_graph(paths, touched=touched)
        data = {
            "sync": _sync_dict(sync_result),
            "touched": [str(path) for path in touched or []],
        }
        text = (
            f"mode={sync_result.mode} parsed_notes={sync_result.parsed_notes} "
            f"changed_notes={sync_result.changed_notes} note_count={sync_result.note_count}"
        )
        return CommandOutcome("note.sync", data, text)

    body = args.body.strip()
    if args.body_stdin:
        stdin_body = sys.stdin.read().strip()
        body = "\n\n".join(item for item in [body, stdin_body] if item).strip()
    project = resolve_or_placeholder(paths, args.project) if args.project else None
    with mutation_lock(paths, "notes"):
        target = create_note(
            paths,
            title=args.title,
            kind=args.kind,
            project=project,
            tags=args.tag,
            body=body,
        )
    data = {"path": str(target), "sync": _relationships_meta(paths)}
    return CommandOutcome("note.new", data, str(target))


def _context_main(argv: list[str], json_mode: bool) -> CommandOutcome:
    parser = argparse.ArgumentParser(
        prog="atlas context", description="Build repo and note bundles."
    )
    subparsers = parser.add_subparsers(dest="action")
    notes_parser = subparsers.add_parser("notes")
    notes_parser.add_argument("--pwd-only", action="store_true")
    notes_parser.add_argument("-o", "--output")
    notes_parser.add_argument("path")
    repo_parser = subparsers.add_parser("repo")
    repo_parser.add_argument("reference")
    repo_parser.add_argument("group", nargs="?")
    repo_parser.add_argument("-o", "--output")
    stack_parser = subparsers.add_parser("stack")
    stack_parser.add_argument("--group")
    stack_parser.add_argument("--remember", action="store_true")
    stack_parser.add_argument("-o", "--output")
    stack_parser.add_argument("items", nargs="*")
    ranked_parser = subparsers.add_parser("ranked")
    ranked_parser.add_argument("target")
    ranked_parser.add_argument("config", nargs="?")
    ranked_parser.add_argument("-o", "--output")
    ranked_parser.add_argument("--wait-fresh-ms", type=int, default=0)
    ranked_parser.add_argument("--ttl-ms", type=int, default=DEFAULT_TTL_MS)
    ranked_parser.add_argument("--allow-stale", dest="allow_stale", action="store_true")
    ranked_parser.add_argument("--no-allow-stale", dest="allow_stale", action="store_false")
    ranked_parser.set_defaults(allow_stale=True)
    args = parser.parse_args(argv)

    if args.action is None:
        text = render_topic_help("context")
        return CommandOutcome("context", {"topic": "context", "text": text}, text)

    paths = get_paths()
    ensure_state(paths)

    if args.action == "notes":
        manifest = markdown_manifest(paths, Path(args.path), pwd_only=args.pwd_only)
        notes_data: dict[str, Any] = {"manifest": manifest_dict(manifest)}
        if args.output is not None:
            notes_data["output_path"] = _copy_bundle(manifest.bundle_path, args.output)
            return CommandOutcome("context.notes", notes_data, str(notes_data["output_path"]))
        if json_mode:
            return CommandOutcome("context.notes", notes_data, None)
        return CommandOutcome(
            "context.notes",
            notes_data,
            Path(manifest.bundle_path).read_text(encoding="utf-8"),
        )

    if args.action == "repo":
        reference = args.reference
        target = Path(reference).expanduser()
        if not target.exists():
            target = Path(resolve_project_ref(paths, reference).path)
        manifest = mix_manifest(paths, target, args.group)
        repo_data: dict[str, Any] = {
            "reference": args.reference,
            "group": args.group,
            "manifest": manifest_dict(manifest),
        }
        if args.output is not None:
            repo_data["output_path"] = _copy_bundle(manifest.bundle_path, args.output)
            return CommandOutcome("context.repo", repo_data, str(repo_data["output_path"]))
        if json_mode:
            return CommandOutcome("context.repo", repo_data, None)
        return CommandOutcome(
            "context.repo",
            repo_data,
            Path(manifest.bundle_path).read_text(encoding="utf-8"),
        )

    if args.action == "ranked":
        ranked_mode = "render"
        config_name = args.target
        if args.target in {"prepare", "status"}:
            ranked_mode = args.target
            if args.config is None:
                raise SystemExit(_ranked_context_usage())
            config_name = args.config
            if args.output is not None:
                raise SystemExit(
                    "`-o/--output` is only supported for atlas context ranked <config-name>."
                )
        elif args.config is not None:
            raise SystemExit(_ranked_context_usage())

        freshness = ranked_index_freshness_payload(
            paths,
            config_name,
            ttl_ms=args.ttl_ms,
            wait_fresh_ms=args.wait_fresh_ms,
            allow_stale=args.allow_stale,
        )

        if ranked_mode == "prepare":
            prepared = prepare_ranked_manifest(
                paths,
                config_name,
                progress=_write_progress,
            )
            prepared_data = {
                "config": config_name,
                "index_freshness": freshness,
                "prepared_manifest": prepared_manifest_dict(prepared),
            }
            text = (
                f"prepared {config_name}: {prepared.manifest_path}\n"
                f"repos={prepared.repo_count} projects={prepared.project_count} "
                f"files={len(prepared.files)}"
            )
            return CommandOutcome(
                "context.ranked.prepare",
                prepared_data,
                None if json_mode else text,
            )

        if ranked_mode == "status":
            prepared = load_prepared_ranked_manifest(paths, config_name)
            prepared_data = {
                "config": config_name,
                "index_freshness": freshness,
                "prepared_manifest": prepared_manifest_dict(prepared),
            }
            text = (
                f"prepared {config_name}: {prepared.manifest_path}\n"
                f"prepared_at={prepared.prepared_at} repos={prepared.repo_count} "
                f"projects={prepared.project_count} files={len(prepared.files)}"
            )
            return CommandOutcome(
                "context.ranked.status",
                prepared_data,
                None if json_mode else text,
            )

        manifest = ranked_manifest(paths, config_name)
        prepared = load_prepared_ranked_manifest(paths, config_name)
        ranked_data: dict[str, Any] = {
            "config": config_name,
            "index_freshness": freshness,
            "prepared_manifest": prepared_manifest_dict(prepared),
            "manifest": manifest_dict(manifest),
        }
        if args.output is not None:
            ranked_data["output_path"] = _copy_bundle(manifest.bundle_path, args.output)
            return CommandOutcome("context.ranked", ranked_data, str(ranked_data["output_path"]))
        if json_mode:
            return CommandOutcome("context.ranked", ranked_data, None)
        return CommandOutcome(
            "context.ranked",
            ranked_data,
            Path(manifest.bundle_path).read_text(encoding="utf-8"),
        )

    remembered_preset_id: int | None = None
    if args.remember:
        from .multi_ctx import Preset, load_presets, resolve_input_path, save_presets

        presets = load_presets()
        remembered_preset_id = max((preset.id for preset in presets), default=0) + 1
        presets.append(
            Preset(
                id=remembered_preset_id,
                paths=[resolve_input_path(item) for item in args.items],
            )
        )
        save_presets(presets)
    manifest = stack_manifest(paths, args.items, args.group)
    stack_data: dict[str, Any] = {
        "items": args.items,
        "group": args.group,
        "manifest": manifest_dict(manifest),
        "remembered_preset_id": remembered_preset_id,
    }
    if args.output is not None:
        stack_data["output_path"] = _copy_bundle(manifest.bundle_path, args.output)
        return CommandOutcome("context.stack", stack_data, str(stack_data["output_path"]))
    if json_mode:
        return CommandOutcome("context.stack", stack_data, None)
    return CommandOutcome(
        "context.stack",
        stack_data,
        Path(manifest.bundle_path).read_text(encoding="utf-8"),
    )


def _snapshot_main(argv: list[str], _: bool) -> CommandOutcome:
    parser = argparse.ArgumentParser(
        prog="atlas snapshot", description="Save command output as a snapshot."
    )
    parser.add_argument("name")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise SystemExit("Usage: atlas snapshot <name> -- <command ...>")

    paths = get_paths()
    ensure_state(paths)
    now = now_local()
    with mutation_lock(paths, "snapshots"):
        day_dir = paths.snapshots_root / now.strftime("%Y%m%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        stem = args.name.lower().replace(" ", "-")
        snapshot_path = day_dir / f"{stem}.ctx"
        meta_path = day_dir / f"{stem}.json"
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        snapshot_path.write_text(result.stdout, encoding="utf-8")
        meta_path.write_text(
            json.dumps(
                {
                    "name": args.name,
                    "command": command,
                    "cwd": os.getcwd(),
                    "timestamp": now.isoformat(),
                    "returncode": result.returncode,
                    "stderr": result.stderr,
                    "snapshot": str(snapshot_path),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    data = {
        "path": str(snapshot_path),
        "meta_path": str(meta_path),
        "returncode": result.returncode,
    }
    return CommandOutcome("snapshot", data, str(snapshot_path))


def _related_main(argv: list[str], json_mode: bool) -> CommandOutcome:
    parser = argparse.ArgumentParser(prog="atlas related", description="Show related notes.")
    parser.add_argument("path")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args(argv)
    target = Path(args.path).expanduser().resolve()
    if not target.is_file():
        raise SystemExit(f"Path is not a file: {target}")
    paths = get_paths()
    ensure_state(paths)
    del json_mode
    _, _, related_map, _, _ = build_graph(paths)
    items = related_map.get(target, [])
    data = {"path": str(target), "items": [str(path) for path in items[: args.limit]]}
    text = "\n".join(
        f"{index}\t{candidate}" for index, candidate in enumerate(items[: args.limit], start=1)
    )
    return CommandOutcome("related", data, text)


def _intelligence_text(data: dict[str, Any]) -> str:
    result = data.get("result")
    if isinstance(result, str):
        return result.rstrip()
    if result is None:
        return ""
    return json.dumps(result, indent=2, sort_keys=True)


def _dexter_text(data: dict[str, Any]) -> str:
    stdout = str(data.get("stdout") or "").rstrip()
    stderr = str(data.get("stderr") or "").rstrip()
    return stdout or stderr


def _project_option_parser(prog: str, description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description=description)
    parser.add_argument(
        "--project",
        default=".",
        help="Project ref/path. Defaults to the current repo.",
    )
    return parser


def _def_main(argv: list[str], _: bool) -> CommandOutcome:
    parser = _project_option_parser("atlas def", "Find a module/function definition.")
    parser.add_argument("module")
    parser.add_argument("function", nargs="?")
    parser.add_argument("arity", nargs="?")
    args = parser.parse_args(argv)
    positional = [args.module]
    if args.function is not None:
        positional.append(args.function)
    if args.arity is not None:
        positional.append(args.arity)
    paths = get_paths()
    ensure_state(paths)
    if args.function is None:
        data = run_dexter_cli(paths, "lookup", positional, reference=args.project)
        data["result"] = data.get("stdout", "")
        return CommandOutcome("def", data, _dexter_text(data))
    data = run_dexterity_query(paths, "definition", positional, reference=args.project)
    return CommandOutcome("def", data, _intelligence_text(data))


def _refs_main(argv: list[str], _: bool) -> CommandOutcome:
    parser = _project_option_parser("atlas refs", "Find module/function references.")
    parser.add_argument("module")
    parser.add_argument("function", nargs="?")
    parser.add_argument("arity", nargs="?")
    args = parser.parse_args(argv)
    positional = [args.module]
    if args.function is not None:
        positional.append(args.function)
    if args.arity is not None:
        positional.append(args.arity)
    paths = get_paths()
    ensure_state(paths)
    data = run_dexterity_query(paths, "references", positional, reference=args.project)
    return CommandOutcome("refs", data, _intelligence_text(data))


def _symbols_main(argv: list[str], _: bool) -> CommandOutcome:
    parser = _project_option_parser("atlas symbols", "Search indexed symbols.")
    parser.add_argument("--limit", type=int)
    parser.add_argument("query", nargs="+")
    args = parser.parse_args(argv)
    option_args = ["--limit", str(args.limit)] if args.limit is not None else []
    paths = get_paths()
    ensure_state(paths)
    data = run_dexterity_query(
        paths,
        "symbols",
        [" ".join(args.query)],
        reference=args.project,
        option_args=option_args,
    )
    return CommandOutcome("symbols", data, _intelligence_text(data))


def _files_main(argv: list[str], _: bool) -> CommandOutcome:
    parser = _project_option_parser("atlas files", "Search indexed files.")
    parser.add_argument("--limit", type=int)
    parser.add_argument("pattern")
    args = parser.parse_args(argv)
    option_args = ["--limit", str(args.limit)] if args.limit is not None else []
    paths = get_paths()
    ensure_state(paths)
    data = run_dexterity_query(
        paths,
        "files",
        [args.pattern],
        reference=args.project,
        option_args=option_args,
    )
    return CommandOutcome("files", data, _intelligence_text(data))


def _ranked_common_options(args: argparse.Namespace) -> list[str]:
    option_args: list[str] = []
    for value in getattr(args, "active_files", []) or []:
        option_args.extend(["--active-file", value])
    for value in getattr(args, "mentioned_files", []) or []:
        option_args.extend(["--mentioned-file", value])
    for value in getattr(args, "edited_files", []) or []:
        option_args.extend(["--edited-file", value])
    for value in getattr(args, "include_prefixes", []) or []:
        option_args.extend(["--include-prefix", value])
    for value in getattr(args, "exclude_prefixes", []) or []:
        option_args.extend(["--exclude-prefix", value])
    if getattr(args, "overscan_limit", None) is not None:
        option_args.extend(["--overscan-limit", str(args.overscan_limit)])
    if getattr(args, "limit", None) is not None:
        option_args.extend(["--limit", str(args.limit)])
    if getattr(args, "token_budget", None) is not None:
        option_args.extend(["--token-budget", str(args.token_budget)])
    return option_args


def _ranked_files_main(argv: list[str], _: bool) -> CommandOutcome:
    parser = _project_option_parser("atlas ranked-files", "Rank files for agent context.")
    parser.add_argument("--active", "--active-file", action="append", dest="active_files")
    parser.add_argument("--mentioned", "--mentioned-file", action="append", dest="mentioned_files")
    parser.add_argument("--edited", "--edited-file", action="append", dest="edited_files")
    parser.add_argument("--include-prefix", action="append", dest="include_prefixes")
    parser.add_argument("--exclude-prefix", action="append", dest="exclude_prefixes")
    parser.add_argument("--include-external", action="store_true")
    parser.add_argument("--overscan-limit", type=int)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    paths = get_paths()
    ensure_state(paths)
    data = run_dexterity_query(
        paths,
        "ranked_files",
        [],
        reference=args.project,
        option_args=_ranked_common_options(args),
        filter_repo_source=not args.include_external,
    )
    return CommandOutcome("ranked-files", data, _intelligence_text(data))


def _ranked_symbols_main(argv: list[str], _: bool) -> CommandOutcome:
    parser = _project_option_parser("atlas ranked-symbols", "Rank symbols for agent context.")
    parser.add_argument("--active", "--active-file", action="append", dest="active_files")
    parser.add_argument("--mentioned", "--mentioned-file", action="append", dest="mentioned_files")
    parser.add_argument("--include-external", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    paths = get_paths()
    ensure_state(paths)
    data = run_dexterity_query(
        paths,
        "ranked_symbols",
        [],
        reference=args.project,
        option_args=_ranked_common_options(args),
        filter_repo_source=not args.include_external,
    )
    return CommandOutcome("ranked-symbols", data, _intelligence_text(data))


def _impact_main(argv: list[str], _: bool) -> CommandOutcome:
    parser = _project_option_parser("atlas impact", "Build focused impact context.")
    parser.add_argument("--token-budget", type=int)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--include-external", action="store_true")
    parser.add_argument("file")
    args = parser.parse_args(argv)
    option_args = ["--changed-file", args.file]
    if args.token_budget is not None:
        option_args.extend(["--token-budget", str(args.token_budget)])
    if args.limit is not None:
        option_args.extend(["--limit", str(args.limit)])
    paths = get_paths()
    ensure_state(paths)
    data = run_dexterity_query(
        paths,
        "impact_context",
        [],
        reference=args.project,
        option_args=option_args,
        filter_repo_source=not args.include_external,
        filter_text=True,
    )
    return CommandOutcome("impact", data, _intelligence_text(data))


def _blast_main(argv: list[str], _: bool) -> CommandOutcome:
    parser = _project_option_parser("atlas blast", "Show file dependency blast radius.")
    parser.add_argument("--depth", type=int)
    parser.add_argument("file")
    args = parser.parse_args(argv)
    option_args = ["--depth", str(args.depth)] if args.depth is not None else []
    paths = get_paths()
    ensure_state(paths)
    data = run_dexterity_query(
        paths,
        "blast",
        [args.file],
        reference=args.project,
        option_args=option_args,
    )
    return CommandOutcome("blast", data, _intelligence_text(data))


def _cochanges_main(argv: list[str], _: bool) -> CommandOutcome:
    parser = _project_option_parser("atlas cochanges", "Show files that co-change together.")
    parser.add_argument("--limit", type=int)
    parser.add_argument("file")
    args = parser.parse_args(argv)
    option_args = ["--limit", str(args.limit)] if args.limit is not None else []
    paths = get_paths()
    ensure_state(paths)
    data = run_dexterity_query(
        paths,
        "cochanges",
        [args.file],
        reference=args.project,
        option_args=option_args,
    )
    return CommandOutcome("cochanges", data, _intelligence_text(data))


def _export_query_main(argv: list[str], command: str, action: str) -> CommandOutcome:
    parser = _project_option_parser(f"atlas {command}", f"Run Dexterity {action}.")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    option_args = ["--limit", str(args.limit)] if args.limit is not None else []
    paths = get_paths()
    ensure_state(paths)
    data = run_dexterity_query(paths, action, [], reference=args.project, option_args=option_args)
    return CommandOutcome(command, data, _intelligence_text(data))


def _exports_main(argv: list[str], _: bool) -> CommandOutcome:
    return _export_query_main(argv, "exports", "export_analysis")


def _unused_exports_main(argv: list[str], _: bool) -> CommandOutcome:
    return _export_query_main(argv, "unused-exports", "unused_exports")


def _test_only_exports_main(argv: list[str], _: bool) -> CommandOutcome:
    return _export_query_main(argv, "test-only-exports", "test_only_exports")


def _repo_map_main(argv: list[str], _: bool) -> CommandOutcome:
    parser = _project_option_parser("atlas repo-map", "Render a ranked repo map.")
    parser.add_argument("--active", "--active-file", action="append", dest="active_files")
    parser.add_argument("--mentioned", "--mentioned-file", action="append", dest="mentioned_files")
    parser.add_argument("--edited", "--edited-file", action="append", dest="edited_files")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--token-budget")
    args = parser.parse_args(argv)
    paths = get_paths()
    ensure_state(paths)
    data = run_dexterity_map(
        paths,
        reference=args.project,
        option_args=_ranked_common_options(args),
    )
    return CommandOutcome("repo-map", data, _intelligence_text(data))


def _dexter_main(argv: list[str], _: bool) -> CommandOutcome:
    parser = argparse.ArgumentParser(
        prog="atlas dexter",
        description="Run useful raw Dexter commands through Atlas shadow indexes.",
    )
    parser.add_argument("--project", default=".")
    subparsers = parser.add_subparsers(dest="action")
    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("--force", action="store_true")
    lookup_parser = subparsers.add_parser("lookup")
    lookup_parser.add_argument("module")
    lookup_parser.add_argument("function", nargs="?")
    lookup_parser.add_argument("--strict", action="store_true")
    lookup_parser.add_argument("--no-follow-delegates", action="store_true")
    refs_parser = subparsers.add_parser("refs")
    refs_parser.add_argument("module")
    refs_parser.add_argument("function", nargs="?")
    references_parser = subparsers.add_parser("references")
    references_parser.add_argument("module")
    references_parser.add_argument("function", nargs="?")
    reindex_parser = subparsers.add_parser("reindex")
    reindex_parser.add_argument("target", nargs="?")
    args = parser.parse_args(argv)
    if args.action is None:
        raise SystemExit("Usage: atlas dexter <init|lookup|refs|references|reindex> ...")

    positional: list[str] = []
    option_args: list[str] = []
    ensure_index = True
    action = str(args.action)
    if action == "init":
        ensure_index = False
        if args.force:
            option_args.append("--force")
    elif action == "lookup":
        positional.append(args.module)
        if args.function is not None:
            positional.append(args.function)
        if args.strict:
            option_args.append("--strict")
        if args.no_follow_delegates:
            option_args.append("--no-follow-delegates")
    elif action in {"refs", "references"}:
        positional.append(args.module)
        if args.function is not None:
            positional.append(args.function)
    elif action == "reindex" and args.target:
        positional.append(args.target)

    paths = get_paths()
    ensure_state(paths)
    data = run_dexter_cli(
        paths,
        action,
        positional,
        reference=args.project,
        option_args=option_args,
        ensure_index=ensure_index,
    )
    return CommandOutcome(f"dexter.{action}", data, _dexter_text(data))


def _index_status_text(data: dict[str, Any]) -> str:
    summary = data["summary"]
    daemon = data["daemon"]
    return (
        "atlas index status\n"
        f"running={daemon['running']} pid={daemon['pid']} watcher={daemon['watcher_type']}\n"
        f"projects={summary['projects_total']} fresh={summary['fresh']} "
        f"stale={summary['stale']} warming={summary['warming']} error={summary['error']} "
        f"queue={data['global_queue_depth']}"
    )


def _index_targets_data(targets: list[IndexWatchTarget]) -> list[dict[str, str]]:
    return [
        {
            "project_key": target.project_key,
            "project_ref": target.project_ref,
            "project_path": str(target.project_path),
        }
        for target in targets
    ]


def _index_runtime(paths: AtlasPaths) -> RankedRuntime:
    return load_ranked_default_runtime(paths)


def _index_main(argv: list[str], _: bool) -> CommandOutcome:
    parser = argparse.ArgumentParser(prog="atlas index", description="Manage atlas indexes.")
    subparsers = parser.add_subparsers(dest="action")
    here_parser = subparsers.add_parser("here")
    here_parser.add_argument("project", nargs="?", default=".")
    rebuild_parser = subparsers.add_parser("rebuild")
    rebuild_parser.add_argument("--changed-only", action="store_true")
    watch_parser = subparsers.add_parser("watch")
    watch_parser.add_argument("--daemon", action="store_true")
    watch_parser.add_argument("--once", action="store_true")
    watch_parser.add_argument("--poll", action="store_true")
    watch_parser.add_argument("--poll-interval-ms", type=int, default=DEFAULT_POLL_INTERVAL_MS)
    watch_parser.add_argument("--debounce-ms", type=int, default=DEFAULT_DEBOUNCE_MS)
    watch_parser.add_argument("--ttl-ms", type=int, default=DEFAULT_TTL_MS)
    watch_parser.add_argument("--project", action="append", dest="projects")

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--project", action="append", dest="projects")
    status_parser.add_argument("--all", action="store_true")
    status_parser.add_argument("--ttl-ms", type=int, default=DEFAULT_TTL_MS)

    refresh_parser = subparsers.add_parser("refresh")
    refresh_parser.add_argument("--project", action="append", dest="projects")
    refresh_parser.add_argument("--all", action="store_true")
    refresh_parser.add_argument("--ttl-ms", type=int, default=DEFAULT_TTL_MS)
    refresh_parser.add_argument("--wait-fresh-ms", type=int, default=0)

    stop_parser = subparsers.add_parser("stop")
    stop_parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    paths = get_paths()
    ensure_state(paths)

    if args.action == "here" or (args.action is None and current_directory_is_mix_project()):
        project = getattr(args, "project", ".")
        target, run = ensure_intelligence_index(paths, project)
        data = {
            "project": {
                "reference": target.reference,
                "project_ref": target.project_ref,
                "repo_root": str(target.project_root),
                "shadow_root": str(target.shadow_root),
                "dexterity_root": str(target.runtime.dexterity_root),
                "dexter_bin": target.runtime.dexter_bin,
            },
            "tool": {
                "kind": "dexterity",
                "command": run.command,
                "cwd": str(run.cwd),
                "returncode": run.returncode,
            },
            "stdout": run.stdout,
            "stderr": run.stderr,
        }
        return CommandOutcome(
            "index.here",
            data,
            f"indexed {target.project_ref} via {target.shadow_root}",
        )

    if args.action is None:
        args.action = "rebuild"
        args.changed_only = False

    if args.action == "watch":
        runtime = _index_runtime(paths)
        targets = resolve_watch_targets(paths, args.projects, strict=bool(args.projects))
        state = start_watch(
            paths,
            targets,
            dexterity_root=runtime.dexterity_root,
            dexter_bin=runtime.dexter_bin,
            shadow_root=runtime.shadow_root,
            daemon=args.daemon,
            poll_interval_ms=args.poll_interval_ms,
            debounce_ms=args.debounce_ms,
            ttl_ms=args.ttl_ms,
            once=args.once or not args.daemon,
        )
        data = {
            "watcher": {
                "running": state.running,
                "pid": state.pid,
                "watcher_type": state.watcher_type,
                "poll": args.poll,
                "daemon": args.daemon,
                "once": args.once or not args.daemon,
            },
            "control": {
                "poll_interval_ms": args.poll_interval_ms,
                "debounce_ms": args.debounce_ms,
                "ttl_ms": args.ttl_ms,
            },
            "targets": _index_targets_data(targets),
        }
        text = (
            f"watcher running={state.running} projects={len(targets)} "
            f"daemon={args.daemon} once={args.once or not args.daemon}"
        )
        return CommandOutcome("index.watch", data, text)

    if args.action == "status":
        targets = resolve_watch_targets(paths, args.projects, strict=bool(args.projects))
        data = status_payload(paths, ttl_ms=args.ttl_ms, targets=targets)
        data["control"] = {"ttl_ms": args.ttl_ms, "all": args.all}
        return CommandOutcome("index.status", data, _index_status_text(data))

    if args.action == "refresh":
        runtime = _index_runtime(paths)
        targets = resolve_watch_targets(paths, args.projects, strict=bool(args.projects))
        state = refresh_projects(
            paths,
            targets,
            dexterity_root=runtime.dexterity_root,
            dexter_bin=runtime.dexter_bin,
            shadow_root=runtime.shadow_root,
        )
        status = status_payload(paths, ttl_ms=args.ttl_ms, targets=targets)
        data = {
            "targets": _index_targets_data(targets),
            "state": {
                "running": state.running,
                "pid": state.pid,
                "project_count": len(state.projects),
            },
            "status": status,
            "control": {
                "ttl_ms": args.ttl_ms,
                "wait_fresh_ms": args.wait_fresh_ms,
                "all": args.all,
            },
        }
        text = f"refreshed projects={len(targets)} fresh={status['summary']['fresh']}"
        return CommandOutcome("index.refresh", data, text)

    if args.action == "stop":
        data = stop_watch(paths, force=args.force)
        text = f"stop requested force={args.force} running={data['running']} pid={data['pid']}"
        return CommandOutcome("index.stop", data, text)

    if args.action not in {None, "rebuild"}:
        raise SystemExit("Usage: atlas index [rebuild|watch|status|refresh|stop]")

    with mutation_lock(paths, "state"):
        registry = scan_registry_with_stats(paths, changed_only=args.changed_only)
        sync = sync_note_graph(paths)
    data = {
        "registry": {
            "project_count": len(registry.projects),
            "scanned_roots": registry.scanned_roots,
            "reused_roots": registry.reused_roots,
            "changed_only": args.changed_only,
        },
        "sync": _sync_dict(sync),
    }
    text = f"registry={len(registry.projects)} notes_changed={sync.changed_notes} mode={sync.mode}"
    return CommandOutcome("index.rebuild", data, text)


def _intelligence_main(argv: list[str], _: bool) -> CommandOutcome:
    parser = argparse.ArgumentParser(
        prog="atlas intelligence",
        description="Control the Atlas persistent code-intelligence service.",
    )
    subparsers = parser.add_subparsers(dest="action")
    subparsers.add_parser("status")
    subparsers.add_parser("start")
    subparsers.add_parser("stop")
    subparsers.add_parser("serve")
    args = parser.parse_args(argv)
    paths = get_paths()
    ensure_state(paths)

    if args.action == "status":
        data = status_service(paths)
        return CommandOutcome("intelligence.status", data, json.dumps(data, indent=2))
    if args.action == "start":
        data = start_service(paths)
        return CommandOutcome("intelligence.start", data, json.dumps(data, indent=2))
    if args.action == "stop":
        data = stop_service(paths)
        return CommandOutcome("intelligence.stop", data, json.dumps(data, indent=2))
    if args.action == "serve":
        serve_intelligence_service(paths)
        return CommandOutcome("intelligence.serve", {"stopped": True}, "stopped")
    raise SystemExit("Usage: atlas intelligence [status|start|stop|serve]")


def _prune_main(argv: list[str], _: bool) -> CommandOutcome:
    parser = argparse.ArgumentParser(prog="atlas prune", description="Prune atlas artifacts.")
    subparsers = parser.add_subparsers(dest="action")
    snapshots_parser = subparsers.add_parser("snapshots")
    snapshots_parser.add_argument("--days", type=int, default=45)
    snapshots_parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if args.action != "snapshots":
        raise SystemExit("Usage: atlas prune snapshots [--days N] [--apply]")
    paths = get_paths()
    ensure_state(paths)
    cutoff = now_local().timestamp() - args.days * 86400
    doomed = [
        path
        for path in paths.snapshots_root.rglob("*")
        if path.is_file() and path.stat().st_mtime < cutoff
    ]
    if args.apply:
        with mutation_lock(paths, "snapshots"):
            for path in doomed:
                path.unlink()
    data = {"days": args.days, "apply": args.apply, "paths": [str(path) for path in doomed]}
    return CommandOutcome("prune.snapshots", data, "\n".join(str(path) for path in doomed))


def _find_main(argv: list[str], _: bool) -> CommandOutcome:
    parser = argparse.ArgumentParser(
        prog="atlas find", description="Search Atlas notes and memory."
    )
    parser.add_argument("query", nargs="+")
    args = parser.parse_args(argv)
    paths = get_paths()
    ensure_state(paths)
    query = " ".join(args.query)
    matches = _find_note_candidates(paths, query)
    text = "\n".join(f"{item['path']}:{item['line_number']}:{item['line']}" for item in matches)
    return CommandOutcome("find", {"query": query, "matches": matches}, text)


def _open_main(argv: list[str], json_mode: bool) -> CommandOutcome:
    return _note_main(["open", *argv], json_mode)


def _status_main(argv: list[str], _: bool) -> CommandOutcome:
    argparse.ArgumentParser(
        prog="atlas status", description="Show current atlas state."
    ).parse_args(argv)
    paths = get_paths()
    data = _status_data(paths)
    return CommandOutcome("status", data, _render_status_text(data))


def _next_main(argv: list[str], _: bool) -> CommandOutcome:
    argparse.ArgumentParser(
        prog="atlas next", description="Recommend the next atlas action."
    ).parse_args(argv)
    paths = get_paths()
    ensure_state(paths)
    data = _next_action(paths)
    text = f"{data['command']}\n{data['reason']}"
    return CommandOutcome("next", data, text)


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    json_mode, argv = _strip_global_flag(argv, "--json")
    guessed_command = _guess_command(argv)
    dispatch = {
        "help": _help_main,
        "menu": _menu_main,
        "init": _init_main,
        "install": _install_main,
        "config": _config_main,
        "registry": _registry_main,
        "resolve": _resolve_main,
        "status": _status_main,
        "next": _next_main,
        "today": _today_main,
        "capture": _capture_main,
        "review": _review_main,
        "promote": _promote_main,
        "note": _note_main,
        "context": _context_main,
        "snapshot": _snapshot_main,
        "related": _related_main,
        "def": _def_main,
        "refs": _refs_main,
        "symbols": _symbols_main,
        "files": _files_main,
        "ranked-files": _ranked_files_main,
        "ranked-symbols": _ranked_symbols_main,
        "impact": _impact_main,
        "blast": _blast_main,
        "cochanges": _cochanges_main,
        "exports": _exports_main,
        "unused-exports": _unused_exports_main,
        "test-only-exports": _test_only_exports_main,
        "repo-map": _repo_map_main,
        "dexter": _dexter_main,
        "index": _index_main,
        "intelligence": _intelligence_main,
        "prune": _prune_main,
        "find": _find_main,
        "open": _open_main,
    }

    try:
        if not argv:
            outcome = _dashboard_main([], json_mode)
        else:
            command, *rest = argv
            if command not in dispatch:
                raise AtlasCliError(
                    ExitCode.USAGE,
                    "unknown_command",
                    f"Unknown atlas command: {command}",
                    {"command": command},
                )
            outcome = dispatch[command](rest, json_mode)
        exit_code, payload = success(outcome.command, outcome.data)
    except BaseException as error:
        if isinstance(error, SystemExit) and error.code == 0:
            exit_code, payload = success(guessed_command, {})
            with suppress(Exception):
                append_event(get_paths(), guessed_command, argv, exit_code, payload)
            return exit_code
        exit_code, payload = map_exception(guessed_command, error)
        if json_mode:
            print_json(payload)
        else:
            errors = payload.get("errors", [])
            if errors:
                print(errors[0]["message"], file=sys.stderr)
        with suppress(Exception):
            append_event(get_paths(), guessed_command, argv, exit_code, payload)
        return exit_code

    if json_mode:
        print_json(payload)
    else:
        _write_text(outcome.text)
    append_event(get_paths(), outcome.command, argv, exit_code, payload)
    return exit_code
