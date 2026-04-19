from __future__ import annotations

import hashlib
import re
from pathlib import Path


def _safe_name(path_name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", path_name).strip("._-") or "project"
    return value


def shadow_root_for_project(project_root: Path, shadow_root: Path) -> Path:
    shadow_root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(str(project_root.resolve()).encode("utf-8")).hexdigest()[:12]
    safe_name = _safe_name(project_root.name)
    return shadow_root / f"{safe_name}-{digest}"


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    for child in sorted(path.iterdir(), reverse=True):
        remove_path(child)
    path.rmdir()


def sync_shadow_entry(target: Path, source: Path) -> None:
    if target.is_symlink():
        try:
            if target.resolve() == source.resolve():
                return
        except FileNotFoundError:
            pass
    if target.exists() or target.is_symlink():
        remove_path(target)
    target.symlink_to(source, target_is_directory=source.is_dir())


def ensure_shadow_project_root(project_root: Path, shadow_root: Path) -> Path:
    target = shadow_root_for_project(project_root, shadow_root)
    target.mkdir(parents=True, exist_ok=True)

    source_entries = {
        entry.name: entry
        for entry in sorted(project_root.iterdir(), key=lambda item: item.name)
        if entry.name not in {".dexter.db", ".dexterity"}
    }

    for entry in list(target.iterdir()):
        if entry.name in {".dexter.db", ".dexterity"}:
            continue
        if entry.name not in source_entries:
            remove_path(entry)

    for name, source in source_entries.items():
        destination = target / name
        sync_shadow_entry(destination, source)

    return target
