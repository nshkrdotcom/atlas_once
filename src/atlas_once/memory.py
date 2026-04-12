from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from .config import AtlasPaths, ensure_state, get_paths
from .inbox import create_entry
from .notes import build_graph, create_note, sync_note_graph
from .registry import resolve_or_placeholder, scan_registry
from .templates import daily_note_template
from .util import (
    atomic_json_write,
    collect_note_files,
    command_exists,
    now_local,
    open_in_editor,
    print_search_matches,
    search_text,
    slugify,
)


def today_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="today",
        description="Create or open today's daily note.",
    )
    parser.add_argument(
        "--print",
        action="store_true",
        dest="print_only",
        help="Print the created note path instead of opening.",
    )
    args = parser.parse_args(argv)
    paths = get_paths()
    ensure_state(paths)
    day = now_local().strftime("%Y%m%d")
    day_dir = paths.docs_root / day
    day_dir.mkdir(parents=True, exist_ok=True)
    note_path = day_dir / "index.md"
    if not note_path.exists():
        note_path.write_text(daily_note_template(day), encoding="utf-8")
    if args.print_only:
        print(note_path)
        return 0
    return open_in_editor(note_path)


def memadd_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="memadd",
        description="Append a quick note to today's inbox.",
    )
    parser.add_argument("--project", help="Optional project name for the note.")
    parser.add_argument(
        "--kind",
        default="note",
        choices=("note", "decision", "project", "topic", "person"),
        help="Structured inbox kind.",
    )
    parser.add_argument("--tag", action="append", default=[], help="Optional tag to prepend.")
    parser.add_argument("text", nargs="*", help="Note text. If omitted, stdin is used.")
    args = parser.parse_args(argv)
    paths = get_paths()
    ensure_state(paths)

    text = " ".join(args.text).strip()
    if not text:
        text = __import__("sys").stdin.read().strip()
    if not text:
        raise SystemExit("memadd needs note text via args or stdin.")
    project = resolve_or_placeholder(paths, args.project) if args.project else None
    entry = create_entry(paths, text, project=project, tags=args.tag, kind=args.kind)
    print(entry.source_path)
    return 0


def _roots_from_args(paths: AtlasPaths, args: argparse.Namespace) -> list[Path]:
    selected: list[Path] = []
    if args.docs:
        selected.append(paths.docs_root)
    if args.inbox:
        selected.append(paths.inbox_root)
    if args.sessions:
        selected.append(paths.sessions_root)
    if args.projects:
        selected.append(paths.projects_root)
    if args.decisions:
        selected.append(paths.decisions_root)
    if args.people:
        selected.append(paths.people_root)
    if args.topics:
        selected.append(paths.topics_root)
    if args.snapshots:
        selected.append(paths.snapshots_root)
    if not selected:
        selected.extend([paths.docs_root, paths.mem_root])
    return selected


def memfind_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="memfind",
        description="Search Atlas Once notes and memory.",
    )
    for name in (
        "docs",
        "inbox",
        "sessions",
        "projects",
        "decisions",
        "people",
        "topics",
        "snapshots",
    ):
        parser.add_argument(f"--{name}", action="store_true")
    parser.add_argument("--paths-only", action="store_true", help="Print matching file paths only.")
    parser.add_argument("query", nargs="+", help="Case-insensitive text query.")
    args = parser.parse_args(argv)
    paths = get_paths()
    ensure_state(paths)
    matches = search_text(_roots_from_args(paths, args), " ".join(args.query))
    if args.paths_only:
        seen: set[Path] = set()
        for match in matches:
            if match.path not in seen:
                print(match.path)
                seen.add(match.path)
        return 0
    print_search_matches(matches)
    return 0


def memopen_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="memopen", description="Pick and open a memory file.")
    parser.add_argument(
        "--print",
        action="store_true",
        dest="print_only",
        help="Print the chosen path instead of opening.",
    )
    parser.add_argument("query", nargs="*", help="Optional path/name prefilter.")
    args = parser.parse_args(argv)
    paths = get_paths()
    ensure_state(paths)
    candidates = collect_note_files([paths.docs_root, paths.mem_root])
    if args.query:
        needle = " ".join(args.query).lower()
        candidates = [path for path in candidates if needle in path.as_posix().lower()]
    if not candidates:
        raise SystemExit("No matching notes found.")
    selected = None
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
    if selected is None:
        selected = candidates[0]
    if args.print_only:
        print(selected)
        return 0
    return open_in_editor(selected)


def memsnap_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="memsnap",
        description="Save command output as a dated snapshot.",
    )
    parser.add_argument("name", help="Snapshot name.")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="Command to run after '--'.")
    args = parser.parse_args(argv)
    paths = get_paths()
    ensure_state(paths)
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise SystemExit("Usage: memsnap <name> -- <command ...>")
    now = now_local()
    day_dir = paths.snapshots_root / now.strftime("%Y%m%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    stem = slugify(args.name)
    snapshot_path = day_dir / f"{stem}.ctx"
    meta_path = day_dir / f"{stem}.json"
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    snapshot_path.write_text(result.stdout, encoding="utf-8")
    atomic_json_write(
        meta_path,
        {
            "name": args.name,
            "command": command,
            "cwd": os.getcwd(),
            "timestamp": now.isoformat(),
            "returncode": result.returncode,
            "stderr": result.stderr,
            "snapshot": str(snapshot_path),
        },
    )
    print(snapshot_path)
    return result.returncode


def session_close_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="session-close",
        description="Write an end-of-session note.",
    )
    parser.add_argument("--project", default="", help="Optional project name.")
    parser.add_argument("slug", nargs="?", default="session", help="Slug for the session note.")
    parser.add_argument("--print", action="store_true", dest="print_only")
    args = parser.parse_args(argv)
    paths = get_paths()
    ensure_state(paths)
    project = resolve_or_placeholder(paths, args.project) if args.project else None
    session_path = create_note(
        paths,
        title=args.slug,
        kind="session",
        project=project,
        tags=[],
    )
    if args.print_only:
        print(session_path)
        return 0
    return open_in_editor(session_path)


def index_rebuild_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas-index", description="Rebuild Atlas Once indexes.")
    parser.parse_args(argv)
    paths = get_paths()
    ensure_state(paths)
    scan_registry(paths)
    sync_note_graph(paths)
    print(paths.indexes_root)
    return 0


def related_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas-related", description="Suggest related notes.")
    parser.add_argument("path", help="Path to a note.")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args(argv)
    target = Path(args.path).expanduser().resolve()
    if not target.is_file():
        raise SystemExit(f"Path is not a file: {target}")
    paths = get_paths()
    ensure_state(paths)
    _, _, related = build_graph(paths)
    items = related.get(target, [])
    for index, candidate in enumerate(items[: args.limit], start=1):
        print(f"{index}\t{candidate}")
    return 0


def prune_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="atlas-prune",
        description="Prune old snapshot files.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=45,
        help="Delete snapshots older than this many days.",
    )
    parser.add_argument("--apply", action="store_true", help="Actually delete files.")
    args = parser.parse_args(argv)
    paths = get_paths()
    ensure_state(paths)
    cutoff = now_local().timestamp() - args.days * 86400
    doomed = [
        path
        for path in paths.snapshots_root.rglob("*")
        if path.is_file() and path.stat().st_mtime < cutoff
    ]
    for path in doomed:
        print(path)
        if args.apply:
            path.unlink()
    return 0
