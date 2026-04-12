from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .util import iter_markdown_files, read_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ctx",
        description="Concatenate Markdown files from a directory tree with file markers.",
    )
    parser.add_argument(
        "--pwd-only",
        action="store_true",
        help="Only include Markdown files directly inside the target directory.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Directory to read from. Defaults to the current working directory.",
    )
    return parser


def run(path: Path, pwd_only: bool) -> int:
    root = path.expanduser().resolve()
    if not root.exists():
        raise SystemExit(f"Path does not exist: {root}")
    if not root.is_dir():
        raise SystemExit(f"Path is not a directory: {root}")

    for item in iter_markdown_files(root, recursive=not pwd_only):
        rel = item.relative_to(root).as_posix()
        contents = read_text(item)
        sys.stdout.write(f"# FILE: ./{rel}\n")
        sys.stdout.write(contents)
        if not contents.endswith("\n"):
            sys.stdout.write("\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run(Path(args.path), args.pwd_only)
