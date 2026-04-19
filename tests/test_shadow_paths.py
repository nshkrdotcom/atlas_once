from __future__ import annotations

import multiprocessing as mp
import time
from pathlib import Path
from typing import Any

from atlas_once.shadow_workspace import (
    ensure_shadow_project_root,
    shadow_intelligence_lock,
    shadow_root_for_project,
)


def _hold_shadow_lock(shadow_root: str, ready: Any, release: Any) -> None:
    with shadow_intelligence_lock(Path(shadow_root), timeout_seconds=5.0):
        ready.set()
        release.wait(5.0)


def test_shared_shadow_rules(atlas_env: Path) -> None:
    project = atlas_env / "code" / "demo-app"
    shadow_root = atlas_env / "state" / "code" / "shadows"
    project.mkdir(parents=True)
    (project / "lib").mkdir()
    (project / "mix.exs").write_text("defmodule Demo.MixProject do\nend\n", encoding="utf-8")
    (project / "lib" / "demo.ex").write_text("defmodule Demo do\nend\n", encoding="utf-8")
    (project / ".dexter.db").write_text("source db must not mirror\n", encoding="utf-8")
    (project / ".dexterity").mkdir()
    (project / ".dexterity" / "dexterity.db").write_text("source store\n", encoding="utf-8")

    expected = shadow_root_for_project(project, shadow_root)
    actual = ensure_shadow_project_root(project, shadow_root)

    assert actual == expected
    assert (actual / "mix.exs").is_symlink()
    assert (actual / "lib").is_dir()
    assert not (actual / "lib").is_symlink()
    assert (actual / "lib" / "demo.ex").is_symlink()
    assert not (actual / ".dexter.db").exists()
    assert not (actual / ".dexterity").exists()
    assert not (actual / ".atlas-intelligence.lock").exists()


def test_shadow_keeps_source_symlink_directories_as_symlinks(atlas_env: Path) -> None:
    project = atlas_env / "code" / "demo-app"
    shared = atlas_env / "shared-lib"
    shadow_root = atlas_env / "state" / "code" / "shadows"
    shared.mkdir(parents=True)
    (shared / "linked.ex").write_text("defmodule Linked do\nend\n", encoding="utf-8")
    project.mkdir(parents=True)
    (project / "mix.exs").write_text("defmodule Demo.MixProject do\nend\n", encoding="utf-8")
    (project / "linked").symlink_to(shared, target_is_directory=True)

    actual = ensure_shadow_project_root(project, shadow_root)

    assert (actual / "linked").is_symlink()
    assert (actual / "linked").resolve() == shared.resolve()


def test_shadow_intelligence_lock_serializes_processes(atlas_env: Path) -> None:
    context = mp.get_context("fork")
    shadow = atlas_env / "state" / "code" / "shadows" / "demo"
    ready_one = context.Event()
    release_one = context.Event()
    ready_two = context.Event()
    release_two = context.Event()

    process_one = context.Process(
        target=_hold_shadow_lock,
        args=(str(shadow), ready_one, release_one),
    )
    process_two = context.Process(
        target=_hold_shadow_lock,
        args=(str(shadow), ready_two, release_two),
    )

    process_one.start()
    assert ready_one.wait(2.0)
    process_two.start()
    time.sleep(0.2)
    assert not ready_two.is_set()

    release_one.set()
    assert ready_two.wait(2.0)
    release_two.set()

    process_one.join(2.0)
    process_two.join(2.0)
    assert process_one.exitcode == 0
    assert process_two.exitcode == 0
