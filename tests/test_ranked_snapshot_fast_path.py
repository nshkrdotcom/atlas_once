"""Phase 4 — foreground render must use the snapshot fast path.

Central docset invariant proven here:

    Given a valid ranked snapshot exists, calling the foreground
    render with render-only options (portion, max-tokens, max-bytes,
    no-budget) MUST NOT:

        * call the legacy expensive builder (_build_prepared_manifest),
        * call the code-intelligence / Dexterity adapter,
        * mutate the latest pointer,
        * write a new snapshot.

These are negative tests: we patch the heavy entrypoints to fail loudly
if they are reached and assert the fast path still succeeds.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from atlas_once.config import get_paths
from atlas_once.ranked_snapshot import (
    LatestPointer,
    RankAlgorithmOptions,
    RankedItem,
    RankedSnapshot,
    RankScopeOptions,
    RankSourceState,
    RankUniverseOptions,
    RenderViewOptions,
    build_ranked_snapshot_key,
    write_latest_pointer,
    write_ranked_snapshot,
)
from atlas_once.ranked_snapshot_bridge import (
    SnapshotMissingError,
    load_snapshot_for_scope,
    render_snapshot_fast_path,
)


def _seed_snapshot_on_disk(atlas_env: Path, *, scope_id: str = "fixture") -> RankedSnapshot:
    paths = get_paths()
    repo = atlas_env / "code" / "demo"
    repo.mkdir(parents=True, exist_ok=True)
    file_a = repo / "lib" / "a.ex"
    file_b = repo / "lib" / "b.ex"
    file_c = repo / "lib" / "c.ex"
    for path, body in [
        (file_a, "defmodule A do\n  def x, do: 1\nend\n"),
        (file_b, "defmodule B do\n  def y, do: 2\nend\n"),
        (file_c, "defmodule C do\n  def z, do: 3\nend\n"),
    ]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    scope = RankScopeOptions(
        scope_kind="group", scope_id=scope_id, resolved_repos=("demo",)
    )
    universe = RankUniverseOptions()
    algorithm = RankAlgorithmOptions()
    source_state = RankSourceState(source_snapshot="fixed")
    key = build_ranked_snapshot_key(scope, universe, algorithm, source_state)
    items = [
        RankedItem(
            rank=1,
            repo_ref="demo",
            repo_root=str(repo),
            path="lib/a.ex",
            absolute_path=str(file_a),
            bytes_size=file_a.stat().st_size,
            approx_tokens=20,
        ),
        RankedItem(
            rank=2,
            repo_ref="demo",
            repo_root=str(repo),
            path="lib/b.ex",
            absolute_path=str(file_b),
            bytes_size=file_b.stat().st_size,
            approx_tokens=20,
        ),
        RankedItem(
            rank=3,
            repo_ref="demo",
            repo_root=str(repo),
            path="lib/c.ex",
            absolute_path=str(file_c),
            bytes_size=file_c.stat().st_size,
            approx_tokens=20,
        ),
    ]
    snapshot = RankedSnapshot(
        snapshot_key=key,
        created_at="2026-05-30T00:00:00+00:00",
        scope=scope,
        universe=universe,
        algorithm=algorithm,
        source_state=source_state,
        items=items,
    )
    write_ranked_snapshot(paths, snapshot)
    write_latest_pointer(
        paths,
        LatestPointer(
            scope_kind="group",
            scope_id=scope_id,
            status="fresh",
            latest_complete_snapshot_key=key,
            latest_fresh_snapshot_key=key,
            updated_at=snapshot.created_at,
            last_success_at=snapshot.created_at,
        ),
    )
    return snapshot


def test_fast_path_renders_full_snapshot_by_default(atlas_env: Path) -> None:
    snapshot = _seed_snapshot_on_disk(atlas_env)
    result = render_snapshot_fast_path(
        get_paths(), "group", snapshot.scope.scope_id, RenderViewOptions()
    )
    assert result is not None
    assert "# FILE: ./lib/a.ex" in result.text
    assert "# FILE: ./lib/b.ex" in result.text
    assert "# FILE: ./lib/c.ex" in result.text
    assert result.view.budget.selected_count_after_budget == 3
    assert len(result.files) == 3


@pytest.mark.parametrize(
    "render,expected_count",
    [
        (RenderViewOptions(portion=50), 2),       # ceil(3*0.5)=2
        (RenderViewOptions(portion=33), 1),       # ceil(3*0.33)=1
        (RenderViewOptions(portion=100), 3),
        (RenderViewOptions(portion=0), 0),
    ],
)
def test_portion_changes_only_render_view_count(
    atlas_env: Path, render: RenderViewOptions, expected_count: int
) -> None:
    snapshot = _seed_snapshot_on_disk(atlas_env)
    result = render_snapshot_fast_path(
        get_paths(), "group", snapshot.scope.scope_id, render
    )
    assert result is not None
    assert result.view.budget.selected_count_after_budget == expected_count
    # The snapshot key on disk is unchanged (proved by re-loading).
    snap2 = load_snapshot_for_scope(get_paths(), "group", snapshot.scope.scope_id)
    assert snap2 is not None
    assert snap2.snapshot_key == snapshot.snapshot_key


def test_fast_path_does_not_call_legacy_builder_or_code_intelligence(
    atlas_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = _seed_snapshot_on_disk(atlas_env)

    # Trip-wires: any call to the legacy builder or Dexterity must fail.
    def boom_builder(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "fast path called the legacy _build_prepared_manifest "
            "(forbidden by docset Phase 4 invariant)"
        )

    def boom_subprocess(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "fast path called subprocess.run (Dexterity) — forbidden by docset Phase 4"
        )

    monkeypatch.setattr(
        "atlas_once.ranked_context._build_prepared_manifest", boom_builder
    )
    monkeypatch.setattr("atlas_once.ranked_context.subprocess.run", boom_subprocess)

    result = render_snapshot_fast_path(
        get_paths(),
        "group",
        snapshot.scope.scope_id,
        RenderViewOptions(portion=50, max_tokens=10_000),
    )
    assert result is not None
    assert result.view.snapshot_key == snapshot.snapshot_key


def test_fast_path_does_not_advance_pointer_or_create_new_snapshot(
    atlas_env: Path,
) -> None:
    from atlas_once.ranked_snapshot import latest_pointer_path, snapshots_root

    snapshot = _seed_snapshot_on_disk(atlas_env)
    paths = get_paths()
    pointer_path = latest_pointer_path(paths, "group", snapshot.scope.scope_id)
    pointer_mtime_before = pointer_path.stat().st_mtime_ns
    snap_count_before = len(list(snapshots_root(paths, "group").glob("*.json")))

    for render in [
        RenderViewOptions(portion=10),
        RenderViewOptions(portion=99, max_tokens=1),
        RenderViewOptions(max_bytes=10, no_budget=True),
    ]:
        render_snapshot_fast_path(paths, "group", snapshot.scope.scope_id, render)

    # Pointer must be byte-identical and snapshot count unchanged.
    assert pointer_path.stat().st_mtime_ns == pointer_mtime_before
    snap_count_after = len(list(snapshots_root(paths, "group").glob("*.json")))
    assert snap_count_after == snap_count_before == 1


def test_missing_snapshot_raises_structured_error(atlas_env: Path) -> None:
    with pytest.raises(SnapshotMissingError):
        render_snapshot_fast_path(
            get_paths(), "group", "no-such-scope", RenderViewOptions()
        )


def test_missing_snapshot_returns_none_when_not_required(atlas_env: Path) -> None:
    result = render_snapshot_fast_path(
        get_paths(),
        "group",
        "no-such-scope",
        RenderViewOptions(),
        require_snapshot=False,
    )
    assert result is None


# ---------------------------------------------------------------------------
# CLI-level Phase 4 wiring (ATLAS_ONCE_RANKED_FAST_PATH=1).
# ---------------------------------------------------------------------------


def test_cli_fast_path_renders_without_calling_legacy_builder(
    atlas_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``atlas --json context ranked <scope>`` under the fast-path flag
    must render from the on-disk snapshot without touching the legacy
    builder or Dexterity."""
    import json as _json

    from atlas_once.atlas import main as atlas_main

    snapshot = _seed_snapshot_on_disk(atlas_env, scope_id="fixture")

    # Also seed an empty ranked_contexts.json so the CLI doesn't bail
    # out on a missing config — the fast path doesn't read it, but the
    # CLI front-end touches it on the auto-prepare check path.
    (atlas_env / "config" / "atlas_once").mkdir(parents=True, exist_ok=True)
    (atlas_env / "config" / "atlas_once" / "ranked_contexts.json").write_text(
        _json.dumps(
            {
                "version": 3,
                "defaults": {
                    "registry": {"self_owners": ["nshkrdotcom"]},
                    "runtime": {"dexterity_root": str(atlas_env / "dx")},
                    "strategies": {"elixir_ranked_v1": {"top_files": 3}},
                },
                "repos": {},
                "groups": {"fixture": {"items": [{"ref": "placeholder", "variant": "default"}]}},
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("ATLAS_ONCE_RANKED_FAST_PATH", "1")

    def boom_builder(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "fast-path CLI must not call _build_prepared_manifest"
        )

    def boom_subprocess(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "fast-path CLI must not call subprocess.run (Dexterity)"
        )

    monkeypatch.setattr(
        "atlas_once.ranked_context._build_prepared_manifest", boom_builder
    )
    monkeypatch.setattr(
        "atlas_once.ranked_context.subprocess.run", boom_subprocess
    )

    assert atlas_main(["--json", "context", "ranked", "fixture"]) == 0
    payload = _json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["command"] == "context.ranked"
    data = payload["data"]
    assert data["ranked_snapshot"]["key"] == snapshot.snapshot_key
    assert data["ranked_snapshot"]["source"] == "snapshot_fast_path"
    assert "render_view" in data
    assert data["render_view"]["selected_count_after_budget"] >= 1


def test_cli_fast_path_portion_changes_render_view_not_snapshot_key(
    atlas_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Repeated CLI calls with different ``--portion`` values must
    return the same ``ranked_snapshot.key`` and varying
    ``render_view.selected_count_after_budget``."""
    import json as _json

    from atlas_once.atlas import main as atlas_main

    snapshot = _seed_snapshot_on_disk(atlas_env, scope_id="fixture")
    (atlas_env / "config" / "atlas_once").mkdir(parents=True, exist_ok=True)
    (atlas_env / "config" / "atlas_once" / "ranked_contexts.json").write_text(
        _json.dumps(
            {
                "version": 3,
                "defaults": {
                    "registry": {"self_owners": ["nshkrdotcom"]},
                    "runtime": {"dexterity_root": str(atlas_env / "dx")},
                    "strategies": {"elixir_ranked_v1": {"top_files": 3}},
                },
                "repos": {},
                "groups": {"fixture": {"items": [{"ref": "placeholder", "variant": "default"}]}},
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("ATLAS_ONCE_RANKED_FAST_PATH", "1")

    seen_keys: set[str] = set()
    seen_counts: list[int] = []
    for portion in [1, 33, 50, 100]:
        capsys.readouterr()  # drain
        rc = atlas_main(
            ["--json", "context", "ranked", "fixture", "--portion", str(portion)]
        )
        assert rc == 0
        payload = _json.loads(capsys.readouterr().out)
        seen_keys.add(payload["data"]["ranked_snapshot"]["key"])
        seen_counts.append(
            payload["data"]["render_view"]["selected_count_after_budget"]
        )

    assert seen_keys == {snapshot.snapshot_key}
    # Monotonic non-decreasing as portion grows.
    assert seen_counts == sorted(seen_counts)
