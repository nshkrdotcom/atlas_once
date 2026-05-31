"""Phase 7 — freshness / wait / strict semantics on the snapshot fast path."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from test_ranked_snapshot_fast_path import _seed_snapshot_on_disk

from atlas_once.config import get_paths
from atlas_once.ranked_snapshot import (
    LatestPointer,
    write_latest_pointer,
)
from atlas_once.ranked_snapshot_bridge import (
    SnapshotNotFreshError,
    resolve_snapshot_freshness,
)


def _set_pointer_status(scope_id: str, *, status: str, dirty: bool = False) -> None:
    paths = get_paths()
    from atlas_once.ranked_snapshot import load_latest_pointer

    base = load_latest_pointer(paths, "group", scope_id)
    assert base is not None
    updated = LatestPointer(
        scope_kind="group",
        scope_id=scope_id,
        status=status,
        latest_complete_snapshot_key=base.latest_complete_snapshot_key,
        latest_fresh_snapshot_key=(
            base.latest_complete_snapshot_key if status == "fresh" else None
        ),
        latest_attempted_snapshot_key=base.latest_complete_snapshot_key,
        dirty=dirty,
        warming=False,
        updated_at=base.updated_at,
        last_success_at=base.last_success_at,
        last_attempt_at=base.last_attempt_at,
    )
    write_latest_pointer(paths, updated)


def test_fresh_pointer_returns_immediately(atlas_env: Path) -> None:
    snap = _seed_snapshot_on_disk(atlas_env)
    _set_pointer_status(snap.scope.scope_id, status="fresh")
    outcome = resolve_snapshot_freshness(get_paths(), "group", snap.scope.scope_id)
    assert outcome.status == "fresh"
    assert outcome.snapshot_key == snap.snapshot_key
    assert outcome.waited_ms < 50


def test_stale_pointer_returned_when_wait_not_set(atlas_env: Path) -> None:
    snap = _seed_snapshot_on_disk(atlas_env)
    _set_pointer_status(snap.scope.scope_id, status="stale", dirty=True)
    outcome = resolve_snapshot_freshness(get_paths(), "group", snap.scope.scope_id)
    assert outcome.status == "stale"
    assert outcome.snapshot_key == snap.snapshot_key
    assert outcome.pointer_dirty is True


def test_wait_fresh_timeout_returns_stale(atlas_env: Path) -> None:
    snap = _seed_snapshot_on_disk(atlas_env)
    _set_pointer_status(snap.scope.scope_id, status="stale", dirty=True)
    start = time.monotonic()
    outcome = resolve_snapshot_freshness(
        get_paths(), "group", snap.scope.scope_id, wait_fresh_ms=200, poll_interval_ms=50
    )
    elapsed_ms = (time.monotonic() - start) * 1000
    assert outcome.status == "stale"
    assert outcome.waited_ms >= 150  # actually waited
    assert elapsed_ms < 500           # but not absurdly long


def test_wait_fresh_returns_early_when_pointer_flips_fresh(atlas_env: Path) -> None:
    snap = _seed_snapshot_on_disk(atlas_env)
    _set_pointer_status(snap.scope.scope_id, status="fresh")
    start = time.monotonic()
    outcome = resolve_snapshot_freshness(
        get_paths(),
        "group",
        snap.scope.scope_id,
        wait_fresh_ms=2000,
        poll_interval_ms=10,
    )
    elapsed_ms = (time.monotonic() - start) * 1000
    assert outcome.status == "fresh"
    assert elapsed_ms < 200  # didn't burn the full 2s budget


def test_fresh_required_raises_when_stale(atlas_env: Path) -> None:
    snap = _seed_snapshot_on_disk(atlas_env)
    _set_pointer_status(snap.scope.scope_id, status="stale")
    with pytest.raises(SnapshotNotFreshError):
        resolve_snapshot_freshness(
            get_paths(),
            "group",
            snap.scope.scope_id,
            fresh_required=True,
        )


def test_fresh_required_succeeds_when_fresh(atlas_env: Path) -> None:
    snap = _seed_snapshot_on_disk(atlas_env)
    _set_pointer_status(snap.scope.scope_id, status="fresh")
    outcome = resolve_snapshot_freshness(
        get_paths(), "group", snap.scope.scope_id, fresh_required=True
    )
    assert outcome.status == "fresh"


def test_missing_pointer_yields_unknown(atlas_env: Path) -> None:
    outcome = resolve_snapshot_freshness(get_paths(), "group", "no-such-scope")
    assert outcome.status == "unknown"
    assert outcome.snapshot_key is None
