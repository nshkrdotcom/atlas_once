from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from fcntl import LOCK_EX, LOCK_NB, LOCK_UN, flock
from pathlib import Path

GENERATED_STATE_NAMES = {".dexter.db", ".dexterity", ".atlas-intelligence.lock"}
DIRECTORY_SYMLINK_NAMES = {".git"}


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
    if source.name in GENERATED_STATE_NAMES:
        return
    if source.is_dir() and not source.is_symlink() and source.name not in DIRECTORY_SYMLINK_NAMES:
        if target.is_symlink() or target.is_file():
            remove_path(target)
        target.mkdir(parents=True, exist_ok=True)
        source_entries = {
            entry.name: entry
            for entry in sorted(source.iterdir(), key=lambda item: item.name)
            if entry.name not in GENERATED_STATE_NAMES
        }
        for child in list(target.iterdir()):
            if child.name in GENERATED_STATE_NAMES:
                continue
            if child.name not in source_entries:
                remove_path(child)
        for name, child_source in source_entries.items():
            sync_shadow_entry(target / name, child_source)
        return

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
        if entry.name not in GENERATED_STATE_NAMES
    }

    for entry in list(target.iterdir()):
        if entry.name in GENERATED_STATE_NAMES:
            continue
        if entry.name not in source_entries:
            remove_path(entry)

    for name, source in source_entries.items():
        destination = target / name
        sync_shadow_entry(destination, source)

    return target


@contextmanager
def shadow_intelligence_lock(
    shadow_project_root: Path,
    *,
    timeout_seconds: float = 30.0,
) -> Iterator[Path]:
    shadow_project_root.mkdir(parents=True, exist_ok=True)
    lock_path = shadow_project_root / ".atlas-intelligence.lock"
    deadline = time.monotonic() + timeout_seconds
    with lock_path.open("w", encoding="utf-8") as handle:
        while True:
            try:
                flock(handle.fileno(), LOCK_EX | LOCK_NB)
                break
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for lock: {lock_path}") from exc
                time.sleep(0.05)
        try:
            yield lock_path
        finally:
            flock(handle.fileno(), LOCK_UN)
