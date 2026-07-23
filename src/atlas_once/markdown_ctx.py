from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from .util import iter_markdown_files, read_text
from .xml_pack import PackFile, render_pack


@dataclass(frozen=True)
class MarkdownBundle:
    root: Path
    files: list[Path]
    text: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atlas context notes",
        description="Concatenate Markdown files from a directory tree into an XML pack.",
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


def collect_markdown_bundle(path: Path, pwd_only: bool) -> MarkdownBundle:
    root = path.expanduser().resolve()
    if not root.exists():
        raise SystemExit(f"Path does not exist: {root}")
    if not root.is_dir():
        raise SystemExit(f"Path is not a directory: {root}")

    files = iter_markdown_files(root, recursive=not pwd_only)
    pack_files = [
        PackFile(path=item.relative_to(root).as_posix(), content=read_text(item))
        for item in files
    ]
    text = render_pack(pack_files, kind="notes", meta={"root": root.name})
    return MarkdownBundle(root=root, files=files, text=text)


def run(path: Path, pwd_only: bool) -> int:
    bundle = collect_markdown_bundle(path, pwd_only=pwd_only)
    sys.stdout.write(bundle.text)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run(Path(args.path), args.pwd_only)
