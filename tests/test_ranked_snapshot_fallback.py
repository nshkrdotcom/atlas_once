"""Phase 6 — fallback metadata is explicit in the snapshot.

When Dexterity is unavailable and the preparer falls back to
deterministic lib/* ranking, the snapshot must:

  * carry source_state.fallback_mode = "deterministic_all" or
    "deterministic_partial",
  * emit a warning string in snapshot.warnings,
  * stamp item.flags = ("fallback",) on items whose owning project
    fell back, while leaving non-fallback items unmarked,
  * remain a normal snapshot that the fast path can render.

The fallback_mode also participates in the snapshot key (it is part
of RankSourceState), so switching from fallback to real ranking
correctly produces a different snapshot key — a fallback snapshot
is never silently treated as a Dexterity snapshot.
"""

from __future__ import annotations

from pathlib import Path

from atlas_once.ranked_context import (
    PreparedProjectSummary,
    PreparedRepoSummary,
    RankedContextOptions,
    RankedPreparedManifest,
    RankedSelectedFile,
)
from atlas_once.ranked_snapshot_bridge import (
    detect_fallback,
    items_with_fallback_flags,
    snapshot_from_prepared,
)


def _file(repo: str, project_rel: str, name: str, *, tokens: int = 10) -> RankedSelectedFile:
    return RankedSelectedFile(
        abs_path=Path(f"/tmp/{repo}/{project_rel}/{name}").resolve(),
        output_rel=f"{repo}/{name}",
        repo_label=repo,
        project_rel_path=project_rel,
        byte_size=tokens * 4,
        token_estimate=tokens,
    )


def _project(name: str, *, fallback: bool) -> PreparedProjectSummary:
    return PreparedProjectSummary(
        project_rel_path=name,
        category="primary",
        excluded=False,
        exclusion_reason=None,
        selected_count=1,
        fallback_used=fallback,
        shadow_root=None,
    )


def _repo(label: str, projects: list[PreparedProjectSummary]) -> PreparedRepoSummary:
    return PreparedRepoSummary(
        repo_key=label,
        repo_label=label,
        repo_root=Path(f"/tmp/{label}").resolve(),
        variant_name="default",
        strategy="elixir_ranked_v1",
        project_count=len(projects),
        projects=projects,
    )


def _prepared(repos: list[PreparedRepoSummary], files: list[RankedSelectedFile],
) -> RankedPreparedManifest:
    return RankedPreparedManifest(
        config_name="fixture",
        manifest_path=Path("/tmp/manifest.json"),
        config_hash="hash-1",
        prepared_at="2026-05-30T00:00:00+00:00",
        files=files,
        source_roots=[r.repo_root for r in repos],
        repo_count=len(repos),
        project_count=sum(len(r.projects) for r in repos),
        repos=repos,
    )


def test_no_fallback_when_all_projects_used_dexterity() -> None:
    prepared = _prepared(
        [_repo("alpha", [_project("apps/a", fallback=False)])],
        [_file("alpha", "apps/a", "x.ex")],
    )
    assert detect_fallback(prepared) is None
    snapshot = snapshot_from_prepared(prepared, RankedContextOptions())
    assert snapshot.source_state.fallback_mode is None
    assert snapshot.warnings == []
    assert all(item.flags == () for item in snapshot.items)


def test_deterministic_all_when_every_project_fell_back() -> None:
    prepared = _prepared(
        [_repo("alpha", [_project("apps/a", fallback=True)])],
        [_file("alpha", "apps/a", "x.ex")],
    )
    assert detect_fallback(prepared) == "deterministic_all"
    snapshot = snapshot_from_prepared(prepared, RankedContextOptions())
    assert snapshot.source_state.fallback_mode == "deterministic_all"
    assert any("fallback" in w for w in snapshot.warnings)
    assert all(item.flags == ("fallback",) for item in snapshot.items)


def test_deterministic_partial_when_some_projects_fell_back() -> None:
    prepared = _prepared(
        [
            _repo(
                "alpha",
                [_project("apps/a", fallback=True), _project("apps/b", fallback=False)],
            )
        ],
        [
            _file("alpha", "apps/a", "x.ex"),
            _file("alpha", "apps/b", "y.ex"),
        ],
    )
    assert detect_fallback(prepared) == "deterministic_partial"
    flagged = items_with_fallback_flags(prepared)
    # Only the apps/a items carry the fallback flag.
    flags_by_path = {item.path: item.flags for item in flagged}
    assert flags_by_path["apps/a"] == ("fallback",)
    assert flags_by_path["apps/b"] == ()


def test_fallback_mode_changes_snapshot_key() -> None:
    """Switching from a Dexterity-backed prepare to a fallback prepare
    MUST yield a different snapshot key. Otherwise a fallback render
    could silently be served from a 'fresh' pointer next time."""
    no_fb = _prepared(
        [_repo("alpha", [_project("apps/a", fallback=False)])],
        [_file("alpha", "apps/a", "x.ex")],
    )
    fb = _prepared(
        [_repo("alpha", [_project("apps/a", fallback=True)])],
        [_file("alpha", "apps/a", "x.ex")],
    )
    snap_no_fb = snapshot_from_prepared(no_fb, RankedContextOptions())
    snap_fb = snapshot_from_prepared(fb, RankedContextOptions())
    assert snap_no_fb.snapshot_key != snap_fb.snapshot_key


def test_multi_repo_merge_preserves_repo_ref_and_rank_order() -> None:
    """When the legacy preparer concatenates per-repo file lists into
    prepared.files, the bridge must preserve both ordering and
    repo_ref so the snapshot keeps the multi-repo distinction."""
    prepared = _prepared(
        [
            _repo("alpha", [_project("apps/a", fallback=False)]),
            _repo("beta", [_project("apps/b", fallback=False)]),
        ],
        [
            _file("alpha", "apps/a", "x.ex"),
            _file("alpha", "apps/a", "y.ex"),
            _file("beta", "apps/b", "z.ex"),
        ],
    )
    snapshot = snapshot_from_prepared(prepared, RankedContextOptions())
    repos_in_order = [item.repo_ref for item in snapshot.items]
    assert repos_in_order == ["alpha", "alpha", "beta"]
    # Ranks are 1,2,3 with no gaps.
    assert [item.rank for item in snapshot.items] == [1, 2, 3]
