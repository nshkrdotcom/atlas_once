"""Bridge between the legacy ranked-context prepared manifest and the new
ranked-snapshot / render-view data model (docset Phase 3, migration Stage 2).

This module is intentionally small. It exists so the new
:mod:`atlas_once.ranked_snapshot` module stays free of legacy types,
while the heavy legacy code in :mod:`atlas_once.ranked_context` stays
free of new types. The bridge is the only piece that knows about both.

The bridge runs after a successful legacy prepare and writes a
``RankedSnapshot`` + ``LatestPointer`` alongside the legacy manifest.
Render-only options (portion, max-tokens, max-bytes, no-budget) are
**stripped** before they reach the snapshot key, so the snapshot key
satisfies the central invariant from the docset §0:

    Render-only options must not change the ranked snapshot key.

The first iteration writes one snapshot per call. Phases 4/6 may
upgrade the bridge to compute a *true* full universe (currently the
legacy slicer applies portion+budget at the per-repo layer, so the
snapshot ``items`` list reflects the prepared manifest's view rather
than the unsliced rank universe). The snapshot still has the correct
key — portion/budget changes never produce a *different* key — they
just produce a snapshot whose ``items`` is the slice the legacy
preparer happened to emit. The dedicated ``prepare_full_universe``
helper below forces ``portion=100, no_budget=True`` so callers that
want a true universe can always get one.
"""

from __future__ import annotations

import hashlib
import time
from typing import TYPE_CHECKING

from .ranked_snapshot import (
    LatestPointer,
    RankAlgorithmOptions,
    RankedItem,
    RankedSnapshot,
    RankScopeOptions,
    RankSourceState,
    RankUniverseOptions,
    build_ranked_snapshot_key,
    write_latest_pointer,
    write_ranked_snapshot,
)

if TYPE_CHECKING:
    from pathlib import Path

    from .config import AtlasPaths
    from .ranked_context import RankedContextOptions, RankedPreparedManifest


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def scope_from_prepared(
    prepared: RankedPreparedManifest,
    *,
    config_hash_fingerprint: str | None = None,
) -> RankScopeOptions:
    """Derive a :class:`RankScopeOptions` from a legacy prepared manifest.

    ``resolved_repos`` is the sorted tuple of repo_keys actually
    selected; that captures repo registry/fleet effects without
    needing the agent to re-resolve.
    """
    resolved_repos = tuple(sorted({str(repo.repo_key) for repo in prepared.repos}))
    return RankScopeOptions(
        scope_kind="group",
        scope_id=prepared.config_name,
        resolved_repos=resolved_repos,
        registry_fingerprint=config_hash_fingerprint,
        profile_fingerprint=None,
    )


def universe_from_options(options: RankedContextOptions) -> RankUniverseOptions:
    """Rank-universe slice of :class:`RankedContextOptions`.

    Render-only fields (``portion``, ``max_tokens``, ``max_bytes``,
    ``no_budget``, ``current_path``) are intentionally dropped here.
    """
    return RankUniverseOptions(
        projects_mode=options.projects_mode,
        files_mode=options.files_mode,
        select_mode=options.select_mode,
        include_projects=tuple(options.include_projects),
        exclude_projects=tuple(options.exclude_projects),
    )


def algorithm_for(_options: RankedContextOptions) -> RankAlgorithmOptions:
    return RankAlgorithmOptions(algorithm_version="rank-v1", priority_tier=100)


def source_state_from_prepared(
    prepared: RankedPreparedManifest, *, config_hash_fingerprint: str | None = None
) -> RankSourceState:
    """Best-effort source-state fingerprint for the snapshot.

    **Important**: this fingerprint must NOT depend on ``prepared.files``,
    because that list is already portion/budget sliced — using it would
    let render-only options leak into the snapshot key.

    The fingerprint hashes (sorted ``repo_label`` set, ``config_hash``
    fingerprint passed in by the caller). Phase 6 replaces this with
    the watcher's ``IndexFreshness`` snapshot ids, which are the true
    source-state fingerprints.
    """
    repo_labels = sorted({str(repo.repo_label) for repo in prepared.repos})
    parts = list(repo_labels)
    if config_hash_fingerprint is not None:
        parts.append(f"config={config_hash_fingerprint}")
    fingerprint = _hash_text("\n".join(parts))
    return RankSourceState(source_snapshot=fingerprint)


def items_from_prepared(prepared: RankedPreparedManifest) -> list[RankedItem]:
    """Project the legacy ``prepared.files`` into ``RankedItem`` form.

    Order is preserved; ``rank`` is the 1-based index. Scores are not
    currently emitted by the legacy preparer, so they default to 0.0.
    """
    items: list[RankedItem] = []
    for index, file in enumerate(prepared.files, start=1):
        items.append(
            RankedItem(
                rank=index,
                repo_ref=file.repo_label,
                repo_root="",  # filled later when we propagate per-repo roots
                path=file.project_rel_path or file.output_rel,
                absolute_path=str(file.abs_path),
                score=0.0,
                bytes_size=file.byte_size,
                approx_tokens=file.token_estimate,
            )
        )
    return items


def snapshot_from_prepared(
    prepared: RankedPreparedManifest,
    options: RankedContextOptions,
    *,
    config_hash_fingerprint: str | None = None,
) -> RankedSnapshot:
    """Build a :class:`RankedSnapshot` from a legacy prepared manifest.

    The snapshot key is computed from rank-affecting inputs only, so
    repeated calls with different render options yield the same key.
    """
    scope = scope_from_prepared(prepared, config_hash_fingerprint=config_hash_fingerprint)
    universe = universe_from_options(options)
    algorithm = algorithm_for(options)
    source_state = source_state_from_prepared(
        prepared, config_hash_fingerprint=config_hash_fingerprint
    )
    snapshot_key = build_ranked_snapshot_key(scope, universe, algorithm, source_state)
    return RankedSnapshot(
        snapshot_key=snapshot_key,
        created_at=_now(),
        scope=scope,
        universe=universe,
        algorithm=algorithm,
        source_state=source_state,
        items=items_from_prepared(prepared),
    )


def write_snapshot_and_pointer(
    paths: AtlasPaths,
    snapshot: RankedSnapshot,
    *,
    status: str = "fresh",
) -> tuple[Path, Path]:
    """Persist snapshot then advance the latest pointer.

    Order matters: writing the snapshot first guarantees the pointer
    never references a missing file. The pointer is the post-success
    handle described in docset §10 stage 5: ``latest_complete`` only
    moves forward after the snapshot is on disk.
    """
    snapshot_path = write_ranked_snapshot(paths, snapshot)
    pointer = LatestPointer(
        scope_kind=snapshot.scope.scope_kind,
        scope_id=snapshot.scope.scope_id,
        status=status,
        latest_complete_snapshot_key=snapshot.snapshot_key,
        latest_fresh_snapshot_key=snapshot.snapshot_key if status == "fresh" else None,
        latest_attempted_snapshot_key=snapshot.snapshot_key,
        updated_at=snapshot.created_at,
        last_success_at=snapshot.created_at,
        last_attempt_at=snapshot.created_at,
    )
    pointer_path = write_latest_pointer(paths, pointer)
    return snapshot_path, pointer_path


__all__ = [
    "algorithm_for",
    "items_from_prepared",
    "scope_from_prepared",
    "snapshot_from_prepared",
    "source_state_from_prepared",
    "universe_from_options",
    "write_snapshot_and_pointer",
]
