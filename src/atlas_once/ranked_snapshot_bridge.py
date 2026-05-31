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
from dataclasses import dataclass
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
    from .ranked_snapshot import RenderView, RenderViewOptions


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
                path=file.output_rel or file.project_rel_path,
                absolute_path=str(file.abs_path),
                score=0.0,
                bytes_size=file.byte_size,
                approx_tokens=file.token_estimate,
            )
        )
    return items


def detect_fallback(prepared: RankedPreparedManifest) -> str | None:
    """Roll project-level ``fallback_used`` flags into a snapshot-wide
    ``fallback_mode`` string.

    Returns ``None`` when every selected project used real Dexterity
    ranking. Returns ``"deterministic_partial"`` when at least one
    project fell back, and ``"deterministic_all"`` when every project
    fell back. Phase 6 requirement: fallback metadata must be
    explicit in the snapshot so the foreground render path can warn.
    """
    fallback_projects = 0
    total_projects = 0
    for repo in prepared.repos:
        for project in repo.projects:
            if project.excluded:
                continue
            total_projects += 1
            if project.fallback_used:
                fallback_projects += 1
    if total_projects == 0 or fallback_projects == 0:
        return None
    if fallback_projects == total_projects:
        return "deterministic_all"
    return "deterministic_partial"


def items_with_fallback_flags(
    prepared: RankedPreparedManifest,
) -> list[RankedItem]:
    """Like :func:`items_from_prepared` but stamps a ``fallback`` flag
    on items whose owning project relied on deterministic ranking.

    The flag set is the executable form of the docset Phase 6 rule:
    *fallback item flags must propagate into the snapshot*.
    """
    fallback_projects: set[tuple[str, str]] = set()
    for repo in prepared.repos:
        for project in repo.projects:
            if project.fallback_used:
                fallback_projects.add((repo.repo_label, project.project_rel_path))

    items: list[RankedItem] = []
    for index, file in enumerate(prepared.files, start=1):
        flags: tuple[str, ...] = ()
        if (file.repo_label, file.project_rel_path) in fallback_projects:
            flags = ("fallback",)
        items.append(
            RankedItem(
                rank=index,
                repo_ref=file.repo_label,
                repo_root="",
                path=file.output_rel or file.project_rel_path,
                absolute_path=str(file.abs_path),
                score=0.0,
                bytes_size=file.byte_size,
                approx_tokens=file.token_estimate,
                flags=flags,
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
    Fallback metadata is folded into ``source_state.fallback_mode``
    so the foreground render path can surface a structured warning.
    """
    scope = scope_from_prepared(prepared, config_hash_fingerprint=config_hash_fingerprint)
    universe = universe_from_options(options)
    algorithm = algorithm_for(options)
    fallback_mode = detect_fallback(prepared)
    base_state = source_state_from_prepared(
        prepared, config_hash_fingerprint=config_hash_fingerprint
    )
    source_state = RankSourceState(
        source_snapshot=base_state.source_snapshot,
        dexterity_index_snapshot=base_state.dexterity_index_snapshot,
        fallback_mode=fallback_mode,
    )
    snapshot_key = build_ranked_snapshot_key(scope, universe, algorithm, source_state)
    warnings: list[str] = []
    if fallback_mode is not None:
        warnings.append(
            f"ranked snapshot built with deterministic fallback ({fallback_mode}); "
            f"Dexterity may be unavailable"
        )
    return RankedSnapshot(
        snapshot_key=snapshot_key,
        created_at=_now(),
        scope=scope,
        universe=universe,
        algorithm=algorithm,
        source_state=source_state,
        items=items_with_fallback_flags(prepared),
        warnings=warnings,
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
    "FastPathRender",
    "SnapshotMissingError",
    "algorithm_for",
    "detect_fallback",
    "items_from_prepared",
    "items_with_fallback_flags",
    "scope_from_prepared",
    "snapshot_from_prepared",
    "source_state_from_prepared",
    "universe_from_options",
    "FreshnessOutcome",
    "SnapshotNotFreshError",
    "load_snapshot_for_scope",
    "resolve_snapshot_freshness",
    "render_snapshot_fast_path",
    "render_view_files",
    "write_snapshot_and_pointer",
]



# ---------------------------------------------------------------------------
# Phase 4 — foreground render fast path.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FastPathRender:
    """Result of rendering a ranked snapshot via the fast path.

    ``text`` is the rendered context bundle (concatenated file
    contents); ``view`` carries the budget summary; ``files`` is the
    ordered list of absolute paths actually included.
    """

    text: str
    view: RenderView
    files: list[Path]


class SnapshotMissingError(LookupError):
    """Raised when the foreground fast path is asked to render but no
    ranked snapshot exists for the requested scope."""


def load_snapshot_for_scope(
    paths: AtlasPaths,
    scope_kind: str,
    scope_id: str,
) -> RankedSnapshot | None:
    """Return the latest *complete* ranked snapshot for a scope.

    This consults only the local cache subtree. It must not call
    Dexterity, run the legacy preparer, or mutate any pointer.
    """
    from .ranked_snapshot import load_latest_pointer, load_ranked_snapshot

    pointer = load_latest_pointer(paths, scope_kind, scope_id)  # type: ignore[arg-type]
    if pointer is None or pointer.latest_complete_snapshot_key is None:
        return None
    return load_ranked_snapshot(
        paths, scope_kind, pointer.latest_complete_snapshot_key  # type: ignore[arg-type]
    )


def render_view_files(view: RenderView) -> tuple[str, list[Path]]:
    """Render the selected items in a view as a single text bundle.

    Reads file contents from ``item.absolute_path`` (or ``item.path``
    if the absolute path is not available). Missing files are skipped
    with a one-line warning embedded into the bundle — this matches
    the central invariant that render-only work never raises a fatal
    error when a snapshot is otherwise valid; the user is told the
    snapshot is stale and can re-prepare explicitly.
    """
    from pathlib import Path

    parts: list[str] = []
    files: list[Path] = []
    seen: set[Path] = set()
    for item in view.selected_items:
        candidate = item.absolute_path or item.path
        if not candidate:
            continue
        target = Path(candidate)
        if not target.is_file():
            parts.append(f"# WARNING: missing file at render time: {target}\n")
            continue
        resolved = target.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        files.append(resolved)
        output_rel = item.path or resolved.name
        parts.append(f"# FILE: ./{output_rel}\n")
        contents = resolved.read_text(encoding="utf-8")
        parts.append(contents)
        if not contents.endswith("\n"):
            parts.append("\n")
    return "".join(parts), files


def render_snapshot_fast_path(
    paths: AtlasPaths,
    scope_kind: str,
    scope_id: str,
    render_options: RenderViewOptions,
    *,
    require_snapshot: bool = True,
) -> FastPathRender | None:
    """Cheap projection over a ranked snapshot.

    Never calls Dexterity, the code-intelligence adapter, the legacy
    ``_build_prepared_manifest``, or any rank computation. Pure I/O
    plus :func:`build_render_view` plus :func:`render_view_files`.

    If no snapshot exists and ``require_snapshot`` is true, raises
    :class:`SnapshotMissingError`. With ``require_snapshot=False``,
    returns ``None`` instead (used by the legacy command path during
    the migration window).
    """
    from .ranked_snapshot import build_render_view

    snapshot = load_snapshot_for_scope(paths, scope_kind, scope_id)
    if snapshot is None:
        if require_snapshot:
            raise SnapshotMissingError(
                f"no ranked snapshot for scope_kind={scope_kind!r} scope_id={scope_id!r}; "
                f"run `atlas context ranked prepare {scope_id}` first"
            )
        return None
    view = build_render_view(snapshot, render_options)
    text, files = render_view_files(view)
    return FastPathRender(text=text, view=view, files=files)



# ---------------------------------------------------------------------------
# Phase 7 — freshness / wait / strict semantics.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FreshnessOutcome:
    """Result of resolving a snapshot's freshness for a foreground call.

    ``status`` is one of ``fresh``, ``stale``, ``warming``, ``fallback``,
    ``error``, ``unknown`` — mirroring :attr:`LatestPointer.status`.
    ``waited_ms`` records how long the wait-fresh loop slept.
    ``snapshot_key`` is the key the caller should actually render
    (may be the complete or fresh key depending on policy).
    """

    status: str
    snapshot_key: str | None
    waited_ms: int
    pointer_dirty: bool
    pointer_warming: bool
    fallback_mode: str | None


class SnapshotNotFreshError(RuntimeError):
    """Raised when --fresh-required is set and the snapshot is not fresh."""


def resolve_snapshot_freshness(
    paths: AtlasPaths,
    scope_kind: str,
    scope_id: str,
    *,
    wait_fresh_ms: int = 0,
    fresh_required: bool = False,
    poll_interval_ms: int = 50,
) -> FreshnessOutcome:
    """Resolve which snapshot key the foreground render should use.

    Behaviour mirrors the docset Phase 7 spec:

    * ``wait_fresh_ms <= 0``: return immediately with whatever the
      pointer currently reports.
    * ``wait_fresh_ms > 0``: poll the pointer up to that budget,
      returning early as soon as ``status == 'fresh'``.
    * ``fresh_required=True``: after the wait, raise
      :class:`SnapshotNotFreshError` unless the final status is
      ``fresh``.

    The function never builds a snapshot, calls Dexterity, or mutates
    the pointer — it is pure read + sleep.
    """
    import time

    from .ranked_snapshot import load_latest_pointer

    start = time.monotonic()
    deadline = start + max(0.0, wait_fresh_ms / 1000.0)
    pointer = load_latest_pointer(paths, scope_kind, scope_id)  # type: ignore[arg-type]
    while pointer is not None and pointer.status != "fresh" and time.monotonic() < deadline:
        time.sleep(poll_interval_ms / 1000.0)
        pointer = load_latest_pointer(paths, scope_kind, scope_id)  # type: ignore[arg-type]

    waited_ms = int((time.monotonic() - start) * 1000)
    if pointer is None:
        outcome = FreshnessOutcome(
            status="unknown",
            snapshot_key=None,
            waited_ms=waited_ms,
            pointer_dirty=False,
            pointer_warming=False,
            fallback_mode=None,
        )
    else:
        outcome = FreshnessOutcome(
            status=pointer.status,
            snapshot_key=(
                pointer.latest_fresh_snapshot_key
                or pointer.latest_complete_snapshot_key
            ),
            waited_ms=waited_ms,
            pointer_dirty=pointer.dirty,
            pointer_warming=pointer.warming,
            fallback_mode=None,  # filled by caller from snapshot.source_state
        )

    if fresh_required and outcome.status != "fresh":
        raise SnapshotNotFreshError(
            f"snapshot for {scope_kind}/{scope_id} is not fresh (status={outcome.status!r})"
        )
    return outcome
