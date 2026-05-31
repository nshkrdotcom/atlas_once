"""Phase 8 — background ranked-context warming scaffold."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from atlas_once.config import AtlasPaths, get_paths
from atlas_once.ranked_context_warmer import (
    configured_group_names,
    load_dirty_queue,
    mark_dirty,
    seed_configured_groups,
    status_section,
    tick,
)
from atlas_once.ranked_snapshot import (
    LatestPointer,
    load_latest_pointer,
    write_latest_pointer,
)


def test_mark_dirty_dedupes_by_scope(atlas_env: Path) -> None:
    paths = get_paths()
    mark_dirty(paths, "gn-ten", reason="source-change")
    mark_dirty(paths, "gn-ten", reason="another-change")
    mark_dirty(paths, "other")
    queue = load_dirty_queue(paths)
    ids = sorted(s.scope_id for s in queue.scopes)
    assert ids == ["gn-ten", "other"]


def test_tick_runs_prepare_per_scope_and_advances_pointer(
    atlas_env: Path,
) -> None:
    paths = get_paths()
    mark_dirty(paths, "alpha")
    mark_dirty(paths, "beta")

    calls: list[str] = []

    def fake_prepare(paths_arg: AtlasPaths, scope_id: str) -> None:
        calls.append(scope_id)
        # Simulate the prepare step writing a snapshot+pointer.
        write_latest_pointer(
            paths_arg,
            LatestPointer(
                scope_kind="group",
                scope_id=scope_id,
                status="fresh",
                latest_complete_snapshot_key=f"key-{scope_id}",
                latest_fresh_snapshot_key=f"key-{scope_id}",
                updated_at="2026-05-30T00:00:00+00:00",
                last_success_at="2026-05-30T00:00:00+00:00",
            ),
        )

    result = tick(paths, prepare=fake_prepare)
    assert sorted(calls) == ["alpha", "beta"]
    statuses = sorted(p["status"] for p in result["processed"])
    assert statuses == ["rebuilt", "rebuilt"]
    pointer_alpha = load_latest_pointer(paths, "group", "alpha")
    assert pointer_alpha is not None
    assert pointer_alpha.status == "fresh"
    assert pointer_alpha.latest_complete_snapshot_key == "key-alpha"
    # Queue is drained.
    assert load_dirty_queue(paths).scopes == []


def test_failed_rebuild_preserves_previous_complete_pointer(
    atlas_env: Path,
) -> None:
    paths = get_paths()
    # Seed an already-complete pointer.
    write_latest_pointer(
        paths,
        LatestPointer(
            scope_kind="group",
            scope_id="alpha",
            status="fresh",
            latest_complete_snapshot_key="prior-key",
            latest_fresh_snapshot_key="prior-key",
            updated_at="2026-05-30T00:00:00+00:00",
            last_success_at="2026-05-30T00:00:00+00:00",
        ),
    )
    mark_dirty(paths, "alpha")

    def failing_prepare(paths_arg: AtlasPaths, scope_id: str) -> None:
        raise RuntimeError("dexterity timed out")

    result = tick(paths, prepare=failing_prepare)
    assert result["processed"][0]["status"] == "failed"

    pointer = load_latest_pointer(paths, "group", "alpha")
    assert pointer is not None
    # ↓ Absolute safety constraint: previous complete key preserved.
    assert pointer.latest_complete_snapshot_key == "prior-key"
    assert pointer.status == "error"
    assert pointer.last_error is not None and "dexterity timed out" in pointer.last_error


def test_system_exit_rebuild_failure_is_recorded_not_raised(atlas_env: Path) -> None:
    paths = get_paths()
    mark_dirty(paths, "alpha")

    def failing_prepare(paths_arg: AtlasPaths, scope_id: str) -> None:
        del paths_arg, scope_id
        raise SystemExit("ambiguous ranked repo ref")

    result = tick(paths, prepare=failing_prepare)

    assert result["processed"][0]["status"] == "failed"
    assert "ambiguous ranked repo ref" in str(result["processed"][0]["error"])
    pointer = load_latest_pointer(paths, "group", "alpha")
    assert pointer is not None
    assert pointer.status == "error"
    assert pointer.last_error is not None
    assert "ambiguous ranked repo ref" in pointer.last_error


def test_tick_respects_max_scopes(atlas_env: Path) -> None:
    paths = get_paths()
    for scope in ["a", "b", "c", "d"]:
        mark_dirty(paths, scope)

    calls: list[str] = []

    def noop_prepare(paths_arg: AtlasPaths, scope_id: str) -> None:
        calls.append(scope_id)

    result = tick(paths, prepare=noop_prepare, max_scopes=2)
    assert len(calls) == 2
    assert result["remaining"] == 2
    assert len(load_dirty_queue(paths).scopes) == 2


def test_locked_scope_is_requeued(atlas_env: Path) -> None:
    """A scope whose lock is already held must not be rebuilt; it
    should reappear in the dirty queue so a later tick can try."""
    paths = get_paths()
    from atlas_once.ranked_context_warmer import _scope_lock_path

    mark_dirty(paths, "alpha")
    # Pre-create a lock owned by this live process.
    lock = _scope_lock_path(paths, "alpha")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(str(os.getpid()), encoding="utf-8")

    def boom(paths_arg: AtlasPaths, scope_id: str) -> None:
        raise AssertionError("locked scope must not call prepare")

    result = tick(paths, prepare=boom)
    assert result["processed"][0]["status"] == "skipped_locked"
    queue = load_dirty_queue(paths)
    assert any(s.scope_id == "alpha" for s in queue.scopes)


def test_stale_scope_lock_is_recovered(atlas_env: Path) -> None:
    paths = get_paths()
    from atlas_once.ranked_context_warmer import _scope_lock_path

    mark_dirty(paths, "alpha")
    lock = _scope_lock_path(paths, "alpha")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("999999999", encoding="utf-8")

    calls: list[str] = []

    def fake_prepare(paths_arg: AtlasPaths, scope_id: str) -> None:
        calls.append(scope_id)
        write_latest_pointer(
            paths_arg,
            LatestPointer(
                scope_kind="group",
                scope_id=scope_id,
                status="fresh",
                latest_complete_snapshot_key=f"key-{scope_id}",
                latest_fresh_snapshot_key=f"key-{scope_id}",
                updated_at="2026-05-30T00:00:00+00:00",
                last_success_at="2026-05-30T00:00:00+00:00",
            ),
        )

    result = tick(paths, prepare=fake_prepare)

    assert calls == ["alpha"]
    assert result["processed"][0]["status"] == "rebuilt"
    assert load_dirty_queue(paths).scopes == []
    pointer = load_latest_pointer(paths, "group", "alpha")
    assert pointer is not None
    assert pointer.latest_complete_snapshot_key == "key-alpha"


def test_status_section_includes_dirty_list(atlas_env: Path) -> None:
    paths = get_paths()
    mark_dirty(paths, "alpha", reason="seed")
    section = status_section(paths)
    assert section["enabled"] is True
    assert section["dirty_count"] == 1
    assert section["dirty"][0]["scope_id"] == "alpha"


def test_seed_configured_groups_marks_all_ranked_groups_dirty(
    atlas_env: Path,
) -> None:
    cfg_dir = atlas_env / "config" / "atlas_once"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "ranked_contexts.json").write_text(
        json.dumps(
            {
                "version": 3,
                "defaults": {
                    "registry": {"self_owners": []},
                    "runtime": {"dexterity_root": str(atlas_env / "dx")},
                    "strategies": {"elixir_ranked_v1": {"top_files": 3}},
                },
                "repos": {},
                "groups": {
                    "alpha": {"items": [{"ref": "demo", "variant": "default"}]},
                    "beta": {"items": [{"ref": "demo", "variant": "default"}]},
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    paths = get_paths()

    assert configured_group_names(paths) == ["alpha", "beta"]
    result = seed_configured_groups(paths, reason="install")
    seed_configured_groups(paths, reason="install-again")

    assert result["scope_count"] == 2
    assert result["scopes"] == ["alpha", "beta"]
    queue = load_dirty_queue(paths)
    assert sorted(scope.scope_id for scope in queue.scopes) == ["alpha", "beta"]


def test_tick_prioritizes_explicit_item_groups_before_selector_groups(
    atlas_env: Path,
) -> None:
    cfg_dir = atlas_env / "config" / "atlas_once"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "ranked_contexts.json").write_text(
        json.dumps(
            {
                "version": 3,
                "defaults": {
                    "registry": {"self_owners": []},
                    "runtime": {"dexterity_root": str(atlas_env / "dx")},
                    "strategies": {"elixir_ranked_v1": {"top_files": 3}},
                },
                "repos": {},
                "groups": {
                    "gn-ten": {"items": [{"ref": "demo", "variant": "default"}]},
                    "owned-elixir-all": {
                        "selectors": [
                            {
                                "owner_scope": "self",
                                "primary_language": "elixir",
                                "relation": "primary",
                                "roots": [str(atlas_env / "code")],
                                "variant": "default",
                            }
                        ]
                    },
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    paths = get_paths()
    mark_dirty(paths, "owned-elixir-all", reason="older")
    mark_dirty(paths, "gn-ten", reason="newer")

    calls: list[str] = []

    def fake_prepare(paths_arg: AtlasPaths, scope_id: str) -> None:
        calls.append(scope_id)
        write_latest_pointer(
            paths_arg,
            LatestPointer(
                scope_kind="group",
                scope_id=scope_id,
                status="fresh",
                latest_complete_snapshot_key=f"key-{scope_id}",
                latest_fresh_snapshot_key=f"key-{scope_id}",
                updated_at="2026-05-30T00:00:00+00:00",
                last_success_at="2026-05-30T00:00:00+00:00",
            ),
        )

    result = tick(paths, prepare=fake_prepare, max_scopes=1)

    assert calls == ["gn-ten"]
    assert result["remaining"] == 1
    assert [scope.scope_id for scope in load_dirty_queue(paths).scopes] == [
        "owned-elixir-all"
    ]


def test_tick_persists_queue_progress_before_next_scope(atlas_env: Path) -> None:
    cfg_dir = atlas_env / "config" / "atlas_once"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "ranked_contexts.json").write_text(
        json.dumps(
            {
                "version": 3,
                "defaults": {
                    "registry": {"self_owners": []},
                    "runtime": {"dexterity_root": str(atlas_env / "dx")},
                    "strategies": {"elixir_ranked_v1": {"top_files": 3}},
                },
                "repos": {},
                "groups": {
                    "gn-ten": {"items": [{"ref": "demo", "variant": "default"}]},
                    "owned-elixir-all": {
                        "selectors": [
                            {
                                "owner_scope": "self",
                                "primary_language": "elixir",
                                "relation": "primary",
                                "roots": [str(atlas_env / "code")],
                                "variant": "default",
                            }
                        ]
                    },
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    paths = get_paths()
    mark_dirty(paths, "owned-elixir-all", reason="older")
    mark_dirty(paths, "gn-ten", reason="newer")

    def fake_prepare(paths_arg: AtlasPaths, scope_id: str) -> None:
        if scope_id == "owned-elixir-all":
            assert [s.scope_id for s in load_dirty_queue(paths_arg).scopes] == [
                "owned-elixir-all"
            ]
            raise RuntimeError("broad group still warming")
        write_latest_pointer(
            paths_arg,
            LatestPointer(
                scope_kind="group",
                scope_id=scope_id,
                status="fresh",
                latest_complete_snapshot_key=f"key-{scope_id}",
                latest_fresh_snapshot_key=f"key-{scope_id}",
                updated_at="2026-05-30T00:00:00+00:00",
                last_success_at="2026-05-30T00:00:00+00:00",
            ),
        )

    result = tick(paths, prepare=fake_prepare, max_scopes=2)

    assert [entry["status"] for entry in result["processed"]] == [
        "rebuilt",
        "failed",
    ]
    assert load_dirty_queue(paths).scopes == []
    pointer = load_latest_pointer(paths, "group", "gn-ten")
    assert pointer is not None
    assert pointer.latest_complete_snapshot_key == "key-gn-ten"


def test_index_watcher_cycle_ticks_ranked_warmer(
    atlas_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from atlas_once.index_watcher import make_watch_target, start_watch

    paths = get_paths()
    project = atlas_env / "code" / "demo"
    (project / "lib").mkdir(parents=True)
    (project / "mix.exs").write_text("defmodule Demo.MixProject do\nend\n", encoding="utf-8")
    (project / "lib" / "demo.ex").write_text("defmodule Demo do\nend\n", encoding="utf-8")
    target = make_watch_target(project, project_ref="demo")

    calls: list[str] = []

    def fake_run_index(
        project_root: Path,
        *,
        dexterity_root: Path,
        shadow_root: Path,
        dexter_bin: str = "dexter",
    ):
        import subprocess

        del project_root, dexterity_root, shadow_root, dexter_bin
        calls.append("index")
        return subprocess.CompletedProcess(["mix", "dexterity.index"], 0, "ok\n", "")

    def fake_tick(paths_arg, **kwargs):
        del paths_arg, kwargs
        calls.append("tick")
        return {"processed": [], "remaining": 0, "ticked_at": "now"}

    monkeypatch.setattr("atlas_once.index_watcher.run_index", fake_run_index)
    monkeypatch.setattr("atlas_once.ranked_context_warmer.tick", fake_tick)

    start_watch(
        paths,
        [target],
        dexterity_root=atlas_env / "dexterity",
        dexter_bin="dexter",
        shadow_root=paths.state_home / "code" / "shadows",
        debounce_ms=0,
        poll_interval_ms=0,
        once=True,
    )

    assert calls == ["tick", "index", "tick"]


def test_successful_index_refresh_marks_configured_groups_dirty(
    atlas_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from atlas_once.index_watcher import make_watch_target, start_watch

    cfg_dir = atlas_env / "config" / "atlas_once"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "ranked_contexts.json").write_text(
        json.dumps(
            {
                "version": 3,
                "defaults": {
                    "registry": {"self_owners": []},
                    "runtime": {"dexterity_root": str(atlas_env / "dx")},
                    "strategies": {"elixir_ranked_v1": {"top_files": 3}},
                },
                "repos": {},
                "groups": {"alpha": {"items": [{"ref": "demo", "variant": "default"}]}},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    paths = get_paths()
    project = atlas_env / "code" / "demo"
    (project / "lib").mkdir(parents=True)
    (project / "mix.exs").write_text("defmodule Demo.MixProject do\nend\n", encoding="utf-8")
    (project / "lib" / "demo.ex").write_text("defmodule Demo do\nend\n", encoding="utf-8")
    target = make_watch_target(project, project_ref="demo")

    def fake_run_index(
        project_root: Path,
        *,
        dexterity_root: Path,
        shadow_root: Path,
        dexter_bin: str = "dexter",
    ):
        import subprocess

        del project_root, dexterity_root, shadow_root, dexter_bin
        return subprocess.CompletedProcess(["mix", "dexterity.index"], 0, "ok\n", "")

    def fake_tick(paths_arg, **kwargs):
        del paths_arg, kwargs
        return {"processed": [], "remaining": 1, "ticked_at": "now"}

    monkeypatch.setattr("atlas_once.index_watcher.run_index", fake_run_index)
    monkeypatch.setattr("atlas_once.ranked_context_warmer.tick", fake_tick)

    start_watch(
        paths,
        [target],
        dexterity_root=atlas_env / "dexterity",
        dexter_bin="dexter",
        shadow_root=paths.state_home / "code" / "shadows",
        debounce_ms=0,
        poll_interval_ms=0,
        once=True,
    )

    queue = load_dirty_queue(paths)
    assert [scope.scope_id for scope in queue.scopes] == ["alpha"]
    assert queue.scopes[0].reason == "index-refresh"


def test_index_status_payload_includes_ranked_contexts(atlas_env: Path) -> None:
    """The watcher's status_payload must surface ranked_contexts under
    data.tasks.ranked_contexts."""
    from atlas_once.index_watcher import DEFAULT_TTL_MS, status_payload

    paths = get_paths()
    mark_dirty(paths, "alpha")
    payload = status_payload(paths, ttl_ms=DEFAULT_TTL_MS, targets=[])
    assert "ranked_contexts" in payload["tasks"]
    assert payload["tasks"]["ranked_contexts"]["dirty_count"] == 1


def test_render_only_commands_do_not_mark_dirty(
    atlas_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Foreground render-only commands must not touch the warmer queue
    — that is the negative side of the central invariant."""
    from test_ranked_snapshot_fast_path import _seed_snapshot_on_disk

    from atlas_once.atlas import main as atlas_main

    _seed_snapshot_on_disk(atlas_env, scope_id="fixture")
    # Minimal config so the CLI doesn't bail before fast-path runs.
    cfg_dir = atlas_env / "config" / "atlas_once"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "ranked_contexts.json").write_text(
        json.dumps(
            {
                "version": 3,
                "defaults": {
                    "registry": {"self_owners": ["nshkrdotcom"]},
                    "runtime": {"dexterity_root": str(atlas_env / "dx")},
                    "strategies": {"elixir_ranked_v1": {"top_files": 3}},
                },
                "repos": {},
                "groups": {
                    "fixture": {
                        "items": [{"ref": "placeholder", "variant": "default"}]
                    }
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ATLAS_ONCE_RANKED_FAST_PATH", "1")

    paths = get_paths()
    for portion in [1, 50, 100]:
        capsys.readouterr()
        rc = atlas_main(
            [
                "--json",
                "context",
                "ranked",
                "fixture",
                "--portion",
                str(portion),
            ]
        )
        assert rc == 0

    queue = load_dirty_queue(paths)
    assert queue.scopes == [], (
        "render-only command marked a scope dirty — violates Phase 8 invariant"
    )
