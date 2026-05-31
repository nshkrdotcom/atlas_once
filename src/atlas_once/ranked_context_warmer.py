"""Phase 8 — background ranked-context warming.

This module is the scaffolded "background warming" surface described
in ``docs/20260529/atlas/11-agent-checklist.md`` §12 (Phase 8).
It is deliberately small and self-contained:

* a JSON-backed *dirty queue* listing scopes that need a snapshot
  rebuild,
* a per-scope file lock so two ticks never rebuild the same scope
  concurrently,
* a :func:`tick` entrypoint that pops up to ``max_scopes`` dirty
  scopes and rebuilds each via the existing
  :func:`atlas_once.ranked_context.prepare_ranked_manifest` — i.e.
  rebuilds and explicit ``atlas context ranked prepare`` share the
  same snapshot builder (docset Phase 8 invariant: *no separate
  background-only logic*),
* atomic pointer advancement on success; **on failure the previous
  latest-complete pointer is preserved** (docset absolute safety
  constraint),
* a :func:`status_section` that the index watcher folds into its
  ``data.tasks.ranked_contexts`` JSON envelope.

Genericity (I1): every path the module touches is derived from
``AtlasPaths.ranked_context_cache_root``; nothing here is host-specific.
Installer-only (I3): files are created on demand only by the dirty-
queue writer; ``ensure_state`` is not extended.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .ranked_snapshot import (
    LatestPointer,
    atomic_write_json,
    load_latest_pointer,
    write_latest_pointer,
)

if TYPE_CHECKING:
    from .config import AtlasPaths


RANKED_WARMER_SCHEMA = "atlas.ranked_warmer.v1"
DEFAULT_MAX_SCOPES_PER_TICK = 2


def _warmer_root(paths: AtlasPaths) -> Path:
    return paths.ranked_context_cache_root / "warmer"


def _dirty_queue_path(paths: AtlasPaths) -> Path:
    return _warmer_root(paths) / "dirty_queue.json"


def _events_path(paths: AtlasPaths) -> Path:
    return _warmer_root(paths) / "events.jsonl"


def _scope_lock_path(paths: AtlasPaths, scope_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in scope_id) or "scope"
    return _warmer_root(paths) / "locks" / f"{safe}.lock"


@dataclass
class DirtyScope:
    scope_kind: str
    scope_id: str
    reason: str = ""
    enqueued_at: str | None = None

    def key(self) -> tuple[str, str]:
        return (self.scope_kind, self.scope_id)


@dataclass
class DirtyQueue:
    scopes: list[DirtyScope] = field(default_factory=list)
    schema: str = RANKED_WARMER_SCHEMA
    version: int = 1

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "version": self.version,
            "scopes": [
                {
                    "scope_kind": s.scope_kind,
                    "scope_id": s.scope_id,
                    "reason": s.reason,
                    "enqueued_at": s.enqueued_at,
                }
                for s in self.scopes
            ],
        }

    @classmethod
    def from_dict(cls, payload: object) -> DirtyQueue:
        if not isinstance(payload, dict):
            return cls()
        raw = payload.get("scopes", [])
        if not isinstance(raw, list):
            return cls()
        scopes: list[DirtyScope] = []
        seen: set[tuple[str, str]] = set()
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            scope_kind = str(entry.get("scope_kind", "group"))
            scope_id = str(entry.get("scope_id", "")).strip()
            if not scope_id:
                continue
            key = (scope_kind, scope_id)
            if key in seen:
                continue
            seen.add(key)
            scopes.append(
                DirtyScope(
                    scope_kind=scope_kind,
                    scope_id=scope_id,
                    reason=str(entry.get("reason", "") or ""),
                    enqueued_at=entry.get("enqueued_at"),
                )
            )
        return cls(scopes=scopes)


def load_dirty_queue(paths: AtlasPaths) -> DirtyQueue:
    path = _dirty_queue_path(paths)
    if not path.is_file():
        return DirtyQueue()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DirtyQueue()
    return DirtyQueue.from_dict(payload)


def save_dirty_queue(paths: AtlasPaths, queue: DirtyQueue) -> None:
    atomic_write_json(_dirty_queue_path(paths), queue.to_dict())


def mark_dirty(
    paths: AtlasPaths,
    scope_id: str,
    *,
    scope_kind: str = "group",
    reason: str = "",
) -> None:
    """Append a scope to the dirty queue, deduping by (kind, id).

    Reason is best-effort metadata for the events log.
    """
    queue = load_dirty_queue(paths)
    key = (scope_kind, scope_id)
    if any(s.key() == key for s in queue.scopes):
        return
    queue.scopes.append(
        DirtyScope(
            scope_kind=scope_kind,
            scope_id=scope_id,
            reason=reason,
            enqueued_at=_now(),
        )
    )
    save_dirty_queue(paths, queue)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _record_event(paths: AtlasPaths, event: str, payload: dict[str, object]) -> None:
    target = _events_path(paths)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {"event": event, "at": _now(), **payload}, sort_keys=True
            )
            + "\n"
        )


@contextlib.contextmanager
def _scope_lock(paths: AtlasPaths, scope_id: str) -> Iterator[bool]:
    """Best-effort per-scope lock via O_EXCL file create.

    Returns a context that yields True on acquisition and False if the
    lock is already held. The lock file is removed on context exit.
    """
    lock_path = _scope_lock_path(paths, scope_id)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        yield False
        return
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield True
    finally:
        with contextlib.suppress(FileNotFoundError):
            lock_path.unlink()


def _mark_pointer_warming(paths: AtlasPaths, scope_kind: str, scope_id: str) -> None:
    pointer = load_latest_pointer(paths, scope_kind, scope_id)  # type: ignore[arg-type]
    if pointer is None:
        new_pointer = LatestPointer(
            scope_kind=scope_kind,  # type: ignore[arg-type]
            scope_id=scope_id,
            status="warming",
            warming=True,
            dirty=True,
            last_attempt_at=_now(),
            updated_at=_now(),
        )
    else:
        new_pointer = LatestPointer(
            scope_kind=pointer.scope_kind,
            scope_id=pointer.scope_id,
            status="warming",
            latest_complete_snapshot_key=pointer.latest_complete_snapshot_key,
            latest_fresh_snapshot_key=pointer.latest_fresh_snapshot_key,
            latest_attempted_snapshot_key=pointer.latest_attempted_snapshot_key,
            latest_fallback_snapshot_key=pointer.latest_fallback_snapshot_key,
            dirty=True,
            warming=True,
            last_attempt_at=_now(),
            updated_at=_now(),
            last_success_at=pointer.last_success_at,
            last_error=pointer.last_error,
        )
    write_latest_pointer(paths, new_pointer)


def _record_failure(
    paths: AtlasPaths, scope_kind: str, scope_id: str, error: str
) -> None:
    """Preserve the previous latest complete pointer; only update
    error/attempt fields. **Never** write a partial pointer.
    """
    pointer = load_latest_pointer(paths, scope_kind, scope_id)  # type: ignore[arg-type]
    if pointer is None:
        # No prior pointer; record an error pointer with no complete key.
        new_pointer = LatestPointer(
            scope_kind=scope_kind,  # type: ignore[arg-type]
            scope_id=scope_id,
            status="error",
            dirty=True,
            warming=False,
            last_attempt_at=_now(),
            last_error=error,
            updated_at=_now(),
        )
    else:
        new_pointer = LatestPointer(
            scope_kind=pointer.scope_kind,
            scope_id=pointer.scope_id,
            status="error",
            # ↓↓↓ THIS IS THE ABSOLUTE SAFETY CONSTRAINT — keep the
            # previous complete snapshot key.
            latest_complete_snapshot_key=pointer.latest_complete_snapshot_key,
            latest_fresh_snapshot_key=None,
            latest_attempted_snapshot_key=pointer.latest_attempted_snapshot_key,
            latest_fallback_snapshot_key=pointer.latest_fallback_snapshot_key,
            dirty=True,
            warming=False,
            last_attempt_at=_now(),
            updated_at=_now(),
            last_success_at=pointer.last_success_at,
            last_error=error,
        )
    write_latest_pointer(paths, new_pointer)


def tick(
    paths: AtlasPaths,
    *,
    max_scopes: int = DEFAULT_MAX_SCOPES_PER_TICK,
    prepare: Callable[..., object] | None = None,
) -> dict[str, object]:
    """Pop up to ``max_scopes`` dirty scopes and rebuild each.

    ``prepare`` is the snapshot builder to call per scope. In
    production it defaults to
    :func:`atlas_once.ranked_context.prepare_ranked_manifest`; tests
    inject a stub to verify orchestration without touching the legacy
    builder.
    """
    if prepare is None:
        from .ranked_context import prepare_ranked_manifest as default_prepare

        prepare = default_prepare

    queue = load_dirty_queue(paths)
    if not queue.scopes:
        return {"processed": [], "remaining": 0, "ticked_at": _now()}

    selected = queue.scopes[:max_scopes]
    remaining = queue.scopes[max_scopes:]
    processed: list[dict[str, object]] = []

    for scope in selected:
        with _scope_lock(paths, scope.scope_id) as acquired:
            if not acquired:
                # Re-queue at tail so we try again next tick.
                remaining.append(scope)
                processed.append(
                    {
                        "scope_kind": scope.scope_kind,
                        "scope_id": scope.scope_id,
                        "status": "skipped_locked",
                    }
                )
                _record_event(
                    paths,
                    "ranked_warmer.skipped_locked",
                    {"scope_id": scope.scope_id, "scope_kind": scope.scope_kind},
                )
                continue
            _mark_pointer_warming(paths, scope.scope_kind, scope.scope_id)
            try:
                prepare(paths, scope.scope_id)
                pointer = load_latest_pointer(paths, scope.scope_kind, scope.scope_id)  # type: ignore[arg-type]
                processed.append(
                    {
                        "scope_kind": scope.scope_kind,
                        "scope_id": scope.scope_id,
                        "status": "rebuilt",
                        "snapshot_key": (
                            pointer.latest_complete_snapshot_key
                            if pointer is not None
                            else None
                        ),
                    }
                )
                _record_event(
                    paths,
                    "ranked_warmer.rebuilt",
                    {
                        "scope_id": scope.scope_id,
                        "scope_kind": scope.scope_kind,
                        "snapshot_key": (
                            pointer.latest_complete_snapshot_key
                            if pointer is not None
                            else None
                        ),
                    },
                )
            except Exception as exc:  # noqa: BLE001 — surfaced verbatim into events
                error_text = f"{type(exc).__name__}: {exc}"
                _record_failure(paths, scope.scope_kind, scope.scope_id, error_text)
                processed.append(
                    {
                        "scope_kind": scope.scope_kind,
                        "scope_id": scope.scope_id,
                        "status": "failed",
                        "error": error_text,
                    }
                )
                _record_event(
                    paths,
                    "ranked_warmer.failed",
                    {
                        "scope_id": scope.scope_id,
                        "scope_kind": scope.scope_kind,
                        "error": error_text,
                    },
                )

    save_dirty_queue(paths, DirtyQueue(scopes=remaining))
    return {
        "processed": processed,
        "remaining": len(remaining),
        "ticked_at": _now(),
    }


def status_section(paths: AtlasPaths) -> dict[str, object]:
    """JSON payload folded into ``atlas index status data.tasks``."""
    queue = load_dirty_queue(paths)
    return {
        "enabled": True,
        "dirty_count": len(queue.scopes),
        "dirty": [
            {"scope_kind": s.scope_kind, "scope_id": s.scope_id, "reason": s.reason}
            for s in queue.scopes
        ],
    }


__all__ = [
    "DEFAULT_MAX_SCOPES_PER_TICK",
    "DirtyQueue",
    "DirtyScope",
    "load_dirty_queue",
    "mark_dirty",
    "save_dirty_queue",
    "status_section",
    "tick",
]
