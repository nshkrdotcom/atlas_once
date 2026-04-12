from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import AtlasPaths, ensure_state

MARKDOWN_SUFFIXES = {".md", ".markdown", ".mdown", ".mkd", ".mkdn", ".mdx"}
PROJECT_LINE = re.compile(r"^Project:\s*(.+?)\s*$", re.MULTILINE)
TAGS_LINE = re.compile(r"^Tags:\s*(.+?)\s*$", re.MULTILINE)
ALIASES_LINE = re.compile(r"^Aliases:\s*(.+?)\s*$", re.MULTILINE)
REPO_LINE = re.compile(r"^Repos?:\s*(.+?)\s*$", re.MULTILINE)
PATH_MENTION = re.compile(r"(?:^|[\s(])((?:/|~)[^\s)]+)")


@dataclass(frozen=True)
class SearchMatch:
    path: Path
    line_number: int
    line: str


@dataclass(frozen=True)
class NoteMetadata:
    project: str | None
    tags: list[str]
    aliases: list[str]
    repos: list[str]
    links: list[str]


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def ensure_memory_dirs(paths: AtlasPaths) -> None:
    ensure_state(paths)


def atomic_json_write(path: Path, payload: object) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_json(path: Path, default: object) -> object:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "note"


def now_local() -> datetime:
    return datetime.now().astimezone()


def resolve_day_path(offset: int, paths: AtlasPaths) -> Path:
    target_date = date.today() - timedelta(days=offset - 1)
    target = paths.docs_root / target_date.strftime("%Y%m%d")
    target.mkdir(parents=True, exist_ok=True)
    return target


def resolve_recent_letter(letter: str, paths: AtlasPaths) -> Path:
    if len(letter) != 1 or not letter.isalpha():
        raise SystemExit("Letter selector must be a single letter from a to z.")

    index = ord(letter.lower()) - ord("a")

    if not 0 <= index <= 25:
        raise SystemExit("Letter selector must be between a and z.")

    if paths.code_root is None:
        raise SystemExit("No code root configured. Use atlas config set code_root <path>.")

    if not paths.code_root.is_dir():
        raise SystemExit(f"Recent-dir root does not exist: {paths.code_root}")

    candidates = sorted(
        (
            item
            for item in paths.code_root.iterdir()
            if item.is_dir() and not item.name.startswith(".")
        ),
        key=lambda item: (item.stat().st_mtime, item.name.lower()),
        reverse=True,
    )

    if index >= len(candidates):
        raise SystemExit(f"Only found {len(candidates)} directories in {paths.code_root}.")

    return candidates[index]


def iter_markdown_files(root: Path, recursive: bool) -> list[Path]:
    if recursive:
        candidates = [path for path in root.rglob("*") if path.is_file()]
    else:
        candidates = [path for path in root.iterdir() if path.is_file()]

    return sorted(
        (path for path in candidates if path.suffix.lower() in MARKDOWN_SUFFIXES),
        key=lambda item: item.relative_to(root).as_posix().lower(),
    )


def open_in_editor(path: Path) -> int:
    editor = os.environ.get("EDITOR")
    if not editor:
        print(path)
        return 0
    result = subprocess.run([editor, str(path)], check=False)
    return result.returncode


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def search_text(root_paths: Iterable[Path], query: str) -> list[SearchMatch]:
    query_lc = query.lower()
    matches: list[SearchMatch] = []
    for root in root_paths:
        if not root.exists():
            continue
        for path in sorted(path for path in root.rglob("*") if path.is_file()):
            if path.suffix.lower() not in MARKDOWN_SUFFIXES and path.suffix.lower() not in {
                ".ctx",
                ".txt",
                ".json",
            }:
                continue
            for line_number, line in enumerate(read_text(path).splitlines(), start=1):
                if query_lc in line.lower():
                    matches.append(SearchMatch(path=path, line_number=line_number, line=line))
    return matches


def collect_note_files(roots: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        files.extend(path for path in root.rglob("*") if path.is_file())
    return sorted(files, key=lambda item: (item.stat().st_mtime, item.as_posix()), reverse=True)


def parse_metadata(content: str) -> NoteMetadata:
    project = None
    tags: list[str] = []
    aliases: list[str] = []
    repos: list[str] = []

    project_match = PROJECT_LINE.search(content)
    if project_match:
        project = project_match.group(1).strip()

    tags_match = TAGS_LINE.search(content)
    if tags_match:
        tags = [item.strip() for item in tags_match.group(1).split(",") if item.strip()]

    aliases_match = ALIASES_LINE.search(content)
    if aliases_match:
        aliases = [item.strip() for item in aliases_match.group(1).split(",") if item.strip()]

    repos_match = REPO_LINE.search(content)
    if repos_match:
        repos = [item.strip() for item in repos_match.group(1).split(",") if item.strip()]

    return NoteMetadata(
        project=project,
        tags=tags,
        aliases=aliases,
        repos=repos,
        links=[match.group(1) for match in PATH_MENTION.finditer(content)],
    )


def print_search_matches(matches: list[SearchMatch]) -> None:
    for match in matches:
        print(f"{match.path}:{match.line_number}:{match.line}")
