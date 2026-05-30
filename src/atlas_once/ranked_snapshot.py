"""Ranked-snapshot / render-view data model and persistence layer.

This module is the Phase-1+2 home for the refactor described in
``docs/20260529/atlas/01-ranked-snapshot-architecture.md`` and onward.

Design invariants (from the docset §0 in 00-executive-summary.md):

* **I1 Genericity**: nothing in this module hard-codes a host path. All
  on-disk locations are derived from :class:`atlas_once.config.AtlasPaths`,
  which itself reads ``ATLAS_ONCE_*`` env vars or the packaged profile
  defaults.
* **I3 Installer-Only**: this module never writes outside the
  ``paths.ranked_context_cache_root`` subtree; directories are created
  lazily by writers, never at import time.

The data model intentionally lives next to the legacy
``RankedPreparedManifest`` rather than replacing it; later migration
phases route foreground render and explicit prepare through this module.

The **central invariant** the model encodes is:

    Render-only options (portion, max-tokens, max-bytes, no-budget,
    output format, output path) must NOT appear in the ranked snapshot
    key. They are projection parameters over an already-built ranked
    universe.

The :func:`build_ranked_snapshot_key_payload` helper makes that
invariant testable: it accepts a ``RankScopeOptions`` /
``RankUniverseOptions`` / ``RankAlgorithmOptions`` /
``RankSourceState`` quartet and intentionally has no parameter for any
render-only field.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from .config import AtlasPaths

# ---------------------------------------------------------------------------
# Schema versions (single source of truth for on-disk shape).
# ---------------------------------------------------------------------------

RANKED_SNAPSHOT_SCHEMA = "atlas.ranked_snapshot.v1"
RANKED_LATEST_POINTER_SCHEMA = "atlas.ranked_snapshot_latest.v1"
RANKED_RENDER_VIEW_SCHEMA = "atlas.ranked_render_view.v1"
RANKED_SNAPSHOT_KEY_SCHEMA = "atlas.ranked_snapshot_key.v1"

ScopeKind = Literal["group", "repo", "path"]

# ---------------------------------------------------------------------------
# Pure data model: rank-affecting inputs.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RankScopeOptions:
    """Identity of the *thing being ranked*.

    Exactly one of ``group``/``repo``/``path`` should be set; the others
    are ``None``. ``resolved_repos`` is the post-registry, post-fleet
    fingerprint of the actually-included repository roots — it is what
    matters for cache identity, not the raw scope string.
    """

    scope_kind: ScopeKind
    scope_id: str
    resolved_repos: tuple[str, ...] = ()
    registry_fingerprint: str | None = None
    fleet_fingerprint: str | None = None
    profile_fingerprint: str | None = None


@dataclass(frozen=True)
class RankUniverseOptions:
    """Candidate-universe policy.

    These DO affect ranked snapshot identity because they change *what*
    is in the ranked list — but they are NOT render-time knobs.
    """

    projects_mode: str = "preset"
    files_mode: str = "lib"
    select_mode: str = "ranked"
    include_projects: tuple[str, ...] = ()
    exclude_projects: tuple[str, ...] = ()
    exclude_path_prefixes: tuple[str, ...] = ()
    exclude_globs: tuple[str, ...] = ()


@dataclass(frozen=True)
class RankAlgorithmOptions:
    """Identity of the ranking *algorithm*.

    Bumping ``algorithm_version`` is the explicit migration hook for
    "we changed the scoring formula".
    """

    algorithm_version: str = "rank-v1"
    priority_tier: int = 100


@dataclass(frozen=True)
class RankSourceState:
    """Source state the rank depends on.

    These two snapshot ids are computed by the index watcher; the
    snapshot key folds them in so a change in source (or in the
    Dexterity index) produces a different key.
    """

    source_snapshot: str | None = None
    dexterity_index_snapshot: str | None = None
    fallback_mode: str | None = None  # set to "deterministic_lib" or similar


# ---------------------------------------------------------------------------
# Pure data model: render-only options. *Never* used in snapshot key.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RenderViewOptions:
    """The cheap projection over a ranked snapshot."""

    portion: int | None = None
    max_tokens: int | None = None
    max_bytes: int | None = None
    no_budget: bool = False


@dataclass(frozen=True)
class OutputOptions:
    """Pure output presentation knobs."""

    output_format: str = "text"  # text|json
    output_path: str | None = None
    color: bool = False
    verbosity: int = 0


# ---------------------------------------------------------------------------
# Snapshot/item/view records.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RankedItem:
    rank: int
    repo_ref: str
    repo_root: str
    path: str
    absolute_path: str | None = None
    score: float = 0.0
    score_components: dict[str, float] = field(default_factory=dict)
    bytes_size: int = 0
    approx_tokens: int = 0
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class RankedSnapshot:
    """Full ranked candidate universe, before any portion/budget slicing."""

    snapshot_key: str
    created_at: str
    scope: RankScopeOptions
    universe: RankUniverseOptions
    algorithm: RankAlgorithmOptions
    source_state: RankSourceState
    items: list[RankedItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    schema: str = RANKED_SNAPSHOT_SCHEMA
    version: int = 1


@dataclass(frozen=True)
class LatestPointer:
    """Small metadata file at ``latest/<scope_kind>/<scope_id>.json``."""

    scope_kind: ScopeKind
    scope_id: str
    status: str = "unknown"  # fresh|stale|warming|fallback|error|unknown
    latest_complete_snapshot_key: str | None = None
    latest_fresh_snapshot_key: str | None = None
    latest_attempted_snapshot_key: str | None = None
    latest_fallback_snapshot_key: str | None = None
    dirty: bool = False
    warming: bool = False
    updated_at: str | None = None
    last_success_at: str | None = None
    last_attempt_at: str | None = None
    last_error: str | None = None
    schema: str = RANKED_LATEST_POINTER_SCHEMA
    version: int = 1


@dataclass(frozen=True)
class BudgetSummary:
    candidate_count_before_portion: int
    candidate_count_after_portion: int
    selected_count_after_budget: int
    approx_tokens: int
    approx_bytes: int


@dataclass(frozen=True)
class RenderView:
    """Cheap projection of a snapshot under a :class:`RenderViewOptions`."""

    snapshot_key: str
    render_options: RenderViewOptions
    selected_items: list[RankedItem]
    budget: BudgetSummary
    schema: str = RANKED_RENDER_VIEW_SCHEMA
    version: int = 1


# ---------------------------------------------------------------------------
# Pure helpers — the testable core of the snapshot/view split.
# ---------------------------------------------------------------------------


def stable_hash(payload: object) -> str:
    """Deterministic SHA-256 over a JSON-serialisable payload."""
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_json_default)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json_default(value: object) -> object:
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"unhashable: {type(value)!r}")


def build_ranked_snapshot_key_payload(
    scope: RankScopeOptions,
    universe: RankUniverseOptions,
    algorithm: RankAlgorithmOptions,
    source_state: RankSourceState,
) -> dict[str, object]:
    """Compute the canonical payload that feeds the snapshot key.

    The function intentionally accepts no :class:`RenderViewOptions`
    parameter: it is structurally impossible for ``--portion`` and
    friends to leak into the snapshot key by way of this helper.
    """
    return {
        "schema": RANKED_SNAPSHOT_KEY_SCHEMA,
        "scope": _ordered_dict(asdict(scope)),
        "universe": _ordered_dict(asdict(universe)),
        "algorithm": _ordered_dict(asdict(algorithm)),
        "source_state": _ordered_dict(asdict(source_state)),
    }


def build_ranked_snapshot_key(
    scope: RankScopeOptions,
    universe: RankUniverseOptions,
    algorithm: RankAlgorithmOptions,
    source_state: RankSourceState,
) -> str:
    return stable_hash(build_ranked_snapshot_key_payload(scope, universe, algorithm, source_state))


def _ordered_dict(payload: dict[str, Any]) -> dict[str, Any]:
    """Recursively normalise lists/tuples into sorted-keys dict form.

    Tuples become lists (JSON has no tuple); dict values are kept as-is
    because :func:`stable_hash` already passes ``sort_keys=True`` to
    :func:`json.dumps`.
    """
    out: dict[str, Any] = {}
    for key in sorted(payload):
        val = payload[key]
        if isinstance(val, tuple):
            out[key] = list(val)
        else:
            out[key] = val
    return out


def stable_sort_ranked_items(items: list[RankedItem]) -> list[RankedItem]:
    """Order ranked items by (rank ASC, repo_ref ASC, path ASC).

    The tiebreakers make the ordering deterministic even when two items
    share a score, so the same input universe always produces the same
    snapshot bytes.
    """
    return sorted(items, key=lambda it: (it.rank, it.repo_ref, it.path))


def apply_portion(items: list[RankedItem], portion: int | None) -> list[RankedItem]:
    """Slice the top ``portion``% of an already-ranked list.

    ``portion=None`` and ``portion=100`` both return the full list.
    ``portion<=0`` returns an empty list. The relative order is
    preserved.
    """
    if portion is None or portion >= 100:
        return list(items)
    if portion <= 0:
        return []
    import math

    count = math.ceil(len(items) * (portion / 100.0))
    return list(items[:count])


def apply_budget(
    items: list[RankedItem],
    max_tokens: int | None,
    max_bytes: int | None,
    no_budget: bool,
) -> list[RankedItem]:
    """Pack items in rank order until the budget would be exceeded.

    When ``no_budget`` is true, the budget is ignored. Items that would
    exceed the budget are skipped (not aborted at first overflow) so
    the packer is consistent with the legacy budgeting in
    ``ranked_context._build_prepared_manifest``.
    """
    if no_budget or (max_tokens is None and max_bytes is None):
        return list(items)
    out: list[RankedItem] = []
    total_tokens = 0
    total_bytes = 0
    for item in items:
        next_tokens = total_tokens + item.approx_tokens
        next_bytes = total_bytes + item.bytes_size
        if max_tokens is not None and next_tokens > max_tokens:
            continue
        if max_bytes is not None and next_bytes > max_bytes:
            continue
        out.append(item)
        total_tokens = next_tokens
        total_bytes = next_bytes
    return out


def build_render_view(
    snapshot: RankedSnapshot,
    render_options: RenderViewOptions,
) -> RenderView:
    """Cheap pure projection of a snapshot under render-only options."""
    after_portion = apply_portion(snapshot.items, render_options.portion)
    selected = apply_budget(
        after_portion,
        render_options.max_tokens,
        render_options.max_bytes,
        render_options.no_budget,
    )
    return RenderView(
        snapshot_key=snapshot.snapshot_key,
        render_options=render_options,
        selected_items=selected,
        budget=BudgetSummary(
            candidate_count_before_portion=len(snapshot.items),
            candidate_count_after_portion=len(after_portion),
            selected_count_after_budget=len(selected),
            approx_tokens=sum(it.approx_tokens for it in selected),
            approx_bytes=sum(it.bytes_size for it in selected),
        ),
    )


# ---------------------------------------------------------------------------
# On-disk layout (lazily created — never at import time).
# ---------------------------------------------------------------------------


def _ranked_root(paths: AtlasPaths) -> Path:
    return paths.ranked_context_cache_root


def snapshots_root(paths: AtlasPaths, scope_kind: ScopeKind) -> Path:
    return _ranked_root(paths) / "snapshots" / scope_kind


def latest_root(paths: AtlasPaths, scope_kind: ScopeKind) -> Path:
    return _ranked_root(paths) / "latest" / scope_kind


def locks_root(paths: AtlasPaths) -> Path:
    return _ranked_root(paths) / "locks"


def events_path(paths: AtlasPaths) -> Path:
    return _ranked_root(paths) / "events.jsonl"


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename(name: str) -> str:
    safe = _SAFE_NAME.sub("_", name).strip("._-")
    return safe or "ranked"


def snapshot_path(paths: AtlasPaths, scope_kind: ScopeKind, snapshot_key: str) -> Path:
    return snapshots_root(paths, scope_kind) / f"{_safe_filename(snapshot_key)}.json"


def latest_pointer_path(paths: AtlasPaths, scope_kind: ScopeKind, scope_id: str) -> Path:
    return latest_root(paths, scope_kind) / f"{_safe_filename(scope_id)}.json"


# ---------------------------------------------------------------------------
# Atomic JSON I/O.
# ---------------------------------------------------------------------------


def atomic_write_json(path: Path, payload: object) -> None:
    """Write ``payload`` as pretty JSON atomically (write+rename).

    The destination directory is created on demand.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    tmp_path = Path(tmp_name)
    import contextlib

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, default=_json_default)
            handle.write("\n")
        os.replace(tmp_path, path)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            tmp_path.unlink()
        raise


# ---------------------------------------------------------------------------
# Snapshot persistence.
# ---------------------------------------------------------------------------


class RankedSnapshotValidationError(ValueError):
    """Raised when a persisted snapshot/pointer fails schema validation."""


def snapshot_to_dict(snapshot: RankedSnapshot) -> dict[str, object]:
    return {
        "schema": snapshot.schema,
        "version": snapshot.version,
        "snapshot_key": snapshot.snapshot_key,
        "created_at": snapshot.created_at,
        "scope": _ordered_dict(asdict(snapshot.scope)),
        "universe": _ordered_dict(asdict(snapshot.universe)),
        "algorithm": _ordered_dict(asdict(snapshot.algorithm)),
        "source_state": _ordered_dict(asdict(snapshot.source_state)),
        "items": [_ordered_dict(asdict(item)) for item in snapshot.items],
        "warnings": list(snapshot.warnings),
        "errors": list(snapshot.errors),
        "key_payload": build_ranked_snapshot_key_payload(
            snapshot.scope, snapshot.universe, snapshot.algorithm, snapshot.source_state
        ),
    }


def _opt_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _as_str_list(value: object) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return []


def _as_int(value: object, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, str)):
        return int(value)
    raise TypeError(f"cannot coerce {type(value).__name__} to int")


def _as_float(value: object, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float, str)):
        return float(value)
    raise TypeError(f"cannot coerce {type(value).__name__} to float")


def _item_from_dict(it: object) -> RankedItem:
    assert isinstance(it, dict)
    components_obj = it.get("score_components", {}) or {}
    assert isinstance(components_obj, dict)
    score_components = {str(k): float(v) for k, v in components_obj.items()}
    return RankedItem(
        rank=_as_int(it["rank"]),
        repo_ref=str(it["repo_ref"]),
        repo_root=str(it["repo_root"]),
        path=str(it["path"]),
        absolute_path=_opt_str(it.get("absolute_path")),
        score=_as_float(it.get("score")),
        score_components=score_components,
        bytes_size=_as_int(it.get("bytes_size")),
        approx_tokens=_as_int(it.get("approx_tokens")),
        flags=tuple(_as_str_list(it.get("flags"))),
    )


def snapshot_from_dict(payload: dict[str, object]) -> RankedSnapshot:
    validate_ranked_snapshot(payload)
    scope_p = payload["scope"]
    universe_p = payload["universe"]
    algo_p = payload["algorithm"]
    src_p = payload["source_state"]
    assert isinstance(scope_p, dict)
    assert isinstance(universe_p, dict)
    assert isinstance(algo_p, dict)
    assert isinstance(src_p, dict)
    items_p = payload.get("items", [])
    assert isinstance(items_p, list)
    warnings_p = payload.get("warnings", []) or []
    errors_p = payload.get("errors", []) or []
    assert isinstance(warnings_p, list)
    assert isinstance(errors_p, list)
    return RankedSnapshot(
        snapshot_key=str(payload["snapshot_key"]),
        created_at=str(payload["created_at"]),
        scope=RankScopeOptions(
            scope_kind=str(scope_p["scope_kind"]),  # type: ignore[arg-type]
            scope_id=str(scope_p["scope_id"]),
            resolved_repos=tuple(_as_str_list(scope_p.get("resolved_repos"))),
            registry_fingerprint=_opt_str(scope_p.get("registry_fingerprint")),
            fleet_fingerprint=_opt_str(scope_p.get("fleet_fingerprint")),
            profile_fingerprint=_opt_str(scope_p.get("profile_fingerprint")),
        ),
        universe=RankUniverseOptions(
            projects_mode=str(universe_p.get("projects_mode", "preset")),
            files_mode=str(universe_p.get("files_mode", "lib")),
            select_mode=str(universe_p.get("select_mode", "ranked")),
            include_projects=tuple(_as_str_list(universe_p.get("include_projects"))),
            exclude_projects=tuple(_as_str_list(universe_p.get("exclude_projects"))),
            exclude_path_prefixes=tuple(_as_str_list(universe_p.get("exclude_path_prefixes"))),
            exclude_globs=tuple(_as_str_list(universe_p.get("exclude_globs"))),
        ),
        algorithm=RankAlgorithmOptions(
            algorithm_version=str(algo_p.get("algorithm_version", "rank-v1")),
            priority_tier=_as_int(algo_p.get("priority_tier"), 100),
        ),
        source_state=RankSourceState(
            source_snapshot=_opt_str(src_p.get("source_snapshot")),
            dexterity_index_snapshot=_opt_str(src_p.get("dexterity_index_snapshot")),
            fallback_mode=_opt_str(src_p.get("fallback_mode")),
        ),
        items=[_item_from_dict(it) for it in items_p],
        warnings=[str(w) for w in warnings_p],
        errors=[str(e) for e in errors_p],
        schema=str(payload.get("schema", RANKED_SNAPSHOT_SCHEMA)),
        version=_as_int(payload.get("version"), 1),
    )


def validate_ranked_snapshot(payload: object) -> None:
    if not isinstance(payload, dict):
        raise RankedSnapshotValidationError("ranked snapshot payload must be an object")
    required = {
        "schema",
        "version",
        "snapshot_key",
        "created_at",
        "scope",
        "universe",
        "algorithm",
        "source_state",
        "items",
    }
    missing = required - set(payload)
    if missing:
        raise RankedSnapshotValidationError(
            f"ranked snapshot missing fields: {sorted(missing)}"
        )
    if payload.get("schema") != RANKED_SNAPSHOT_SCHEMA:
        raise RankedSnapshotValidationError(
            f"ranked snapshot schema must be {RANKED_SNAPSHOT_SCHEMA}; "
            f"got {payload.get('schema')!r}"
        )
    items = payload["items"]
    if not isinstance(items, list):
        raise RankedSnapshotValidationError("ranked snapshot items must be a list")


def validate_latest_pointer(payload: object) -> None:
    if not isinstance(payload, dict):
        raise RankedSnapshotValidationError("latest pointer payload must be an object")
    required = {"schema", "version", "scope_kind", "scope_id", "status"}
    missing = required - set(payload)
    if missing:
        raise RankedSnapshotValidationError(
            f"latest pointer missing fields: {sorted(missing)}"
        )
    if payload.get("schema") != RANKED_LATEST_POINTER_SCHEMA:
        raise RankedSnapshotValidationError(
            f"latest pointer schema must be {RANKED_LATEST_POINTER_SCHEMA}; "
            f"got {payload.get('schema')!r}"
        )


def latest_pointer_to_dict(pointer: LatestPointer) -> dict[str, object]:
    return _ordered_dict(asdict(pointer))


def latest_pointer_from_dict(payload: dict[str, object]) -> LatestPointer:
    validate_latest_pointer(payload)
    return LatestPointer(
        scope_kind=str(payload["scope_kind"]),  # type: ignore[arg-type]
        scope_id=str(payload["scope_id"]),
        status=str(payload.get("status", "unknown")),
        latest_complete_snapshot_key=_opt_str(payload.get("latest_complete_snapshot_key")),
        latest_fresh_snapshot_key=_opt_str(payload.get("latest_fresh_snapshot_key")),
        latest_attempted_snapshot_key=_opt_str(payload.get("latest_attempted_snapshot_key")),
        latest_fallback_snapshot_key=_opt_str(payload.get("latest_fallback_snapshot_key")),
        dirty=bool(payload.get("dirty", False)),
        warming=bool(payload.get("warming", False)),
        updated_at=_opt_str(payload.get("updated_at")),
        last_success_at=_opt_str(payload.get("last_success_at")),
        last_attempt_at=_opt_str(payload.get("last_attempt_at")),
        last_error=_opt_str(payload.get("last_error")),
        schema=str(payload.get("schema", RANKED_LATEST_POINTER_SCHEMA)),
        version=_as_int(payload.get("version"), 1),
    )


def write_ranked_snapshot(paths: AtlasPaths, snapshot: RankedSnapshot) -> Path:
    target = snapshot_path(paths, snapshot.scope.scope_kind, snapshot.snapshot_key)
    atomic_write_json(target, snapshot_to_dict(snapshot))
    return target


def load_ranked_snapshot(
    paths: AtlasPaths, scope_kind: ScopeKind, snapshot_key: str
) -> RankedSnapshot | None:
    target = snapshot_path(paths, scope_kind, snapshot_key)
    if not target.is_file():
        return None
    payload = json.loads(target.read_text(encoding="utf-8"))
    return snapshot_from_dict(payload)


def write_latest_pointer(paths: AtlasPaths, pointer: LatestPointer) -> Path:
    target = latest_pointer_path(paths, pointer.scope_kind, pointer.scope_id)
    atomic_write_json(target, latest_pointer_to_dict(pointer))
    return target


def load_latest_pointer(
    paths: AtlasPaths, scope_kind: ScopeKind, scope_id: str
) -> LatestPointer | None:
    target = latest_pointer_path(paths, scope_kind, scope_id)
    if not target.is_file():
        return None
    payload = json.loads(target.read_text(encoding="utf-8"))
    return latest_pointer_from_dict(payload)


# ---------------------------------------------------------------------------
# Public surface (re-exports for tests / future imports).
# ---------------------------------------------------------------------------

__all__ = [
    "RANKED_SNAPSHOT_SCHEMA",
    "RANKED_LATEST_POINTER_SCHEMA",
    "RANKED_RENDER_VIEW_SCHEMA",
    "RANKED_SNAPSHOT_KEY_SCHEMA",
    "RankScopeOptions",
    "RankUniverseOptions",
    "RankAlgorithmOptions",
    "RankSourceState",
    "RenderViewOptions",
    "OutputOptions",
    "RankedItem",
    "RankedSnapshot",
    "LatestPointer",
    "BudgetSummary",
    "RenderView",
    "RankedSnapshotValidationError",
    "stable_hash",
    "build_ranked_snapshot_key_payload",
    "build_ranked_snapshot_key",
    "stable_sort_ranked_items",
    "apply_portion",
    "apply_budget",
    "build_render_view",
    "snapshots_root",
    "latest_root",
    "locks_root",
    "events_path",
    "snapshot_path",
    "latest_pointer_path",
    "atomic_write_json",
    "snapshot_to_dict",
    "snapshot_from_dict",
    "validate_ranked_snapshot",
    "validate_latest_pointer",
    "latest_pointer_to_dict",
    "latest_pointer_from_dict",
    "write_ranked_snapshot",
    "load_ranked_snapshot",
    "write_latest_pointer",
    "load_latest_pointer",
]
