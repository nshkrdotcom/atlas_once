from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from .config import ensure_state, get_paths
from .dashboard import render_dashboard, render_topic_help
from .inbox import create_entry, promote_auto, promote_entry, review_daily, review_inbox
from .markdown_ctx import main as ctx_main
from .memory import (
    memfind_main,
    memopen_main,
    memsnap_main,
    prune_main,
    related_main,
    today_main,
)
from .mix_ctx import main as mixctx_main
from .multi_ctx import main as mcc_main
from .notes import create_note, sync_note_graph
from .registry import (
    add_alias,
    add_root,
    load_registry,
    remove_alias,
    remove_root,
    resolve_or_placeholder,
    resolve_project_ref,
    scan_registry,
)


def _dashboard() -> int:
    paths = get_paths()
    settings = ensure_state(paths)
    registry = load_registry(paths)
    if not registry and settings.project_roots:
        registry = scan_registry(paths, settings)
    print(render_dashboard(paths, settings, registry))
    return 0


def _help_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="atlas help", description="Show atlas help topics.")
    parser.add_argument("topic", nargs="?", default="")
    args = parser.parse_args(argv)
    if not args.topic:
        return _dashboard()
    print(render_topic_help(args.topic))
    return 0


def _init_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="atlas init",
        description="Initialize atlas storage and settings.",
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help="Scan project roots after bootstrapping.",
    )
    args = parser.parse_args(argv)
    paths = get_paths()
    settings = ensure_state(paths)
    if args.scan:
        registry = scan_registry(paths, settings)
        print(f"Initialized atlas at {paths.state_home} with {len(registry)} projects.")
        return 0
    print(f"Initialized atlas at {paths.state_home}.")
    return 0


def _registry_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="atlas registry",
        description="Manage the project registry.",
    )
    subparsers = parser.add_subparsers(dest="action")
    subparsers.add_parser("scan")
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
        print(render_topic_help("registry"))
        return 0

    paths = get_paths()
    settings = ensure_state(paths)
    if args.action == "scan":
        registry = scan_registry(paths, settings)
        print(f"Scanned {len(registry)} projects.")
        return 0
    if args.action == "list":
        registry = load_registry(paths) or scan_registry(paths, settings)
        for record in registry:
            print(f"{record.name}\t{record.path}\t{','.join(record.aliases)}")
        return 0
    if args.action == "resolve":
        print(resolve_project_ref(paths, args.reference).path)
        return 0
    if args.action == "show":
        record = resolve_project_ref(paths, args.reference)
        print(f"name: {record.name}")
        print(f"path: {record.path}")
        print(f"root: {record.root}")
        print(f"aliases: {', '.join(record.aliases)}")
        print(f"markers: {', '.join(record.markers)}")
        return 0
    if args.action == "root-add":
        updated = add_root(paths, args.path)
        print("\n".join(updated.project_roots))
        return 0
    if args.action == "root-remove":
        updated = remove_root(paths, args.path)
        print("\n".join(updated.project_roots))
        return 0
    if args.action == "alias-add":
        record = add_alias(paths, args.reference, args.alias)
        print(f"{record.name}: {', '.join(record.aliases)}")
        return 0
    record = remove_alias(paths, args.reference, args.alias)
    print(f"{record.name}: {', '.join(record.aliases)}")
    return 0


def _capture_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="atlas capture",
        description="Capture a structured inbox entry.",
    )
    parser.add_argument("--project", help="Project registry ref or explicit path-like name.")
    parser.add_argument(
        "--tag",
        action="append",
        default=[],
        help="Tag to associate with the entry.",
    )
    parser.add_argument(
        "--kind",
        default="note",
        choices=("note", "decision", "project", "topic", "person"),
        help="Entry kind used for review and auto-promotion.",
    )
    parser.add_argument("text", nargs="+")
    args = parser.parse_args(argv)
    paths = get_paths()
    ensure_state(paths)
    project = resolve_or_placeholder(paths, args.project) if args.project else None
    entry = create_entry(paths, " ".join(args.text), project=project, tags=args.tag, kind=args.kind)
    print(entry.source_path)
    return 0


def _review_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="atlas review",
        description="Review inbox and daily state.",
    )
    subparsers = parser.add_subparsers(dest="action")
    inbox_parser = subparsers.add_parser("inbox")
    inbox_parser.add_argument("--date")
    daily_parser = subparsers.add_parser("daily")
    daily_parser.add_argument("--date")
    args = parser.parse_args(argv)
    if args.action is None:
        print(render_topic_help("review"))
        return 0
    paths = get_paths()
    ensure_state(paths)
    if args.action == "inbox":
        print(review_inbox(paths, day=args.date))
        return 0
    print(review_daily(paths, day=args.date))
    return 0


def _promote_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="atlas promote",
        description="Promote inbox entries into durable notes.",
    )
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
        print(render_topic_help("review"))
        return 0

    paths = get_paths()
    ensure_state(paths)
    if args.action == "auto":
        for target in promote_auto(paths, day=args.date):
            print(target)
        return 0

    project = resolve_or_placeholder(paths, args.project) if args.project else None
    print(promote_entry(paths, args.entry_id, kind=args.kind, title=args.title, project=project))
    return 0


def _note_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="atlas note",
        description="Create, find, open, and sync notes.",
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
    find_parser = subparsers.add_parser("find")
    find_parser.add_argument("query", nargs="+")
    open_parser = subparsers.add_parser("open")
    open_parser.add_argument("--print", action="store_true", dest="print_only")
    open_parser.add_argument("query", nargs="*")
    subparsers.add_parser("sync")
    args = parser.parse_args(argv)

    if args.action is None:
        print(render_topic_help("note"))
        return 0

    if args.action == "find":
        return memfind_main(args.query)
    if args.action == "open":
        forwarded = [*args.query]
        if args.print_only:
            forwarded.insert(0, "--print")
        return memopen_main(forwarded)
    if args.action == "sync":
        paths = get_paths()
        ensure_state(paths)
        print(sync_note_graph(paths))
        return 0

    paths = get_paths()
    ensure_state(paths)
    project = resolve_or_placeholder(paths, args.project) if args.project else None
    target = create_note(
        paths,
        title=args.title,
        kind=args.kind,
        project=project,
        tags=args.tag,
        body=args.body,
    )
    print(target)
    return 0


def _context_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="atlas context",
        description="Build repo and note context bundles.",
    )
    subparsers = parser.add_subparsers(dest="action")
    notes_parser = subparsers.add_parser("notes")
    notes_parser.add_argument("--pwd-only", action="store_true")
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
        print(render_topic_help("context"))
        return 0

    if args.action == "notes":
        forwarded = [args.path]
        if args.pwd_only:
            forwarded.insert(0, "--pwd-only")
        return ctx_main(forwarded)

    paths = get_paths()
    ensure_state(paths)
    if args.action == "repo":
        reference = args.reference
        if not Path(reference).expanduser().exists():
            reference = resolve_project_ref(paths, reference).path
        repo_args: list[str] = []
        if args.group:
            repo_args.append(args.group)
        if args.output:
            repo_args.extend(["-o", args.output])
        repo_args.append(reference)
        return mixctx_main(repo_args)

    stack_args = list(args.items)
    if args.group:
        stack_args = ["--group", args.group, *stack_args]
    if args.remember:
        stack_args = ["--remember", *stack_args]
    if args.output:
        stack_args = [*stack_args, "-o", args.output]
    return mcc_main(stack_args)


def _menu_main(argv: list[str]) -> int:
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
    print("atlas menu")
    for index, option in enumerate(options, start=1):
        print(f"{index}. {option}")
    choice = input("\nSelection [1]: ").strip() or "1"
    if choice == "1":
        return _dashboard()
    if choice == "2":
        return _init_main([])
    if choice == "3":
        return today_main(["--print"])
    if choice == "4":
        text = input("Note: ").strip()
        if not text:
            raise SystemExit("No note text provided.")
        return _capture_main([text])
    if choice == "5":
        return _review_main(["inbox"])
    if choice == "6":
        return _registry_main(["scan"])
    if choice == "7":
        return _registry_main(["list"])
    if choice == "8":
        reference = input("Project ref: ").strip()
        return _registry_main(["resolve", reference])
    return 0


def _related_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="atlas related", description="Show related notes.")
    parser.add_argument("path")
    parser.add_argument("--limit", default="10")
    args = parser.parse_args(argv)
    return related_main([args.path, "--limit", args.limit])


def _index_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="atlas index", description="Rebuild atlas indexes.")
    subparsers = parser.add_subparsers(dest="action")
    subparsers.add_parser("rebuild")
    args = parser.parse_args(argv)
    if args.action is None:
        args.action = "rebuild"
    paths = get_paths()
    ensure_state(paths)
    registry = scan_registry(paths)
    changed = sync_note_graph(paths)
    print(f"registry={len(registry)} notes_updated={changed}")
    return 0


def _prune_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="atlas prune", description="Prune atlas artifacts.")
    subparsers = parser.add_subparsers(dest="action")
    snapshots_parser = subparsers.add_parser("snapshots")
    snapshots_parser.add_argument("--days", default="45")
    snapshots_parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if args.action != "snapshots":
        raise SystemExit("Usage: atlas prune snapshots [--days N] [--apply]")
    forwarded = ["--days", args.days]
    if args.apply:
        forwarded.append("--apply")
    return prune_main(forwarded)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else __import__("sys").argv[1:]
    if not argv:
        return _dashboard()

    command, *rest = argv
    dispatch: dict[str, Callable[[list[str]], int]] = {
        "help": _help_main,
        "menu": _menu_main,
        "init": _init_main,
        "registry": _registry_main,
        "today": lambda args: today_main(args),
        "capture": _capture_main,
        "review": _review_main,
        "promote": _promote_main,
        "note": _note_main,
        "context": _context_main,
        "snapshot": lambda args: memsnap_main(args),
        "related": _related_main,
        "index": _index_main,
        "prune": _prune_main,
        "find": lambda args: memfind_main(args),
        "open": lambda args: memopen_main(args),
    }
    if command not in dispatch:
        raise SystemExit(f"Unknown atlas command: {command}")
    return dispatch[command](rest)
