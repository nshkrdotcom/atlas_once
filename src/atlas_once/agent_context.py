from __future__ import annotations

import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

SKIP_DIRS = {
    ".git",
    ".elixir_ls",
    "_build",
    "deps",
    "dist",
    "node_modules",
    "tmp",
}

MODULE_RE = re.compile(r"^\s*defmodule\s+([A-Za-z0-9_.]+)\s+do\b", re.MULTILINE)
APP_RE = re.compile(r"\bapp:\s*:([A-Za-z0-9_]+)")


def _layer_for(relative: str) -> str:
    if relative in {"", "."}:
        return "root"
    first = relative.split("/", 1)[0]
    if first in {"core", "bridges", "apps", "surfaces", "lib", "test", "config"}:
        return first
    return "other"


def _iter_source_files(project_root: Path) -> list[Path]:
    files: list[Path] = []
    for current_text, dirnames, filenames in os.walk(project_root):
        dirnames[:] = [name for name in sorted(dirnames) if name not in SKIP_DIRS]
        current = Path(current_text)
        for filename in sorted(filenames):
            if filename.endswith((".ex", ".exs")):
                files.append(current / filename)
    return files


def _read_small(path: Path, *, max_bytes: int = 200_000) -> str:
    try:
        if path.stat().st_size > max_bytes:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _mix_project_payload(project_root: Path, mix_path: Path) -> dict[str, Any]:
    relative_parent = mix_path.parent.relative_to(project_root).as_posix()
    if relative_parent == ".":
        relative_parent = ""
    text = _read_small(mix_path, max_bytes=80_000)
    app_match = APP_RE.search(text)
    return {
        "rel_path": relative_parent or ".",
        "mix_path": mix_path.relative_to(project_root).as_posix(),
        "layer": _layer_for(relative_parent),
        "app": app_match.group(1) if app_match else None,
    }


def scan_repo_structure(
    project_root: Path,
    *,
    module_limit: int = 30,
    file_limit: int = 20,
) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    mix_paths = [
        path
        for path in sorted(root.rglob("mix.exs"))
        if not any(part in SKIP_DIRS for part in path.relative_to(root).parts)
    ]
    mix_projects = [_mix_project_payload(root, path) for path in mix_paths]
    layer_counts = Counter(str(item["layer"]) for item in mix_projects)

    source_files = _iter_source_files(root)
    modules: list[dict[str, Any]] = []
    for path in source_files:
        relative = path.relative_to(root).as_posix()
        text = _read_small(path)
        for match in MODULE_RE.finditer(text):
            modules.append(
                {
                    "module": match.group(1),
                    "file": relative,
                    "layer": _layer_for(relative),
                }
            )
            if len(modules) >= module_limit:
                break
        if len(modules) >= module_limit:
            break

    preferred_files = [
        "mix.exs",
        "lib/citadel/workspace.ex",
        "lib/citadel/build/dependency_resolver.ex",
    ]
    key_files: list[str] = []
    seen: set[str] = set()
    for relative in preferred_files:
        if (root / relative).is_file() and relative not in seen:
            seen.add(relative)
            key_files.append(relative)
    for module in modules:
        file_path = str(module["file"])
        if file_path not in seen:
            seen.add(file_path)
            key_files.append(file_path)
        if len(key_files) >= file_limit:
            break

    return {
        "repo_root": str(root),
        "mix_project_count": len(mix_projects),
        "multi_mix": len(mix_projects) > 1,
        "mix_projects": mix_projects[:file_limit],
        "layer_counts": dict(sorted(layer_counts.items())),
        "module_count_sampled": len(modules),
        "modules": modules,
        "key_files": key_files,
    }
