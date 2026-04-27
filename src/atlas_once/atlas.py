from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .agent_context import scan_repo_structure
from .bundles import (
    manifest_dict,
    markdown_manifest,
    mix_manifest,
    ranked_manifest,
    stack_manifest,
)
from .cli_ui import Cell, Column, render_table
from .code_intelligence import (
    backend_query_timeout_seconds,
    current_directory_is_mix_project,
    ensure_intelligence_index,
    find_project_root,
    run_dexter_cli,
    run_dexterity_map,
    run_dexterity_query,
    target_dict,
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
from .git_health import status_for_selectors
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
    load_state,
    refresh_projects,
    resolve_watch_targets,
    start_watch,
    status_payload,
    stop_watch,
    watcher_is_active,
)
from .intelligence_service import (
    serve as serve_intelligence_service,
)
from .intelligence_service import (
    start_service,
    status_service,
    stop_service,
    warm_intelligence_service,
)
from .notes import NoteGraphSyncResult, build_graph, create_note, sync_note_graph
from .profiles import DEFAULT_INSTALL_PROFILE, get_profile, list_profiles, profile_dict
from .ranked_context import (
    DEFAULT_TREE_MAX_DEPTH,
    RankedContextsSeedResult,
    RankedRuntime,
    add_ranked_group,
    collect_ranked_context_tree,
    ensure_prepared_ranked_manifest,
    ensure_ranked_contexts_config,
    load_ranked_contexts_payload,
    load_ranked_default_runtime,
    prepare_ranked_manifest,
    prepared_manifest_dict,
    ranked_group_repo_summaries,
    ranked_group_summaries,
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
from .workflows import (
    list_presets,
    plan_or_run_direct,
    run_preset,
    show_preset,
    upsert_preset,
    workflow_list,
    workflow_status,
)


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
    return (
        "Usage: atlas context ranked <config-name>|prepare <config-name>|"
        "status <config-name>|tree <config-name>|repos <config-name>|groups"
    )


def _ranked_groups_text(groups_data: dict[str, object]) -> str:
    groups = groups_data["groups"]
    assert isinstance(groups, list)
    rows: list[dict[str, object]] = []
    for item in groups:
        assert isinstance(item, dict)
        rows.append(
            {
                "name": item["name"],
                "items": item["item_count"],
                "selectors": item["selector_count"],
            }
        )
    table = render_table(
        rows,
        [
            Column("name", "GROUP"),
            Column("items", "ITEMS", align="right"),
            Column("selectors", "SELECTORS", align="right"),
        ],
    )
    return "\n".join([f"ranked groups ({groups_data['group_count']})", table]).rstrip()


def _ranked_repos_text(repos_data: dict[str, object]) -> str:
    repos = repos_data["repos"]
    assert isinstance(repos, list)
    rows: list[dict[str, object]] = []
    for item in repos:
        assert isinstance(item, dict)
        rows.append(
            {
                "repo": item["repo_label"],
                "variant": item["variant_name"],
                "strategy": item["strategy"],
                "overrides": item["project_override_count"],
                "path": item["repo_root"],
            }
        )
    table = render_table(
        rows,
        [
            Column("repo", "REPO"),
            Column("variant", "VARIANT"),
            Column("strategy", "STRATEGY"),
            Column("overrides", "OVR", align="right"),
            Column("path", "PATH"),
        ],
    )
    title = f"ranked repos: {repos_data['config']} ({repos_data['repo_count']})"
    return "\n".join([title, table]).rstrip()


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
        "agent",
        "git",
        "workflow",
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
        "git_health_root": str(paths.git_health_root),
        "git_health_latest_path": str(paths.git_health_latest_path),
        "workflows_root": str(paths.workflows_root),
        "workflow_runs_root": str(paths.workflow_runs_root),
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
        "Fleet git status",
        "Workflow presets",
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
    if choice == "9":
        return _git_main(["status", "@all"], False)
    if choice == "10":
        return _workflow_main(["preset", "list"], False)
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
    ranked_group_parser = ranked_subparsers.add_parser("group")
    ranked_group_subparsers = ranked_group_parser.add_subparsers(dest="group_action")
    ranked_group_add_parser = ranked_group_subparsers.add_parser("add")
    ranked_group_add_parser.add_argument("name")
    ranked_group_add_parser.add_argument("refs", nargs="+")
    ranked_group_add_parser.add_argument("--variant", default="default")
    ranked_group_add_parser.add_argument("--force", action="store_true")

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
        if args.ranked_action == "group":
            if args.group_action == "add":
                with mutation_lock(paths, "state"):
                    group_data = add_ranked_group(
                        paths,
                        args.name,
                        args.refs,
                        default_variant=args.variant,
                        force=args.force,
                    )
                group_items = group_data["items"]
                assert isinstance(group_items, list)
                refs_text = " ".join(
                    f"{item['ref']}:{item['variant']}"
                    for item in group_items
                    if isinstance(item, dict)
                )
                return CommandOutcome(
                    "config.ranked.group.add",
                    {"group": group_data},
                    f"{group_data['name']}: {refs_text}",
                )
            raise SystemExit("Usage: atlas config ranked group add <name> <ref[:variant]>...")
        raise SystemExit("Usage: atlas config ranked <path|show|install|group>")

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
        text = render_table(
            [
                {
                    "name": record.name,
                    "path": record.path,
                    "aliases": ", ".join(record.aliases),
                }
                for record in registry
            ],
            [
                Column("name", "PROJECT"),
                Column("path", "PATH"),
                Column("aliases", "ALIASES"),
            ],
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
    ranked_parser.add_argument("--include", action="append", default=None)
    ranked_parser.add_argument("--all", dest="include_all", action="store_true")
    ranked_parser.add_argument("--max-depth", type=int, default=DEFAULT_TREE_MAX_DEPTH)
    ranked_parser.add_argument("--names", action="store_true")
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
        if args.target in {"prepare", "status", "tree", "repos"}:
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
        elif args.target == "groups":
            ranked_mode = "groups"
            if args.output is not None:
                raise SystemExit(
                    "`-o/--output` is only supported for atlas context ranked <config-name>."
                )

        freshness = (
            None
            if ranked_mode in {"groups", "repos"}
            else ranked_index_freshness_payload(
                paths,
                config_name,
                ttl_ms=args.ttl_ms,
                wait_fresh_ms=args.wait_fresh_ms,
                allow_stale=args.allow_stale,
            )
        )

        if ranked_mode == "groups":
            groups_data = ranked_group_summaries(paths)
            groups = groups_data["groups"]
            assert isinstance(groups, list)
            names = [str(item["name"]) for item in groups if isinstance(item, dict)]
            data = {"groups": groups_data, "names": names}
            text = "\n".join(names) if args.names else _ranked_groups_text(groups_data)
            return CommandOutcome("context.ranked.groups", data, None if json_mode else text)

        if ranked_mode == "repos":
            repos_data = ranked_group_repo_summaries(paths, config_name)
            repos = repos_data["repos"]
            assert isinstance(repos, list)
            names = [str(item["repo_label"]) for item in repos if isinstance(item, dict)]
            data = {"config": config_name, "repos": repos_data, "names": names}
            text = "\n".join(names) if args.names else _ranked_repos_text(repos_data)
            return CommandOutcome("context.ranked.repos", data, None if json_mode else text)

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
            prepared, auto_prepared, auto_prepare_reason = ensure_prepared_ranked_manifest(
                paths,
                config_name,
                progress=None if json_mode else _write_progress,
            )
            prepared_data = {
                "config": config_name,
                "index_freshness": freshness,
                "auto_prepared": auto_prepared,
                "auto_prepare_reason": auto_prepare_reason,
                "prepared_manifest": prepared_manifest_dict(prepared),
            }
            text = (
                f"prepared {config_name}: {prepared.manifest_path}\n"
                f"prepared_at={prepared.prepared_at} repos={prepared.repo_count} "
                f"projects={prepared.project_count} files={len(prepared.files)} "
                f"auto_prepared={auto_prepared}"
            )
            return CommandOutcome(
                "context.ranked.status",
                prepared_data,
                None if json_mode else text,
            )

        if ranked_mode == "tree":
            prepared, auto_prepared, auto_prepare_reason = ensure_prepared_ranked_manifest(
                paths,
                config_name,
                progress=None if json_mode else _write_progress,
            )
            tree = collect_ranked_context_tree(
                prepared,
                include_prefixes=args.include,
                max_depth=args.max_depth,
                include_all=args.include_all,
            )
            tree_data = {
                "config": config_name,
                "index_freshness": freshness,
                "auto_prepared": auto_prepared,
                "auto_prepare_reason": auto_prepare_reason,
                "prepared_manifest": prepared_manifest_dict(prepared),
                "tree": tree.data,
            }
            return CommandOutcome(
                "context.ranked.tree",
                tree_data,
                None if json_mode else tree.text,
            )

        prepared, auto_prepared, auto_prepare_reason = ensure_prepared_ranked_manifest(
            paths,
            config_name,
            progress=None if json_mode else _write_progress,
        )
        manifest = ranked_manifest(paths, config_name)
        ranked_data: dict[str, Any] = {
            "config": config_name,
            "index_freshness": freshness,
            "auto_prepared": auto_prepared,
            "auto_prepare_reason": auto_prepare_reason,
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
    text = render_table(
        [
            {"index": index, "path": candidate}
            for index, candidate in enumerate(items[: args.limit], start=1)
        ],
        [Column("index", "#", align="right"), Column("path", "PATH")],
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


AGENT_STOPWORDS = {
    "about",
    "above",
    "after",
    "again",
    "agentic",
    "around",
    "before",
    "build",
    "change",
    "code",
    "create",
    "feature",
    "fix",
    "from",
    "functionality",
    "implement",
    "into",
    "logic",
    "make",
    "modules",
    "need",
    "new",
    "architecture",
    "key",
    "repo",
    "repository",
    "support",
    "that",
    "the",
    "this",
    "understand",
    "update",
    "with",
    "work",
}

AGENT_QUERY_TIMEOUT_ENV = "ATLAS_ONCE_AGENT_QUERY_TIMEOUT_SECONDS"
AGENT_LOCK_TIMEOUT_ENV = "ATLAS_ONCE_AGENT_LOCK_TIMEOUT_SECONDS"
DEFAULT_AGENT_LOCK_TIMEOUT_SECONDS = 10.0


def _agent_help_text() -> str:
    return (
        "atlas agent\n\n"
        "Start here:\n"
        '  atlas agent task "add streaming support"\n'
        "  atlas agent status\n\n"
        "Focused navigation:\n"
        "  atlas agent find Agent\n"
        "  atlas agent def ClaudeAgentSDK.Agent\n"
        "  atlas agent refs ClaudeAgentSDK.Agent\n"
        "  atlas agent related lib/claude_agent_sdk/agent.ex\n"
        "  atlas agent impact lib/claude_agent_sdk/agent.ex\n\n"
        "Use --json for machine-readable output. All commands default to the current "
        "Mix repo and use Atlas shadow indexes."
    )


def _agent_project_parser(prog: str, description: str) -> argparse.ArgumentParser:
    parser = _project_option_parser(prog, description)
    parser.add_argument(
        "--include-external",
        action="store_true",
        help="Include stdlib/dependency results for ranked commands.",
    )
    return parser


def _agent_env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _agent_query_timeout_seconds() -> float:
    return _agent_env_float(AGENT_QUERY_TIMEOUT_ENV, backend_query_timeout_seconds())


def _agent_lock_timeout_seconds() -> float:
    return _agent_env_float(AGENT_LOCK_TIMEOUT_ENV, DEFAULT_AGENT_LOCK_TIMEOUT_SECONDS)


def _agent_single_target_status(
    paths: AtlasPaths,
    project: str,
    *,
    ttl_ms: int = DEFAULT_TTL_MS,
) -> dict[str, Any]:
    targets = resolve_watch_targets(paths, [project], strict=True)
    status = status_payload(paths, ttl_ms=ttl_ms, targets=targets)
    return status


def _agent_status_text(data: dict[str, Any]) -> str:
    project = data.get("project", {})
    freshness = data.get("freshness", {})
    intelligence = data.get("intelligence", {})
    structure = data.get("repo_structure", {})
    return (
        "atlas agent status\n"
        f"project={project.get('project_ref')} path={project.get('project_path')}\n"
        f"index={freshness.get('status')} source_dirty={freshness.get('source_dirty')} "
        f"queue={freshness.get('queue_depth')}\n"
        f"mix_projects={structure.get('mix_project_count', 0)} "
        f"multi_mix={structure.get('multi_mix', False)}\n"
        f"intelligence_running={intelligence.get('running', False)} "
        f"pid={intelligence.get('pid')}\n"
        'next=atlas agent task "<goal>"'
    )


def _agent_status_data(project: str, *, ttl_ms: int = DEFAULT_TTL_MS) -> dict[str, Any]:
    paths = get_paths()
    ensure_state(paths)
    index_status = _agent_single_target_status(paths, project, ttl_ms=ttl_ms)
    project_rows = index_status.get("projects", [])
    project_row = project_rows[0] if project_rows else {}
    repo_structure = {}
    project_path = project_row.get("project_path")
    if isinstance(project_path, str) and project_path:
        repo_structure = scan_repo_structure(Path(project_path))
    return {
        "project": {
            "project_ref": project_row.get("project_ref"),
            "project_key": project_row.get("project_key"),
            "project_path": project_row.get("project_path"),
        },
        "freshness": project_row,
        "repo_structure": repo_structure,
        "index": index_status,
        "intelligence": status_service(paths),
        "commands": {
            "task": 'atlas agent task "<goal>"',
            "find": "atlas agent find <query>",
            "definition": "atlas agent def <Module>",
            "references": "atlas agent refs <Module>",
            "related": "atlas agent related <file>",
            "impact": "atlas agent impact <file>",
        },
    }


def _agent_command_metadata(
    *,
    name: str,
    project: str,
    next_commands: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "project": project,
        "next_commands": next_commands or [],
    }


def _agent_result_text(title: str, data: dict[str, Any]) -> str:
    result = data.get("result")
    if isinstance(result, str):
        body = result.rstrip()
    else:
        body = json.dumps(result, indent=2, sort_keys=True)
    next_commands = data.get("agent", {}).get("next_commands", [])
    next_text = "\n".join(f"  {command}" for command in next_commands)
    if next_text:
        return f"{title}\n\n{body}\n\nnext:\n{next_text}".rstrip()
    return f"{title}\n\n{body}".rstrip()


def _agent_terms(goal: str, *, limit: int) -> list[str]:
    seen: set[str] = set()
    terms: list[str] = []

    def add(term: str) -> None:
        normalized = term.strip("_")
        if not normalized:
            return
        key = normalized.lower()
        if key in seen or key in AGENT_STOPWORDS:
            return
        seen.add(key)
        terms.append(normalized)

    if "agent" in goal.lower():
        add("Agent")

    for raw in re.findall(r"[A-Za-z][A-Za-z0-9_]+", goal):
        lowered = raw.lower()
        if len(lowered) < 4 or lowered in AGENT_STOPWORDS:
            continue
        if raw.islower():
            add(raw[:1].upper() + raw[1:])
        else:
            add(raw)
        if len(terms) >= limit:
            break
    return terms[:limit]


def _agent_task_text(data: dict[str, Any]) -> str:
    lines = [
        "atlas agent task",
        f"goal={data['goal']}",
        (
            f"project={data['project'].get('project_ref')} "
            f"path={data['project'].get('project_path')}"
        ),
        (
            f"index={data['freshness'].get('status')} "
            f"source_dirty={data['freshness'].get('source_dirty')}"
        ),
    ]
    terms = data.get("terms", [])
    if terms:
        lines.append(f"terms={', '.join(terms)}")
    structure = data.get("repo_structure", {})
    if structure:
        layer_counts = structure.get("layer_counts", {})
        layer_text = ", ".join(f"{key}={value}" for key, value in layer_counts.items())
        lines.append(
            "\nrepo structure:\n"
            f"  mix_projects={structure.get('mix_project_count', 0)} "
            f"multi_mix={structure.get('multi_mix', False)}"
            + (f" layers=({layer_text})" if layer_text else "")
        )
        modules = structure.get("modules", [])
        if modules:
            lines.append("  modules:")
            for module in modules[:6]:
                if isinstance(module, dict):
                    lines.append(f"    - {module.get('module')} ({module.get('file')})")
    ranked = data.get("ranked_files", {}).get("result") or []
    likely = data.get("likely_files") or [
        item[0] for item in ranked if isinstance(item, list) and item
    ]
    if likely:
        lines.append("\nlikely files:")
        for item in likely[:10]:
            lines.append(f"  - {item}")
    searches = data.get("symbol_searches", [])
    if searches:
        lines.append("\nsymbol searches:")
        for search in searches:
            result = search.get("result") or []
            lines.append(f"  - {search.get('term')}: {len(result)} hit(s)")
    errors = data.get("backend_errors", [])
    if errors:
        lines.append("\nbackend errors:")
        for error in errors:
            if isinstance(error, dict):
                lines.append(
                    f"  - {error.get('stage')}: {error.get('kind')} - {error.get('message')}"
                )
    lines.append("\nnext:")
    lines.extend(f"  {command}" for command in data.get("next_commands", []))
    return "\n".join(lines).rstrip()


def _agent_symbol_files(symbol_searches: list[dict[str, Any]], *, limit: int = 3) -> list[str]:
    seen: set[str] = set()
    files: list[str] = []
    for search in symbol_searches:
        groups = search.get("result_groups", {})
        grouped = []
        if isinstance(groups, dict):
            grouped = list(groups.get("implementation") or [])
        candidates = grouped or list(search.get("result") or [])
        for item in candidates:
            if not isinstance(item, dict):
                continue
            file_path = item.get("file")
            if not isinstance(file_path, str) or not file_path:
                continue
            if file_path in seen:
                continue
            seen.add(file_path)
            files.append(file_path)
            if len(files) >= limit:
                return files
    return files


def _agent_tool_summary(data: dict[str, Any]) -> dict[str, Any]:
    tool = data.get("tool", {})
    cache = tool.get("cache", {}) if isinstance(tool, dict) else {}
    service = tool.get("service", {}) if isinstance(tool, dict) else {}
    return {
        "kind": tool.get("kind"),
        "transport": tool.get("transport"),
        "cached": tool.get("cached"),
        "cache_hit": cache.get("hit"),
        "attempts": tool.get("attempts"),
        "service_used": service.get("used"),
    }


def _agent_index_summary(data: dict[str, Any]) -> dict[str, Any]:
    index = data.get("index", {})
    freshness = index.get("freshness", {}) if isinstance(index, dict) else {}
    return {
        "skipped": index.get("skipped"),
        "status": freshness.get("status"),
        "waited_ms": freshness.get("waited_ms"),
    }


def _agent_intelligence_summary(data: dict[str, Any]) -> dict[str, Any]:
    pool = data.get("pool", {}) if isinstance(data, dict) else {}
    return {
        "running": data.get("running"),
        "pid": data.get("pid"),
        "worker_count": pool.get("worker_count"),
        "max_workers": pool.get("max_workers"),
    }


def _agent_error_payload(error: BaseException, *, stage: str) -> dict[str, Any]:
    if isinstance(error, AtlasCliError):
        return {
            "stage": stage,
            "kind": error.kind,
            "message": error.message,
            "details": error.details or {},
        }
    return {
        "stage": stage,
        "kind": error.__class__.__name__,
        "message": str(error),
        "details": {},
    }


def _agent_query_kwargs() -> dict[str, Any]:
    return {
        "timeout_seconds": _agent_query_timeout_seconds(),
        "lock_timeout_seconds": _agent_lock_timeout_seconds(),
    }


def _agent_structure_terms(structure: dict[str, Any], *, limit: int = 1) -> list[str]:
    seen: set[str] = set()
    terms: list[str] = []
    for item in structure.get("modules", []):
        if not isinstance(item, dict):
            continue
        module = item.get("module")
        if not isinstance(module, str) or "." not in module:
            continue
        prefix = module.split(".", 1)[0]
        key = prefix.lower()
        if key in seen:
            continue
        seen.add(key)
        terms.append(prefix)
        if len(terms) >= limit:
            return terms
    return terms


def _agent_structure_seed_files(structure: dict[str, Any], *, limit: int = 3) -> list[str]:
    files = structure.get("key_files", [])
    if not isinstance(files, list):
        return []
    return [item for item in files if isinstance(item, str)][:limit]


def _agent_structure_symbol_matches(
    structure: dict[str, Any],
    query: str,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    lowered = query.lower()
    matches: list[dict[str, Any]] = []
    for item in structure.get("modules", []):
        if not isinstance(item, dict):
            continue
        module = item.get("module")
        file_path = item.get("file")
        if not isinstance(module, str) or not isinstance(file_path, str):
            continue
        if lowered not in module.lower() and lowered not in file_path.lower():
            continue
        matches.append(
            {
                "module": module,
                "file": file_path,
                "source": "repo_structure",
            }
        )
        if len(matches) >= limit:
            break
    return matches


def _agent_project_root(paths: AtlasPaths, reference: str) -> Path:
    raw = (reference or ".").strip() or "."
    candidate = Path(raw).expanduser()
    if raw in {".", ".."} or "/" in raw or raw.startswith("~") or candidate.exists():
        return find_project_root(candidate)
    record = resolve_project_ref(paths, raw)
    return Path(record.path).expanduser().resolve()


def _agent_main(argv: list[str], _: bool) -> CommandOutcome:
    if not argv:
        return CommandOutcome("agent.help", {"text": _agent_help_text()}, _agent_help_text())

    action, *rest = argv
    if action in {"help", "quickstart", "explain"}:
        argparse.ArgumentParser(
            prog=f"atlas agent {action}",
            description="Show agent-oriented Atlas commands.",
        ).parse_args(rest)
        return CommandOutcome("agent.help", {"text": _agent_help_text()}, _agent_help_text())

    if action == "status":
        parser = _project_option_parser("atlas agent status", "Show agent-ready project status.")
        parser.add_argument("--ttl-ms", type=int, default=DEFAULT_TTL_MS)
        args = parser.parse_args(rest)
        data = _agent_status_data(args.project, ttl_ms=args.ttl_ms)
        return CommandOutcome("agent.status", data, _agent_status_text(data))

    if action in {"find", "symbols"}:
        parser = _project_option_parser("atlas agent find", "Search symbols with agent defaults.")
        parser.add_argument("--limit", type=int, default=10)
        parser.add_argument("query", nargs="+")
        args = parser.parse_args(rest)
        query = " ".join(args.query)
        paths = get_paths()
        ensure_state(paths)
        find_backend_errors: list[dict[str, Any]] = []
        try:
            data = run_dexterity_query(
                paths,
                "symbols",
                [query],
                reference=args.project,
                option_args=["--limit", str(args.limit)],
                use_service=True,
                **_agent_query_kwargs(),
            )
        except Exception as error:
            find_backend_errors.append(_agent_error_payload(error, stage=f"symbols:{query}"))
            project_root = _agent_project_root(paths, args.project)
            structure = scan_repo_structure(project_root)
            result = _agent_structure_symbol_matches(structure, query, limit=args.limit)
            data = {
                "project": {
                    "project_ref": project_root.name,
                    "repo_root": str(project_root),
                },
                "repo_structure": structure,
                "result": result,
                "result_groups": {"repo_structure": result},
                "backend_errors": find_backend_errors,
            }
        data["agent"] = _agent_command_metadata(
            name="find",
            project=args.project,
            next_commands=[
                "atlas agent def <Module>",
                "atlas agent refs <Module>",
                "atlas agent related <file>",
            ],
        )
        data["query"] = query
        return CommandOutcome("agent.find", data, _agent_result_text("atlas agent find", data))

    if action in {"def", "definition"}:
        parser = _project_option_parser("atlas agent def", "Find a definition.")
        parser.add_argument("module")
        parser.add_argument("function", nargs="?")
        parser.add_argument("arity", nargs="?")
        args = parser.parse_args(rest)
        positional = [args.module]
        if args.function is not None:
            positional.append(args.function)
        if args.arity is not None:
            positional.append(args.arity)
        paths = get_paths()
        ensure_state(paths)
        if args.function is None:
            data = run_dexter_cli(
                paths,
                "lookup",
                positional,
                reference=args.project,
                **_agent_query_kwargs(),
            )
            data["result"] = data.get("stdout", "")
        else:
            data = run_dexterity_query(
                paths,
                "definition",
                positional,
                reference=args.project,
                use_service=True,
                **_agent_query_kwargs(),
            )
        data["agent"] = _agent_command_metadata(
            name="definition",
            project=args.project,
            next_commands=[
                f"atlas agent refs {args.module}",
                "atlas agent impact <file>",
            ],
        )
        return CommandOutcome("agent.def", data, _agent_result_text("atlas agent def", data))

    if action in {"refs", "references"}:
        parser = _project_option_parser("atlas agent refs", "Find references.")
        parser.add_argument("module")
        parser.add_argument("function", nargs="?")
        parser.add_argument("arity", nargs="?")
        args = parser.parse_args(rest)
        positional = [args.module]
        if args.function is not None:
            positional.append(args.function)
        if args.arity is not None:
            positional.append(args.arity)
        paths = get_paths()
        ensure_state(paths)
        data = run_dexterity_query(
            paths,
            "references",
            positional,
            reference=args.project,
            use_service=True,
            **_agent_query_kwargs(),
        )
        data["agent"] = _agent_command_metadata(
            name="references",
            project=args.project,
            next_commands=["atlas agent related <file>", "atlas agent impact <file>"],
        )
        return CommandOutcome("agent.refs", data, _agent_result_text("atlas agent refs", data))

    if action in {"related", "ranked-files"}:
        parser = _agent_project_parser(
            "atlas agent related",
            "Rank files related to the current edit target.",
        )
        parser.add_argument("--limit", type=int, default=10)
        parser.add_argument("--mentioned", action="append", dest="mentioned_files")
        parser.add_argument("--edited", action="append", dest="edited_files")
        parser.add_argument("active", nargs="?")
        args = parser.parse_args(rest)
        paths = get_paths()
        ensure_state(paths)
        option_args: list[str] = ["--limit", str(args.limit)]
        if args.active:
            option_args.extend(["--active-file", args.active])
        for value in args.mentioned_files or []:
            option_args.extend(["--mentioned-file", value])
        for value in args.edited_files or []:
            option_args.extend(["--edited-file", value])
        data = run_dexterity_query(
            paths,
            "ranked_files",
            [],
            reference=args.project,
            option_args=option_args,
            filter_repo_source=not args.include_external,
            use_service=True,
            **_agent_query_kwargs(),
        )
        data["agent"] = _agent_command_metadata(
            name="related",
            project=args.project,
            next_commands=["atlas agent impact <file>", "atlas agent refs <Module>"],
        )
        return CommandOutcome(
            "agent.related",
            data,
            _agent_result_text("atlas agent related", data),
        )

    if action == "impact":
        parser = _agent_project_parser("atlas agent impact", "Build focused impact context.")
        parser.add_argument("--token-budget", type=int, default=5000)
        parser.add_argument("--limit", type=int)
        parser.add_argument("file")
        args = parser.parse_args(rest)
        option_args = ["--changed-file", args.file, "--token-budget", str(args.token_budget)]
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
            use_service=True,
            **_agent_query_kwargs(),
        )
        data["agent"] = _agent_command_metadata(
            name="impact",
            project=args.project,
            next_commands=["atlas agent refs <Module>", "atlas agent related <file>"],
        )
        return CommandOutcome("agent.impact", data, _agent_result_text("atlas agent impact", data))

    if action == "map":
        parser = _project_option_parser(
            "atlas agent map",
            "Render the full repo map explicitly. This is not used by agent task defaults.",
        )
        parser.add_argument("--active", "--active-file", action="append", dest="active_files")
        parser.add_argument(
            "--mentioned",
            "--mentioned-file",
            action="append",
            dest="mentioned_files",
        )
        parser.add_argument("--edited", "--edited-file", action="append", dest="edited_files")
        parser.add_argument("--limit", type=int)
        parser.add_argument("--token-budget")
        args = parser.parse_args(rest)
        paths = get_paths()
        ensure_state(paths)
        data = run_dexterity_map(
            paths,
            reference=args.project,
            option_args=_ranked_common_options(args),
            **_agent_query_kwargs(),
        )
        data["agent"] = _agent_command_metadata(name="map", project=args.project)
        return CommandOutcome("agent.map", data, _agent_result_text("atlas agent map", data))

    if action == "task":
        parser = _project_option_parser("atlas agent task", "Build compact task context.")
        parser.add_argument("--active", "--active-file", action="append", dest="active_files")
        parser.add_argument(
            "--mentioned",
            "--mentioned-file",
            action="append",
            dest="mentioned_files",
        )
        parser.add_argument("--edited", "--edited-file", action="append", dest="edited_files")
        parser.add_argument("--limit", type=int, default=10)
        parser.add_argument("--symbol-limit", type=int, default=5)
        parser.add_argument("--max-terms", type=int, default=3)
        parser.add_argument("--token-budget", type=int, default=5000)
        parser.add_argument("--include-external", action="store_true")
        parser.add_argument("goal", nargs="+")
        args = parser.parse_args(rest)
        goal = " ".join(args.goal)
        paths = get_paths()
        ensure_state(paths)
        project_root = _agent_project_root(paths, args.project)
        repo_structure = scan_repo_structure(project_root)
        terms = _agent_terms(goal, limit=args.max_terms)
        if not terms and args.active_files:
            terms = _agent_structure_terms(repo_structure, limit=1)
        symbol_searches: list[dict[str, Any]] = []
        backend_errors: list[dict[str, Any]] = []
        for term in terms:
            try:
                search = run_dexterity_query(
                    paths,
                    "symbols",
                    [term],
                    reference=args.project,
                    option_args=["--limit", str(args.symbol_limit)],
                    use_service=True,
                    **_agent_query_kwargs(),
                )
            except Exception as error:
                backend_errors.append(_agent_error_payload(error, stage=f"symbols:{term}"))
                break
            symbol_searches.append(
                {
                    "term": term,
                    "result": search.get("result"),
                    "result_groups": search.get("result_groups", {}),
                    "tool": _agent_tool_summary(search),
                    "index": _agent_index_summary(search),
                }
            )

        seed_files = list(args.active_files or [])
        if not seed_files:
            seed_files = _agent_symbol_files(symbol_searches)
        if not seed_files:
            seed_files = _agent_structure_seed_files(repo_structure)

        ranked_options: list[str] = ["--limit", str(args.limit)]
        for value in seed_files:
            ranked_options.extend(["--active-file", value])
        for value in args.mentioned_files or []:
            ranked_options.extend(["--mentioned-file", value])
        for value in args.edited_files or []:
            ranked_options.extend(["--edited-file", value])
        ranked_files: dict[str, Any] = {
            "result": [[file_path, None] for file_path in seed_files],
            "filter": {"mode": "repo_structure"},
            "tool": {"kind": "repo_structure"},
            "index": {},
        }
        should_rank = bool(seed_files and not backend_errors and (terms or args.active_files))
        if should_rank:
            try:
                ranked_files = run_dexterity_query(
                    paths,
                    "ranked_files",
                    [],
                    reference=args.project,
                    option_args=ranked_options,
                    filter_repo_source=not args.include_external,
                    use_service=True,
                    **_agent_query_kwargs(),
                )
            except Exception as error:
                backend_errors.append(_agent_error_payload(error, stage="ranked_files"))

        impact_contexts: list[dict[str, Any]] = []
        impact_files = [*(args.active_files or []), *(args.edited_files or [])]
        seen_impact: set[str] = set()
        for file_path in impact_files:
            if file_path in seen_impact:
                continue
            seen_impact.add(file_path)
            try:
                impact = run_dexterity_query(
                    paths,
                    "impact_context",
                    [],
                    reference=args.project,
                    option_args=[
                        "--changed-file",
                        file_path,
                        "--token-budget",
                        str(args.token_budget),
                    ],
                    filter_repo_source=not args.include_external,
                    filter_text=True,
                    use_service=True,
                    **_agent_query_kwargs(),
                )
            except Exception as error:
                backend_errors.append(_agent_error_payload(error, stage=f"impact:{file_path}"))
                continue
            impact_contexts.append(
                {
                    "file": file_path,
                    "result": impact.get("result"),
                    "tool": _agent_tool_summary(impact),
                    "index": _agent_index_summary(impact),
                    "filter": impact.get("filter", {}),
                }
            )

        status = _agent_status_data(args.project)
        active = seed_files[0] if seed_files else "<file>"
        first_module = "<Module>"
        for search in symbol_searches:
            for item in search.get("result") or []:
                if isinstance(item, dict) and item.get("module"):
                    first_module = str(item["module"])
                    break
            if first_module != "<Module>":
                break
        next_commands = [
            f"atlas agent related {active}" if active != "<file>" else "atlas agent related <file>",
            f"atlas agent impact {active}" if active != "<file>" else "atlas agent impact <file>",
            f"atlas agent refs {first_module}",
        ]
        likely_files = [
            item[0]
            for item in ranked_files.get("result") or []
            if isinstance(item, (list, tuple)) and item and isinstance(item[0], str)
        ]
        if not likely_files:
            likely_files = seed_files
        data = {
            "goal": goal,
            "project": status["project"],
            "freshness": status["freshness"],
            "repo_structure": repo_structure,
            "intelligence": _agent_intelligence_summary(status["intelligence"]),
            "terms": terms,
            "seed_files": seed_files,
            "likely_files": likely_files,
            "symbol_searches": symbol_searches,
            "ranked_files": {
                "result": ranked_files.get("result"),
                "filter": ranked_files.get("filter"),
                "tool": _agent_tool_summary(ranked_files),
                "index": _agent_index_summary(ranked_files),
            },
            "impact_contexts": impact_contexts,
            "backend_errors": backend_errors,
            "next_commands": next_commands,
        }
        return CommandOutcome("agent.task", data, _agent_task_text(data))

    raise SystemExit(
        "Usage: atlas agent "
        "[status|task|find|def|refs|related|impact|map|quickstart|help] ..."
    )


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


def _split_selector_args(values: list[str] | None) -> list[str]:
    selectors: list[str] = []
    for value in values or []:
        selectors.extend(item.strip() for item in value.split(",") if item.strip())
    return selectors


def _git_status_text(data: dict[str, Any]) -> str:
    lines = [
        "atlas git status",
        (
            f"repos={data['repo_count']} dirty={data['dirty_count']} "
            f"unpushed={data['unpushed_count']} stale={data['stale_count']} "
            f"source={data['source']}"
        ),
    ]
    rows: list[dict[str, object]] = []
    for repo in data["repos"]:
        dirty = bool(
            repo.get("working_dirty")
            or repo.get("index_dirty")
            or repo.get("untracked_count")
            or repo.get("conflicted")
        )
        ahead = int(repo.get("ahead") or 0)
        behind = int(repo.get("behind") or 0)
        errors = repo.get("errors") or []
        stale = bool(repo.get("stale"))
        if errors:
            state = Cell("error", "red")
        elif repo.get("conflicted"):
            state = Cell("conflict", "red")
        elif dirty:
            state = Cell("dirty", "yellow")
        elif stale:
            state = Cell("stale", "magenta")
        else:
            state = Cell("clean", "green")
        rows.append(
            {
                "repo": repo.get("repo_ref"),
                "state": state,
                "ab": Cell(f"{ahead}/{behind}", "cyan" if ahead or behind else None),
                "branch": repo.get("branch") or "-",
                "path": repo.get("path"),
            }
        )
    table = render_table(
        rows,
        [
            Column("repo", "REPO"),
            Column("state", "STATE"),
            Column("ab", "A/B", align="right", min_width=3),
            Column("branch", "BRANCH"),
            Column("path", "PATH"),
        ],
    )
    if table:
        lines.append(table)
    return "\n".join(lines)


def _git_main(argv: list[str], _: bool) -> CommandOutcome:
    parser = argparse.ArgumentParser(prog="atlas git", description="Fleet git health commands.")
    subparsers = parser.add_subparsers(dest="action")
    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("selectors", nargs="*")
    status_parser.add_argument("--manifest")
    status_parser.add_argument(
        "--manifest-format",
        default="json",
        choices=("json", "yaml", "toml"),
    )
    status_parser.add_argument("--refresh", action="store_true")
    status_parser.add_argument("--wait-fresh-ms", type=int, default=0)
    status_parser.add_argument("--stale-after-ms", type=int)
    status_parser.add_argument("--timeout-per-repo", type=float)
    status_parser.add_argument("--order-by", choices=("dirty", "ahead", "branch", "name", "stale"))
    status_parser.add_argument("--include-clean", action="store_true")
    status_parser.add_argument("--include-errors", action="store_true")
    args = parser.parse_args(argv)
    if args.action not in {"status"}:
        raise SystemExit("Usage: atlas git status [selectors] [--refresh]")
    data = status_for_selectors(
        get_paths(),
        args.selectors,
        manifest=args.manifest,
        manifest_format=args.manifest_format,
        refresh=args.refresh,
        stale_after_ms=args.stale_after_ms,
        timeout_seconds=args.timeout_per_repo,
    )
    repos = data["repos"]
    if not args.include_errors:
        for repo in repos:
            if not repo.get("errors"):
                continue
            repo["errors"] = repo["errors"]
    if args.order_by == "dirty":
        repos = sorted(
            repos,
            key=lambda item: (
                not bool(
                    item.get("working_dirty")
                    or item.get("index_dirty")
                    or item.get("untracked_count")
                ),
                str(item.get("repo_ref")),
            ),
        )
    elif args.order_by == "ahead":
        repos = sorted(
            repos,
            key=lambda item: (-int(item.get("ahead") or 0), str(item.get("repo_ref"))),
        )
    elif args.order_by == "branch":
        repos = sorted(
            repos,
            key=lambda item: (str(item.get("branch") or ""), str(item.get("repo_ref"))),
        )
    elif args.order_by == "stale":
        repos = sorted(
            repos,
            key=lambda item: (not bool(item.get("stale")), str(item.get("repo_ref"))),
        )
    data["repos"] = repos
    data["wait_fresh_ms"] = args.wait_fresh_ms
    return CommandOutcome("git.status", data, _git_status_text(data))


def _prompt_run_sdk_main(argv: list[str], _: bool) -> CommandOutcome:
    parser = argparse.ArgumentParser(
        prog="atlas prompt-run-sdk",
        description="Run prompt_runner_sdk against selected Atlas repos.",
    )
    parser.add_argument("prompt_ref")
    parser.add_argument("provider")
    parser.add_argument("packet_root", nargs="?", default=".")
    parser.add_argument("--manifest")
    parser.add_argument("--manifest-format", default="json", choices=("json", "yaml", "toml"))
    parser.add_argument("--targets", action="append", dest="targets")
    parser.add_argument("--group", action="append", dest="groups")
    parser.add_argument("--preset")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--serial", action="store_true", default=True)
    mode.add_argument("--parallel", action="store_true")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-commit", action="store_true")
    parser.add_argument("--json-stream", action="store_true")
    args = parser.parse_args(argv)
    selectors = _split_selector_args(args.targets)
    selectors.extend(f"@group:{group}" for group in args.groups or [])
    data = plan_or_run_direct(
        get_paths(),
        prompt_ref=args.prompt_ref,
        provider=args.provider,
        packet_root=args.packet_root,
        selectors=selectors or None,
        manifest=args.manifest,
        manifest_format=args.manifest_format,
        model=args.model,
        serial=not args.parallel,
        concurrency=args.concurrency,
        timeout_seconds=args.timeout,
        dry_run=args.dry_run,
        no_commit=args.no_commit,
    )
    text = f"{data['run_id']} {data['status']} targets={len(data['targets'])}"
    return CommandOutcome("prompt-run-sdk", data, text)


def _workflow_main(argv: list[str], _: bool) -> CommandOutcome:
    parser = argparse.ArgumentParser(prog="atlas workflow", description="Manage prompt workflows.")
    subparsers = parser.add_subparsers(dest="action")
    preset_parser = subparsers.add_parser("preset")
    preset_subparsers = preset_parser.add_subparsers(dest="preset_action")
    preset_subparsers.add_parser("list")
    preset_show = preset_subparsers.add_parser("show")
    preset_show.add_argument("preset_id")
    preset_upsert = preset_subparsers.add_parser("upsert")
    preset_upsert.add_argument("preset_id")
    preset_upsert.add_argument("file", nargs="?", default="-")
    preset_run = preset_subparsers.add_parser("run")
    preset_run.add_argument("preset_id")
    preset_run.add_argument("--targets", action="append", dest="targets")
    preset_run.add_argument("--group", action="append", dest="groups")
    preset_run.add_argument("--provider")
    preset_run.add_argument("--model")
    preset_run.add_argument("--dry-run", action="store_true")
    subparsers.add_parser("list")
    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("run_id")
    cancel_parser = subparsers.add_parser("cancel")
    cancel_parser.add_argument("run_id")
    args = parser.parse_args(argv)
    paths = get_paths()
    if args.action == "preset":
        if args.preset_action == "list":
            data = list_presets(paths)
            text = "\n".join(str(item.get("id")) for item in data["presets"])
            return CommandOutcome("workflow.preset.list", data, text)
        if args.preset_action == "show":
            data = show_preset(paths, args.preset_id)
            text = json.dumps(data["preset"], indent=2, sort_keys=True)
            return CommandOutcome("workflow.preset.show", data, text)
        if args.preset_action == "upsert":
            raw = (
                sys.stdin.read()
                if args.file == "-"
                else Path(args.file).read_text(encoding="utf-8")
            )
            data = upsert_preset(paths, args.preset_id, json.loads(raw))
            return CommandOutcome("workflow.preset.upsert", data, args.preset_id)
        if args.preset_action == "run":
            selectors = _split_selector_args(args.targets)
            selectors.extend(f"@group:{group}" for group in args.groups or [])
            data = run_preset(
                paths,
                args.preset_id,
                selectors=selectors or None,
                provider=args.provider,
                model=args.model,
                dry_run=args.dry_run,
            )
            return CommandOutcome("workflow.preset.run", data, f"{data['run_id']} {data['status']}")
    if args.action == "list":
        data = workflow_list(paths)
        text = render_table(
            [
                {
                    "run": item["run_id"],
                    "status": Cell(
                        item["status"],
                        "green" if item["status"] in {"completed", "planned"} else "yellow",
                    ),
                }
                for item in data["runs"]
            ],
            [Column("run", "RUN"), Column("status", "STATUS")],
        )
        return CommandOutcome("workflow.list", data, text)
    if args.action == "status":
        data = workflow_status(paths, args.run_id)
        return CommandOutcome("workflow.status", data, json.dumps(data, indent=2, sort_keys=True))
    if args.action == "cancel":
        raise AtlasCliError(
            ExitCode.VALIDATION,
            "workflow_cancel_not_supported",
            "Workflow cancellation is not implemented for completed local subprocess runs.",
            {"run_id": args.run_id},
        )
    raise SystemExit("Usage: atlas workflow preset <list|show|upsert|run> | list | status <run-id>")


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


def _index_start_text(data: dict[str, Any]) -> str:
    watcher = data["watcher"]
    return (
        "atlas index start\n"
        f"running={watcher['running']} pid={watcher['pid']} "
        f"already_running={watcher['already_running']}\n"
        f"projects={len(data['targets'])} log={data['log_path']}"
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


def _index_start_command(args: Any) -> list[str]:
    command = [
        sys.argv[0] or "atlas",
        "index",
        "watch",
        "--daemon",
        "--poll-interval-ms",
        str(args.poll_interval_ms),
        "--debounce-ms",
        str(args.debounce_ms),
        "--ttl-ms",
        str(args.ttl_ms),
    ]
    for project in args.projects or []:
        command.extend(["--project", project])
    return command


def _index_start_background(paths: AtlasPaths, args: Any) -> dict[str, Any]:
    state, _ = load_state(paths)
    if watcher_is_active(state):
        targets = resolve_watch_targets(paths, args.projects, strict=bool(args.projects))
        return {
            "watcher": {
                "running": True,
                "pid": state.pid,
                "already_running": True,
                "watcher_type": state.watcher_type,
            },
            "control": {
                "poll_interval_ms": args.poll_interval_ms,
                "debounce_ms": args.debounce_ms,
                "ttl_ms": args.ttl_ms,
            },
            "targets": _index_targets_data(targets),
            "command": [],
            "log_path": str(paths.state_home / "logs" / "index-watcher.log"),
        }

    targets = resolve_watch_targets(paths, args.projects, strict=bool(args.projects))
    log_path = paths.state_home / "logs" / "index-watcher.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = _index_start_command(args)
    with log_path.open("a", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )

    observed_state = state
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        observed_state, _ = load_state(paths)
        if watcher_is_active(observed_state) and observed_state.pid == process.pid:
            break
        if process.poll() is not None:
            break
        time.sleep(0.05)

    running = watcher_is_active(observed_state)
    return {
        "watcher": {
            "running": running,
            "pid": observed_state.pid if running else process.pid,
            "already_running": False,
            "watcher_type": observed_state.watcher_type,
            "process_exit_code": process.poll(),
        },
        "control": {
            "poll_interval_ms": args.poll_interval_ms,
            "debounce_ms": args.debounce_ms,
            "ttl_ms": args.ttl_ms,
        },
        "targets": _index_targets_data(targets),
        "command": command,
        "log_path": str(log_path),
    }


def _index_main(argv: list[str], _: bool) -> CommandOutcome:
    parser = argparse.ArgumentParser(prog="atlas index", description="Manage atlas indexes.")
    subparsers = parser.add_subparsers(dest="action")
    here_parser = subparsers.add_parser("here")
    here_parser.add_argument("project", nargs="?", default=".")
    rebuild_parser = subparsers.add_parser("rebuild")
    rebuild_parser.add_argument("--changed-only", action="store_true")
    start_parser = subparsers.add_parser("start")
    start_parser.add_argument("--poll-interval-ms", type=int, default=DEFAULT_POLL_INTERVAL_MS)
    start_parser.add_argument("--debounce-ms", type=int, default=DEFAULT_DEBOUNCE_MS)
    start_parser.add_argument("--ttl-ms", type=int, default=DEFAULT_TTL_MS)
    start_parser.add_argument("--project", action="append", dest="projects")
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

    if args.action == "start":
        data = _index_start_background(paths, args)
        return CommandOutcome("index.start", data, _index_start_text(data))

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
    warm_parser = subparsers.add_parser("warm")
    warm_parser.add_argument("--project", action="append", dest="project_options")
    warm_parser.add_argument("projects", nargs="*")
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
    if args.action == "warm":
        start = start_service(paths)
        refs = [*(args.project_options or []), *args.projects] or ["."]
        warmed: list[dict[str, Any]] = []
        for ref in refs:
            target, index_run = ensure_intelligence_index(paths, ref, force=False)
            response = warm_intelligence_service(paths=paths, target=target)
            if response is None:
                raise AtlasCliError(
                    ExitCode.EXTERNAL,
                    "intelligence_warm_failed",
                    f"Failed to warm intelligence worker for {ref}",
                    {"project": ref},
                )
            warmed.append(
                {
                    "reference": ref,
                    "project": target_dict(target),
                    "index": {
                        "command": index_run.command,
                        "returncode": index_run.returncode,
                        "attempts": index_run.attempts,
                        "timed_out": index_run.timed_out,
                    },
                    "service": response,
                }
            )
        data = {
            "start": start,
            "warmed": warmed,
            "status": status_service(paths),
        }
        return CommandOutcome("intelligence.warm", data, json.dumps(data, indent=2))
    raise SystemExit("Usage: atlas intelligence [status|start|stop|serve|warm]")


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
        "agent": _agent_main,
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
        "git": _git_main,
        "prompt-run-sdk": _prompt_run_sdk_main,
        "workflow": _workflow_main,
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
