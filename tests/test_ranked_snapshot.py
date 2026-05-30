"""Phase 1+2 tests for the new ranked-snapshot / render-view model."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from atlas_once.config import get_paths
from atlas_once.ranked_snapshot import (
    RANKED_LATEST_POINTER_SCHEMA,
    RANKED_SNAPSHOT_SCHEMA,
    BudgetSummary,
    LatestPointer,
    RankAlgorithmOptions,
    RankedItem,
    RankedSnapshot,
    RankedSnapshotValidationError,
    RankScopeOptions,
    RankSourceState,
    RankUniverseOptions,
    RenderViewOptions,
    apply_budget,
    apply_portion,
    build_ranked_snapshot_key,
    build_ranked_snapshot_key_payload,
    build_render_view,
    latest_pointer_from_dict,
    latest_pointer_to_dict,
    load_latest_pointer,
    load_ranked_snapshot,
    snapshot_to_dict,
    stable_hash,
    stable_sort_ranked_items,
    validate_latest_pointer,
    validate_ranked_snapshot,
    write_latest_pointer,
    write_ranked_snapshot,
)

# ---------------------------------------------------------------------------
# Pure helpers (Phase 1).
# ---------------------------------------------------------------------------


def _scope(scope_id: str = "gn-ten") -> RankScopeOptions:
    return RankScopeOptions(
        scope_kind="group",
        scope_id=scope_id,
        resolved_repos=("repo_a", "repo_b"),
        registry_fingerprint="reg-1",
        fleet_fingerprint=None,
        profile_fingerprint="prof-1",
    )


def _universe() -> RankUniverseOptions:
    return RankUniverseOptions(
        projects_mode="preset",
        files_mode="lib",
        select_mode="ranked",
        include_projects=("a",),
        exclude_projects=("z",),
    )


def _algo() -> RankAlgorithmOptions:
    return RankAlgorithmOptions(algorithm_version="rank-v1", priority_tier=100)


def _src() -> RankSourceState:
    return RankSourceState(
        source_snapshot="src-deadbeef",
        dexterity_index_snapshot="dex-cafe",
    )


def _items(n: int = 5) -> list[RankedItem]:
    return [
        RankedItem(
            rank=i + 1,
            repo_ref="repo_a",
            repo_root="/tmp/repo_a",
            path=f"lib/file_{i}.ex",
            score=1.0 / (i + 1),
            score_components={"pagerank": 0.5 / (i + 1)},
            bytes_size=1000 * (i + 1),
            approx_tokens=200 * (i + 1),
        )
        for i in range(n)
    ]


def test_stable_hash_is_deterministic_and_order_insensitive() -> None:
    a = stable_hash({"x": 1, "y": [1, 2, 3]})
    b = stable_hash({"y": [1, 2, 3], "x": 1})
    assert a == b
    assert a != stable_hash({"x": 1, "y": [3, 2, 1]})


def test_build_ranked_snapshot_key_payload_excludes_render_options() -> None:
    # The function has no parameter for render options. That is the point.
    payload = build_ranked_snapshot_key_payload(_scope(), _universe(), _algo(), _src())
    flat = json.dumps(payload)
    for token in ("portion", "max_tokens", "max_bytes", "no_budget", "output_path", "color"):
        assert token not in flat, f"render-only token {token!r} leaked into snapshot key payload"


@pytest.mark.parametrize(
    "render",
    [
        RenderViewOptions(portion=10),
        RenderViewOptions(portion=50, max_tokens=10_000),
        RenderViewOptions(max_bytes=99_999, no_budget=True),
        RenderViewOptions(portion=100, max_tokens=1, max_bytes=1, no_budget=True),
    ],
)
def test_render_options_do_not_affect_ranked_snapshot_key(render: RenderViewOptions) -> None:
    """Central invariant: render-only knobs MUST NOT change the snapshot key."""
    baseline = build_ranked_snapshot_key(_scope(), _universe(), _algo(), _src())
    # We don't even have a public surface that mixes render options in — but
    # we make the invariant explicit by hashing the rank quartet alone and
    # asserting it stays stable while render options vary in test cases.
    assert baseline == build_ranked_snapshot_key(_scope(), _universe(), _algo(), _src())
    # And of course the render options object is *not* a parameter of the
    # key builder at all:
    with pytest.raises(TypeError):
        build_ranked_snapshot_key(_scope(), _universe(), _algo(), _src(), render)  # type: ignore[call-arg]


def test_rank_options_affect_ranked_snapshot_key() -> None:
    baseline = build_ranked_snapshot_key(_scope(), _universe(), _algo(), _src())
    # Scope change.
    other_scope = RankScopeOptions(
        scope_kind="group", scope_id="gn-ten", resolved_repos=("repo_a",)
    )
    assert build_ranked_snapshot_key(other_scope, _universe(), _algo(), _src()) != baseline
    # Universe change.
    other_universe = RankUniverseOptions(
        projects_mode="all", files_mode="lib", select_mode="ranked"
    )
    assert build_ranked_snapshot_key(_scope(), other_universe, _algo(), _src()) != baseline
    # Algorithm version bump.
    other_algo = RankAlgorithmOptions(algorithm_version="rank-v2")
    assert build_ranked_snapshot_key(_scope(), _universe(), other_algo, _src()) != baseline
    # Source-state delta.
    other_src = RankSourceState(source_snapshot="other-src")
    assert build_ranked_snapshot_key(_scope(), _universe(), _algo(), other_src) != baseline


def test_apply_portion_handles_edges() -> None:
    items = _items(10)
    assert apply_portion(items, None) == items
    assert apply_portion(items, 100) == items
    assert apply_portion(items, 0) == []
    assert apply_portion(items, -5) == []
    # ceil(10 * 0.5) = 5
    assert len(apply_portion(items, 50)) == 5
    # ceil(10 * 0.01) = 1
    assert len(apply_portion(items, 1)) == 1


def test_apply_budget_skips_over_budget_items_but_keeps_order() -> None:
    items = _items(5)  # tokens 200,400,600,800,1000  bytes 1000,2000,3000,4000,5000
    out = apply_budget(items, max_tokens=600, max_bytes=None, no_budget=False)
    assert [it.rank for it in out] == [1, 2]
    # no_budget bypasses everything
    assert apply_budget(items, max_tokens=1, max_bytes=1, no_budget=True) == items
    # Byte budget filters separately.
    out_bytes = apply_budget(items, max_tokens=None, max_bytes=3000, no_budget=False)
    assert [it.rank for it in out_bytes] == [1, 2]


def test_budget_preserves_rank_order_when_skipping_one_oversized_item() -> None:
    items = [
        RankedItem(rank=1, repo_ref="r", repo_root="/r", path="a.ex", approx_tokens=10),
        RankedItem(rank=2, repo_ref="r", repo_root="/r", path="b.ex", approx_tokens=9999),
        RankedItem(rank=3, repo_ref="r", repo_root="/r", path="c.ex", approx_tokens=10),
    ]
    out = apply_budget(items, max_tokens=100, max_bytes=None, no_budget=False)
    # Item 2 is skipped, item 3 still fits — order [1, 3] preserved.
    assert [it.rank for it in out] == [1, 3]


def test_build_render_view_reports_budget_summary() -> None:
    snapshot = RankedSnapshot(
        snapshot_key="sk-1",
        created_at="2026-05-30T00:00:00+00:00",
        scope=_scope(),
        universe=_universe(),
        algorithm=_algo(),
        source_state=_src(),
        items=_items(10),
    )
    view = build_render_view(snapshot, RenderViewOptions(portion=50, max_tokens=1000))
    assert view.snapshot_key == "sk-1"
    # portion 50 of 10 = 5; budget 1000 tokens fits ranks 1+2+3 (200+400=600, +600=1200 over)
    # so packer should accept 200, 400 then skip 600 (=1200), skip 800, skip 1000 -> [1,2]
    assert [it.rank for it in view.selected_items] == [1, 2]
    assert isinstance(view.budget, BudgetSummary)
    assert view.budget.candidate_count_before_portion == 10
    assert view.budget.candidate_count_after_portion == 5
    assert view.budget.selected_count_after_budget == 2
    assert view.budget.approx_tokens == 600


def test_stable_sort_ranked_items() -> None:
    items = [
        RankedItem(rank=2, repo_ref="b", repo_root="/b", path="z.ex"),
        RankedItem(rank=1, repo_ref="a", repo_root="/a", path="y.ex"),
        RankedItem(rank=2, repo_ref="a", repo_root="/a", path="x.ex"),
    ]
    sorted_items = stable_sort_ranked_items(items)
    assert [(it.rank, it.repo_ref, it.path) for it in sorted_items] == [
        (1, "a", "y.ex"),
        (2, "a", "x.ex"),
        (2, "b", "z.ex"),
    ]


# ---------------------------------------------------------------------------
# Phase 2 — persistence.
# ---------------------------------------------------------------------------


def _snapshot() -> RankedSnapshot:
    scope = _scope()
    universe = _universe()
    algo = _algo()
    src = _src()
    key = build_ranked_snapshot_key(scope, universe, algo, src)
    return RankedSnapshot(
        snapshot_key=key,
        created_at="2026-05-30T00:00:00+00:00",
        scope=scope,
        universe=universe,
        algorithm=algo,
        source_state=src,
        items=_items(3),
    )


def test_snapshot_round_trip_through_disk(atlas_env: Path) -> None:
    paths = get_paths()
    snap = _snapshot()
    target = write_ranked_snapshot(paths, snap)
    assert target.is_file()
    loaded = load_ranked_snapshot(paths, "group", snap.snapshot_key)
    assert loaded is not None
    assert loaded.snapshot_key == snap.snapshot_key
    assert [it.path for it in loaded.items] == [it.path for it in snap.items]


def test_snapshot_dict_includes_schema_and_key_payload() -> None:
    snap = _snapshot()
    payload = snapshot_to_dict(snap)
    assert payload["schema"] == RANKED_SNAPSHOT_SCHEMA
    assert payload["snapshot_key"] == snap.snapshot_key
    assert "key_payload" in payload  # makes the snapshot self-describing
    assert payload["key_payload"]["scope"]["scope_id"] == snap.scope.scope_id


def test_validate_ranked_snapshot_rejects_missing_fields() -> None:
    with pytest.raises(RankedSnapshotValidationError):
        validate_ranked_snapshot({"schema": RANKED_SNAPSHOT_SCHEMA})


def test_validate_ranked_snapshot_rejects_wrong_schema() -> None:
    snap = _snapshot()
    payload = snapshot_to_dict(snap)
    payload["schema"] = "atlas.something_else.v9"
    with pytest.raises(RankedSnapshotValidationError):
        validate_ranked_snapshot(payload)


def test_load_returns_none_when_snapshot_missing(atlas_env: Path) -> None:
    paths = get_paths()
    assert load_ranked_snapshot(paths, "group", "no-such-key") is None


def test_atomic_write_does_not_leave_temp_files(atlas_env: Path) -> None:
    paths = get_paths()
    write_ranked_snapshot(paths, _snapshot())
    # No .tmp leftovers in snapshots dir.
    snap_dir = paths.ranked_context_cache_root / "snapshots" / "group"
    leftovers = list(snap_dir.glob("*.tmp"))
    assert leftovers == []


def test_latest_pointer_round_trip(atlas_env: Path) -> None:
    paths = get_paths()
    pointer = LatestPointer(
        scope_kind="group",
        scope_id="gn-ten",
        status="fresh",
        latest_complete_snapshot_key="sk-1",
        latest_fresh_snapshot_key="sk-1",
        latest_attempted_snapshot_key="sk-1",
        updated_at="2026-05-30T00:00:00+00:00",
        last_success_at="2026-05-30T00:00:00+00:00",
        last_attempt_at="2026-05-30T00:00:00+00:00",
    )
    target = write_latest_pointer(paths, pointer)
    assert target.is_file()
    loaded = load_latest_pointer(paths, "group", "gn-ten")
    assert loaded is not None
    assert loaded.latest_complete_snapshot_key == "sk-1"
    assert loaded.schema == RANKED_LATEST_POINTER_SCHEMA


def test_validate_latest_pointer_rejects_missing_fields() -> None:
    with pytest.raises(RankedSnapshotValidationError):
        validate_latest_pointer({"schema": RANKED_LATEST_POINTER_SCHEMA})


def test_latest_pointer_dict_is_jsonable() -> None:
    pointer = LatestPointer(scope_kind="group", scope_id="gn-ten")
    payload = latest_pointer_to_dict(pointer)
    rendered = json.dumps(payload, sort_keys=True)
    parsed = json.loads(rendered)
    restored = latest_pointer_from_dict(parsed)
    assert restored.scope_id == "gn-ten"


def test_persistence_paths_live_under_ranked_context_cache_root(atlas_env: Path) -> None:
    """I3 sanity: every path we touch is rooted under ``paths.ranked_context_cache_root``.

    No host mutation outside the configured cache subtree.
    """
    paths = get_paths()
    snap = _snapshot()
    target = write_ranked_snapshot(paths, snap)
    pointer_target = write_latest_pointer(
        paths,
        LatestPointer(scope_kind="group", scope_id="gn-ten", status="fresh"),
    )
    root = paths.ranked_context_cache_root.resolve()
    assert root in target.resolve().parents
    assert root in pointer_target.resolve().parents
