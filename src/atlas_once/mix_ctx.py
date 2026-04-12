from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

EXCLUDED_DIR_NAMES = {".git", "_build", "deps", "node_modules"}
GROUP_KEYS = ("all", "main", "ancillary", "core", "bridges", "apps", "current", "all-tests", "menu")


@dataclass(frozen=True)
class Project:
    rel_path: str
    abs_path: Path
    category: str
    ancillary: bool


@dataclass(frozen=True)
class GroupOption:
    key: str
    label: str
    include_tests: bool = False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mixctx",
        description="Dump mix.exs, README.md, and lib/* for Elixir repos and workspaces.",
    )
    parser.add_argument(
        "selectors",
        nargs="*",
        help="Optional group and/or path. Examples: 'current', '/repo/path', 'current /repo/path'.",
    )
    parser.add_argument(
        "--path",
        help=(
            "Use this directory instead of the current working directory "
            "when locating the repo root."
        ),
    )
    parser.add_argument("-o", "--output", help="Write the bundle to a file instead of stdout.")
    parser.add_argument(
        "--list-groups",
        action="store_true",
        help="Print available groups and exit.",
    )
    return parser


def find_repo_root(start: Path) -> Path:
    mix_candidates: list[Path] = []
    workspace_candidates: list[Path] = []
    current = start.resolve()

    while True:
        if (current / "mix.exs").is_file():
            mix_candidates.append(current)
            if has_workspace_layout(current):
                workspace_candidates.append(current)
        if current.parent == current:
            break
        current = current.parent

    if workspace_candidates:
        return workspace_candidates[-1]
    if mix_candidates:
        return mix_candidates[0]
    raise SystemExit("No Elixir repo root found from the current directory.")


def has_workspace_layout(root: Path) -> bool:
    for category in ("core", "bridges", "apps"):
        category_dir = root / category
        if not category_dir.is_dir():
            continue
        for child in sorted(category_dir.iterdir()):
            if should_skip_directory(child):
                continue
            if child.is_dir() and (child / "mix.exs").is_file():
                return True
    return False


def should_skip_directory(path: Path) -> bool:
    return (
        not path.is_dir()
        or path.is_symlink()
        or path.name.startswith(".")
        or path.name in EXCLUDED_DIR_NAMES
    )


def is_ancillary_project(rel_path: str) -> bool:
    if rel_path == ".":
        return False
    if rel_path.startswith("apps/"):
        return True
    name = rel_path.split("/")[-1]
    return "conformance" in name or name.endswith("_example") or name.endswith("-example")


def discover_projects(root: Path) -> list[Project]:
    project_dirs: dict[str, Path] = {".": root}
    for child in sorted(root.iterdir()):
        if should_skip_directory(child):
            continue
        if (child / "mix.exs").is_file():
            project_dirs[child.relative_to(root).as_posix()] = child
            continue
        for grandchild in sorted(child.iterdir()):
            if should_skip_directory(grandchild):
                continue
            if grandchild.is_dir() and (grandchild / "mix.exs").is_file():
                project_dirs[grandchild.relative_to(root).as_posix()] = grandchild

    projects = [
        Project(
            rel_path=rel_path,
            abs_path=abs_path,
            category="root" if rel_path == "." else rel_path.split("/", 1)[0],
            ancillary=is_ancillary_project(rel_path),
        )
        for rel_path, abs_path in sorted(project_dirs.items(), key=lambda item: item[0])
    ]
    root_project = next((project for project in projects if project.rel_path == "."), None)
    if root_project is None:
        raise SystemExit(f"Repo root {root} is missing a root mix.exs.")
    return [root_project, *[project for project in projects if project.rel_path != "."]]


def find_current_project(projects: list[Project], cwd: Path) -> Project | None:
    resolved = cwd.resolve()
    for project in sorted(projects, key=lambda item: len(item.rel_path), reverse=True):
        if resolved == project.abs_path or project.abs_path in resolved.parents:
            return project
    return None


def build_group_options(
    projects: list[Project], current_project: Project | None
) -> list[GroupOption]:
    options = [
        GroupOption("all", "All relevant files (default)"),
        GroupOption("main", "Main packages (root + non-ancillary packages)"),
        GroupOption("ancillary", "Ancillary/proof packages (root + apps/conformance)"),
    ]
    if any(project.category == "core" for project in projects):
        options.append(GroupOption("core", "Core packages"))
    if any(project.category == "bridges" for project in projects):
        options.append(GroupOption("bridges", "Bridge packages"))
    if any(project.category == "apps" for project in projects):
        options.append(GroupOption("apps", "App packages"))
    if current_project is not None:
        options.append(GroupOption("current", f"Current package ({current_project.rel_path})"))
    options.append(GroupOption("all-tests", "All relevant files plus test/*", include_tests=True))
    return options


def choose_group_interactively(options: list[GroupOption]) -> GroupOption:
    print("Select a mixctx view:", file=sys.stderr)
    for index, option in enumerate(options, start=1):
        default_marker = " (default)" if index == 1 else ""
        print(f"{index}. {option.label}{default_marker}", file=sys.stderr)
    while True:
        choice = input("\nSelection [1]: ").strip()
        if not choice:
            return options[0]
        if choice.lower() in {"q", "quit", "exit"}:
            raise SystemExit(0)
        try:
            index = int(choice)
        except ValueError:
            print("Enter a menu number, or q to quit.", file=sys.stderr)
            continue
        if 1 <= index <= len(options):
            return options[index - 1]
        print(f"Enter a number between 1 and {len(options)}.", file=sys.stderr)


def resolve_group(requested_group: str | None, options: list[GroupOption]) -> GroupOption:
    if requested_group is None:
        if sys.stdin.isatty() and sys.stdout.isatty():
            return choose_group_interactively(options)
        return options[0]
    if requested_group == "menu":
        return choose_group_interactively(options)
    for option in options:
        if option.key == requested_group:
            return option
    valid = ", ".join(option.key for option in options)
    raise SystemExit(f"Unknown group '{requested_group}'. Valid groups: {valid}")


def select_projects(
    group_key: str,
    projects: list[Project],
    current_project: Project | None,
) -> tuple[list[Project], bool]:
    root_project = projects[0]
    if group_key == "all":
        return projects, False
    if group_key == "all-tests":
        return projects, True
    if group_key == "main":
        return [
            root_project,
            *[project for project in projects[1:] if not project.ancillary],
        ], False
    if group_key == "ancillary":
        return [root_project, *[project for project in projects[1:] if project.ancillary]], False
    if group_key == "core":
        return [
            root_project,
            *[project for project in projects[1:] if project.category == "core"],
        ], False
    if group_key == "bridges":
        return [
            root_project,
            *[project for project in projects[1:] if project.category == "bridges"],
        ], False
    if group_key == "apps":
        return [
            root_project,
            *[project for project in projects[1:] if project.category == "apps"],
        ], False
    if group_key == "current":
        if current_project is None or current_project.rel_path == ".":
            return [root_project], False
        return [root_project, current_project], False
    raise SystemExit(f"Unknown group: {group_key}")


def iter_regular_files(root: Path) -> list[Path]:
    if not root.is_dir() or root.name in EXCLUDED_DIR_NAMES:
        return []
    files: list[Path] = []
    for child in sorted(root.iterdir()):
        if child.is_symlink():
            continue
        if child.is_dir():
            if child.name in EXCLUDED_DIR_NAMES:
                continue
            files.extend(iter_regular_files(child))
            continue
        if child.is_file():
            files.append(child)
    return files


def collect_project_files(project: Project, include_tests: bool) -> list[Path]:
    files: list[Path] = []
    for basename in ("mix.exs", "README.md"):
        candidate = project.abs_path / basename
        if candidate.is_file():
            files.append(candidate)
    files.extend(iter_regular_files(project.abs_path / "lib"))
    if include_tests:
        files.extend(iter_regular_files(project.abs_path / "test"))
    return files


def ends_with_newline(path: Path) -> bool:
    with path.open("rb") as handle:
        if path.stat().st_size == 0:
            return True
        handle.seek(-1, 2)
        return handle.read(1) == b"\n"


def emit_bundle(
    root: Path,
    projects: list[Project],
    include_tests: bool,
    out_stream: TextIO,
) -> None:
    seen: set[str] = set()
    for project in projects:
        for path in collect_project_files(project, include_tests):
            rel_path = path.relative_to(root).as_posix()
            if rel_path in seen:
                continue
            seen.add(rel_path)
            out_stream.write(f"===== {rel_path} =====\n")
            out_stream.write(path.read_text(encoding="utf-8", errors="replace"))
            if not ends_with_newline(path):
                out_stream.write("\n")
            out_stream.write("\n")


def resolve_target_path_and_group(args: argparse.Namespace) -> tuple[Path, str | None]:
    requested_group: str | None = None
    requested_path: str | None = args.path
    if len(args.selectors) > 2:
        raise SystemExit("Expected at most one group and one path.")
    for selector in args.selectors:
        if selector in GROUP_KEYS:
            if requested_group is not None:
                raise SystemExit("Specify at most one group.")
            requested_group = selector
            continue
        if requested_path is not None:
            raise SystemExit("Specify at most one path. Use --path or a single positional path.")
        requested_path = selector
    if requested_path is None:
        return Path.cwd(), requested_group
    target_path = Path(requested_path).expanduser().resolve()
    if not target_path.exists():
        raise SystemExit(f"Path does not exist: {target_path}")
    if target_path.is_file():
        target_path = target_path.parent
    return target_path, requested_group


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cwd, requested_group = resolve_target_path_and_group(args)
    repo_root = find_repo_root(cwd)
    projects = discover_projects(repo_root)
    current_project = find_current_project(projects, cwd)
    options = build_group_options(projects, current_project)

    if args.list_groups:
        for option in options:
            print(option.key)
        return 0

    selected_option = resolve_group(requested_group, options)
    selected_projects, include_tests = select_projects(
        selected_option.key,
        projects,
        current_project,
    )

    if args.output:
        output_path = Path(args.output).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as handle:
            emit_bundle(repo_root, selected_projects, include_tests, handle)
        print(f"Wrote {selected_option.key} bundle to {output_path}", file=sys.stderr)
        return 0

    emit_bundle(repo_root, selected_projects, include_tests, sys.stdout)
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(argv)
