from __future__ import annotations

import argparse

from .markdown_ctx import main as ctx_main
from .memory import (
    index_rebuild_main,
    memadd_main,
    memfind_main,
    memopen_main,
    memsnap_main,
    prune_main,
    related_main,
    session_close_main,
    today_main,
)
from .mix_ctx import main as mixctx_main
from .multi_ctx import main as mcc_main
from .nav import main as docday_main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atlas-once",
        description="Atlas Once personal memory system.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in (
        "ctx",
        "mixctx",
        "mcc",
        "docday",
        "today",
        "memadd",
        "memfind",
        "memopen",
        "memsnap",
        "session-close",
        "atlas-index",
        "atlas-related",
        "atlas-prune",
    ):
        subparsers.add_parser(name)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args, extras = parser.parse_known_args(argv)
    command = args.command
    dispatch = {
        "ctx": ctx_main,
        "mixctx": mixctx_main,
        "mcc": mcc_main,
        "docday": docday_main,
        "today": today_main,
        "memadd": memadd_main,
        "memfind": memfind_main,
        "memopen": memopen_main,
        "memsnap": memsnap_main,
        "session-close": session_close_main,
        "atlas-index": index_rebuild_main,
        "atlas-related": related_main,
        "atlas-prune": prune_main,
    }
    return dispatch[command](extras)
