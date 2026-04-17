from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent

from .config import get_paths
from .mix_ctx import GROUP_KEYS, collect_mix_bundle
from .registry import resolve_project_ref
from .util import atomic_json_write, ensure_memory_dirs, load_json


@dataclass(frozen=True)
class Preset:
    id: int
    paths: list[str]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Combine repo context across explicit paths or saved stack presets."
    )
    parser.add_argument(
        "--group",
        choices=GROUP_KEYS,
        help="Pass a repo-context group to each target.",
    )
    parser.add_argument(
        "--remember",
        action="store_true",
        help="Save the provided paths as a numbered preset.",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Write the combined output to a file instead of stdout.",
    )
    parser.add_argument(
        "items",
        nargs="*",
        help="Preset ids, explicit paths, or the commands 'list' and 'delete'.",
    )
    return parser


def load_presets() -> list[Preset]:
    paths = get_paths()
    ensure_memory_dirs(paths)
    payload = load_json(paths.stack_preset_path, default=[])
    if not isinstance(payload, Sequence):
        raise SystemExit(f"Invalid preset file format: {paths.stack_preset_path}")
    return sorted(
        (Preset(id=int(item["id"]), paths=list(item["paths"])) for item in payload),
        key=lambda preset: preset.id,
    )


def save_presets(presets: list[Preset]) -> None:
    paths = get_paths()
    ensure_memory_dirs(paths)
    atomic_json_write(
        paths.stack_preset_path,
        [{"id": preset.id, "paths": preset.paths} for preset in presets],
    )


def resolve_input_path(raw_path: str) -> str:
    paths = get_paths()
    path = Path(raw_path).expanduser().resolve()
    if not path.exists():
        return resolve_project_ref(paths, raw_path).path
    if path.is_file():
        path = path.parent
    return str(path)


def parse_preset_id(token: str) -> int:
    try:
        preset_id = int(token)
    except ValueError as exc:
        raise SystemExit(f"Invalid preset id: {token}") from exc
    if preset_id <= 0:
        raise SystemExit(f"Invalid preset id: {token}")
    return preset_id


def find_preset(presets: list[Preset], preset_id: int) -> Preset:
    for preset in presets:
        if preset.id == preset_id:
            return preset
    raise SystemExit(f"Unknown preset id: {preset_id}")


def describe_preset(preset: Preset) -> str:
    return ", ".join(Path(path).name or path for path in preset.paths)


def format_preset_list(presets: list[Preset]) -> str:
    if not presets:
        return "No presets saved."
    lines: list[str] = []
    for preset in presets:
        lines.append(f"{preset.id:>2}. {describe_preset(preset)}")
        for path in preset.paths:
            lines.append(f"    {path}")
    return "\n".join(lines)


def print_dashboard() -> int:
    message = dedent(
        """\
        stack: multi-repo context preset runner

        Commands:
          stack list
            Show saved presets.
          stack 1
            Run preset 1.
          stack 1 3 5
            Run several presets in order.
          stack /foo /bar
            Run explicit paths.
          stack --group current 1 3
            Run a shared repo-context group across each target.
          stack --remember /foo /bar
            Save paths as the next preset number.
          stack delete 4
            Delete preset 4.
          stack -o /tmp/out.ctx 1 2
            Write combined output to a file.

        Presets:
        """
    )
    print(message + format_preset_list(load_presets()))
    return 0


def cmd_list() -> int:
    print(format_preset_list(load_presets()))
    return 0


def cmd_remember(paths_in: list[str]) -> int:
    if not paths_in:
        raise SystemExit("Usage: stack --remember <path> [path ...]")
    presets = load_presets()
    preset_id = max((preset.id for preset in presets), default=0) + 1
    presets.append(Preset(id=preset_id, paths=[resolve_input_path(path) for path in paths_in]))
    save_presets(presets)
    print(preset_id)
    return 0


def cmd_delete(ids: list[str]) -> int:
    if not ids:
        raise SystemExit("Usage: stack delete <id> [id ...]")
    preset_ids = {parse_preset_id(item) for item in ids}
    presets = load_presets()
    remaining = [preset for preset in presets if preset.id not in preset_ids]
    if len(remaining) == len(presets):
        missing = ", ".join(str(item) for item in sorted(preset_ids))
        raise SystemExit(f"No matching preset ids found: {missing}")
    save_presets(remaining)
    return 0


def resolve_targets(items: list[str], presets: list[Preset]) -> list[str]:
    if not items:
        raise SystemExit("Usage: stack <preset-id|path> [preset-id|path ...]")
    targets: list[str] = []
    for item in items:
        if item.isdigit():
            targets.extend(find_preset(presets, parse_preset_id(item)).paths)
        else:
            targets.append(resolve_input_path(item))
    return targets


def render_targets(targets: list[str], group: str | None) -> str:
    chunks: list[str] = []
    show_header = len(targets) > 1
    for target in targets:
        bundle = collect_mix_bundle(Path(target), requested_group=group)
        if show_header:
            chunks.append(f"===== stack {target} =====\n")
        chunks.append(bundle.text)
        if bundle.text and not bundle.text.endswith("\n"):
            chunks.append("\n")
    return "".join(chunks)


def main(argv: list[str] | None = None) -> int:
    if argv is not None and not argv:
        return print_dashboard()
    if argv is None and len(__import__("sys").argv) == 1:
        return print_dashboard()

    args = build_parser().parse_args(argv)
    if args.remember:
        return cmd_remember(args.items)
    if args.items and args.items[0] == "list":
        if len(args.items) != 1:
            raise SystemExit("Usage: stack list")
        return cmd_list()
    if args.items and args.items[0] == "delete":
        return cmd_delete(args.items[1:])

    payload = render_targets(resolve_targets(args.items, load_presets()), args.group)
    if args.output:
        output_path = Path(args.output).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload, encoding="utf-8")
        return 0
    print(payload, end="")
    return 0
