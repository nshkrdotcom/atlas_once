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

from .bundles import manifest_dict, markdown_manifest, mix_manifest, stack_manifest
from .config import AtlasPaths, ensure_state, get_paths
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
from .notes import NoteGraphSyncResult, build_graph, create_note, sync_note_graph
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
    if command in {"registry", "review", "promote", "note", "context", "index"} and len(argv) > 1:
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


def _settings_dict(paths: AtlasPaths) -> dict[str, Any]:
    settings = ensure_state(paths)
    return {
        "project_roots": settings.project_roots,
        "auto_sync_relationships": settings.auto_sync_relationships,
        "review_window_days": settings.review_window_days,
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
            "data_home": str(paths.data_home),
            "state_home": str(paths.state_home),
            "events_path": str(paths.events_path),
        },
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
                f"{auto_count} inbox entr{'y' if auto_count == 1 else 'ies'} "
                "can be auto-promoted."
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
    return (
        "atlas status\n\n"
        f"data:  {data['storage']['data_home']}\n"
        f"state: {data['storage']['state_home']}\n\n"
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


def _registry_main(argv: list[str], _: bool) -> CommandOutcome:
    parser = argparse.ArgumentParser(
        prog="atlas registry", description="Manage the project registry."
    )
    subparsers = parser.add_subparsers(dest="action")
    scan_parser = subparsers.add_parser("scan")
    scan_parser.add_argument("--changed-only", action="store_true")
    subparsers.add_parser("list")
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


def _index_main(argv: list[str], _: bool) -> CommandOutcome:
    parser = argparse.ArgumentParser(prog="atlas index", description="Rebuild atlas indexes.")
    subparsers = parser.add_subparsers(dest="action")
    rebuild_parser = subparsers.add_parser("rebuild")
    rebuild_parser.add_argument("--changed-only", action="store_true")
    args = parser.parse_args(argv)
    if args.action is None:
        args.action = "rebuild"
        args.changed_only = False

    paths = get_paths()
    ensure_state(paths)
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
        "index": _index_main,
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
