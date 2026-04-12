from __future__ import annotations

import argparse

from .config import get_paths
from .util import resolve_day_path, resolve_recent_letter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docday",
        description=(
            "Resolve ~/jb/docs/YYYYMMDD by day number, "
            "or a recent ~/p/g/n directory by letter."
        ),
    )
    parser.add_argument(
        "selector",
        nargs="?",
        default="1",
        help="1..39 selects docs dates; a..z selects recent dirs.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    selector = args.selector.strip()
    paths = get_paths()
    if len(selector) == 1 and selector.isalpha():
        target = resolve_recent_letter(selector, paths)
    else:
        try:
            offset = int(selector)
        except ValueError as exc:
            raise SystemExit(
                "Day must be an integer between 1 and 39, or a letter from a to z."
            ) from exc
        if not 1 <= offset <= 39:
            raise SystemExit("Day must be between 1 and 39.")
        target = resolve_day_path(offset, paths)
    print(target, end="")
    return 0
