from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import time
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import AtlasPaths, ensure_state
from .registry import load_registry, resolve_project_ref
from .runtime import append_event
from .shadow_workspace import ensure_shadow_project_root

INDEX_WATCHER_SCHEMA_VERSION = 1
DEFAULT_TTL_MS = 90_000
DEFAULT_POLL_INTERVAL_MS = 1_200
DEFAULT_DEBOUNCE_MS = 2_500
DEFAULT_REFRESH_WAIT_SECONDS = 0.1
DEFAULT_STOP_WAIT_SECONDS = 5.0
RETRY_DELAYS_MS = (2_000, 5_000, 15_000, 60_000, 300_000)
_SIGNAL_STOP_REQUESTED = False
_ACTIVE_INDEX_PROCESS: subprocess.Popen[str] | None = None


@dataclass(frozen=True)
class IndexWatchTarget:
    project_key: str
    project_ref: str
    project_path: Path


@dataclass
class IndexProjectState:
    project_key: str
    project_ref: str
    project_path: str
    status: str = "missing"
    in_flight: bool = False
    queued: bool = False
    queue_depth: int = 0
    queue_due_at: float | None = None
    last_file_mtime: float = 0.0
    retries: int = 0
    next_retry_at: float | None = None
    last_refresh_started_at: float | None = None
    last_refresh_finished_at: float | None = None
    last_error: str | None = None


@dataclass
class IndexWatcherState:
    schema_version: int = INDEX_WATCHER_SCHEMA_VERSION
    watcher_type: str = "poll"
    running: bool = False
    pid: int | None = None
    started_at: float | None = None
    heartbeat_at: float | None = None
    projects: dict[str, IndexProjectState] = field(default_factory=dict)
    stop_requested_at: float | None = None


@dataclass(frozen=True)
class IndexFreshness:
    project_key: str
    project_ref: str
    status: str
    age_ms: int
    wait_outcome: str
    waited_ms: int
    last_error: str | None
    last_refresh_started_at: float | None
    last_refresh_finished_at: float | None


def _coerce_str(value: Any, default: str | None = None) -> str | None:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _coerce_float(value: Any, default: float | None = None) -> float | None:
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: Any, default: int = 0) -> int:
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _fmt_ts(ts: float | None) -> str | None:
    if ts is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(ts))


def _fmt_mtime(ts: float | None) -> str | None:
    if ts is None or ts <= 0.0:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(ts))


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp_path.replace(path)


def _coerce_project_state(project_key: str, payload: dict[str, Any]) -> IndexProjectState:
    return IndexProjectState(
        project_key=_coerce_str(payload.get("project_key"), default=project_key) or project_key,
        project_ref=_coerce_str(payload.get("project_ref"), default=project_key) or project_key,
        project_path=_coerce_str(payload.get("project_path"), default="") or "",
        status=_coerce_str(payload.get("status"), default="missing") or "missing",
        in_flight=_coerce_bool(payload.get("in_flight")),
        queued=_coerce_bool(payload.get("queued")),
        queue_depth=_coerce_int(payload.get("queue_depth"), default=0),
        queue_due_at=_coerce_float(payload.get("queue_due_at")),
        last_file_mtime=_coerce_float(payload.get("last_file_mtime"), default=0.0) or 0.0,
        retries=_coerce_int(payload.get("retries"), default=0),
        next_retry_at=_coerce_float(payload.get("next_retry_at")),
        last_refresh_started_at=_coerce_float(payload.get("last_refresh_started_at")),
        last_refresh_finished_at=_coerce_float(payload.get("last_refresh_finished_at")),
        last_error=_coerce_str(payload.get("last_error"), default=None),
    )


def _coerce_state(payload: dict[str, Any]) -> IndexWatcherState:
    projects_payload = payload.get("projects", {})
    projects: dict[str, IndexProjectState] = {}
    if isinstance(projects_payload, dict):
        for key, value in projects_payload.items():
            if isinstance(value, dict):
                projects[str(key)] = _coerce_project_state(str(key), value)

    return IndexWatcherState(
        schema_version=_coerce_int(
            payload.get("schema_version"),
            default=INDEX_WATCHER_SCHEMA_VERSION,
        ),
        watcher_type=_coerce_str(payload.get("watcher_type"), default="poll") or "poll",
        running=_coerce_bool(payload.get("running")),
        pid=_coerce_int(payload.get("pid"), default=0) or None,
        started_at=_coerce_float(payload.get("started_at")),
        heartbeat_at=_coerce_float(payload.get("heartbeat_at")),
        stop_requested_at=_coerce_float(payload.get("stop_requested_at")),
        projects=projects,
    )


def _state_to_dict(state: IndexWatcherState) -> dict[str, Any]:
    return {
        "schema_version": state.schema_version,
        "watcher_type": state.watcher_type,
        "running": state.running,
        "pid": state.pid,
        "started_at": state.started_at,
        "heartbeat_at": state.heartbeat_at,
        "stop_requested_at": state.stop_requested_at,
        "projects": {key: asdict(value) for key, value in state.projects.items()},
    }


def _is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _record_state_recovered(paths: AtlasPaths, reason: str) -> None:
    payload = {
        "schema_version": "1.0",
        "ok": True,
        "command": "index.state_recovered",
        "exit_code": 0,
        "data": {"reason": reason},
        "errors": [],
    }
    with suppress(Exception):
        append_event(paths, "index.state_recovered", [], 0, payload)


def _request_signal_stop(signum: int, frame: Any) -> None:
    del signum, frame
    global _SIGNAL_STOP_REQUESTED
    _SIGNAL_STOP_REQUESTED = True
    process = _ACTIVE_INDEX_PROCESS
    if process is not None and process.poll() is None:
        _send_signal(process.pid, signal.SIGTERM)
        with suppress(OSError):
            process.terminate()


def _stop_requested(paths: AtlasPaths) -> bool:
    return _SIGNAL_STOP_REQUESTED or _watcher_stop_requested(paths)


def project_key_for_path(project_root: Path) -> str:
    digest = hashlib.sha256(str(project_root.resolve()).encode("utf-8")).hexdigest()[:12]
    safe_name = (
        "".join(
            character
            for character in project_root.name
            if character.isalnum() or character in {".", "-", "_"}
        )
        or "project"
    )
    return f"{safe_name}-{digest}"


def make_watch_target(project_root: Path, project_ref: str | None = None) -> IndexWatchTarget:
    normalized = project_root.resolve()
    return IndexWatchTarget(
        project_key=project_key_for_path(normalized),
        project_ref=project_ref or normalized.name,
        project_path=normalized,
    )


def _discover_mix_projects(record: Any) -> list[Path]:
    root = Path(record.path)
    entries = record.layout.get("mix_projects") if isinstance(record.layout, dict) else None
    if not isinstance(entries, list):
        return [root]

    projects: list[Path] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        rel_path = _coerce_str(entry.get("rel_path"), default=".")
        if not rel_path:
            rel_path = "."
        candidate = (root / rel_path).resolve()
        if candidate.is_dir() and (candidate / "mix.exs").is_file():
            projects.append(candidate)
    return projects or [root]


def _iter_candidate_project_roots(paths: AtlasPaths) -> list[IndexWatchTarget]:
    targets: list[IndexWatchTarget] = []
    seen: set[str] = set()
    for record in load_registry(paths):
        if not record.capabilities.get("elixir_ranked_v1", False):
            continue
        for project_path in _discover_mix_projects(record):
            target = make_watch_target(project_path, project_ref=record.name)
            if target.project_key in seen:
                continue
            seen.add(target.project_key)
            targets.append(target)
    return targets


def resolve_watch_targets(
    paths: AtlasPaths,
    project_selectors: list[str] | None = None,
    *,
    strict: bool = False,
) -> list[IndexWatchTarget]:
    if not project_selectors:
        return _iter_candidate_project_roots(paths)

    discovered: list[IndexWatchTarget] = []
    seen: set[str] = set()
    for raw in project_selectors:
        candidate = Path(raw).expanduser()
        if candidate.exists():
            root = candidate.resolve()
            if root.is_file():
                root = root.parent
            if not root.is_dir() and strict:
                raise SystemExit(f"Path does not exist: {raw}")
            if not root.is_dir():
                continue
            if not (root / "mix.exs").is_file():
                if strict:
                    raise SystemExit(f"Not a mix project path: {raw}")
                continue
            target = make_watch_target(root)
            if target.project_key not in seen:
                seen.add(target.project_key)
                discovered.append(target)
            continue

        try:
            record = resolve_project_ref(paths, raw)
        except SystemExit:
            if strict:
                raise
            continue
        for project_path in _discover_mix_projects(record):
            target = make_watch_target(project_path, project_ref=record.name)
            if target.project_key in seen:
                continue
            seen.add(target.project_key)
            discovered.append(target)

    if strict and not discovered:
        raise SystemExit(f"No index targets matched: {', '.join(project_selectors)}")
    return discovered


def load_state(paths: AtlasPaths) -> tuple[IndexWatcherState, bool]:
    ensure_state(paths)
    if not paths.index_watcher_state_path.is_file():
        return IndexWatcherState(), True
    try:
        payload = json.loads(paths.index_watcher_state_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("index watcher state must be JSON object")
        state = _coerce_state(payload)
        stale_pid = state.pid
        if (
            state.running
            and stale_pid is not None
            and stale_pid != os.getpid()
            and not _is_alive(stale_pid)
        ):
            state.running = False
            state.pid = None
            state.stop_requested_at = None
            _record_state_recovered(paths, "stale_pid")
        return state, payload.get("schema_version", None) != INDEX_WATCHER_SCHEMA_VERSION
    except (OSError, json.JSONDecodeError, ValueError):
        _record_state_recovered(paths, "malformed_state")
        return IndexWatcherState(), True


def save_state(paths: AtlasPaths, state: IndexWatcherState) -> None:
    _atomic_write_json(paths.index_watcher_state_path, _state_to_dict(state))


def _set_project_entry(state: IndexWatcherState, target: IndexWatchTarget) -> IndexProjectState:
    entry = state.projects.get(target.project_key)
    if entry is None:
        entry = IndexProjectState(
            project_key=target.project_key,
            project_ref=target.project_ref,
            project_path=str(target.project_path),
        )
        state.projects[target.project_key] = entry
        return entry
    if entry.project_ref != target.project_ref:
        entry.project_ref = target.project_ref
    if not entry.project_path:
        entry.project_path = str(target.project_path)
    return entry


def _project_snapshot_mtime(project_root: Path) -> float:
    latest = 0.0
    if not project_root.is_dir():
        return latest

    for current, dirnames, filenames in os.walk(project_root):
        dirnames[:] = [
            directory
            for directory in sorted(dirnames)
            if directory
            not in {
                ".git",
                "_build",
                "deps",
                "tmp",
                "node_modules",
                "dist",
                ".elixir_ls",
                ".vscode",
            }
        ]
        for filename in sorted(filenames):
            if filename == "mix.exs" or filename.endswith(".ex") or filename.endswith(".exs"):
                path = Path(current) / filename
                if path.is_file():
                    latest = max(latest, path.stat().st_mtime)
    return latest


def _snapshot_status(entry: IndexProjectState, *, now: float, ttl_ms: int) -> str:
    if entry.in_flight:
        return "warming"
    if entry.last_refresh_finished_at is None:
        return "missing"
    if entry.last_error is not None and entry.last_refresh_finished_at < entry.last_file_mtime:
        return "error"
    age_ms = (now - entry.last_refresh_finished_at) * 1000.0
    if age_ms > ttl_ms:
        return "stale"
    if entry.last_refresh_finished_at < entry.last_file_mtime:
        return "stale"
    return "fresh"


def _fresh_age_ms(now: float, entry: IndexProjectState) -> int:
    if entry.last_refresh_finished_at is None:
        return 2**31 - 1
    return max(0, int((now - entry.last_refresh_finished_at) * 1000.0))


def _freshness_payload(
    entry: IndexProjectState,
    *,
    now: float,
    ttl_ms: int,
    waited_ms: int,
    wait_outcome: str,
) -> IndexFreshness:
    return IndexFreshness(
        project_key=entry.project_key,
        project_ref=entry.project_ref,
        status=_snapshot_status(entry, now=now, ttl_ms=ttl_ms),
        age_ms=_fresh_age_ms(now, entry),
        wait_outcome=wait_outcome,
        waited_ms=waited_ms,
        last_error=entry.last_error,
        last_refresh_started_at=entry.last_refresh_started_at,
        last_refresh_finished_at=entry.last_refresh_finished_at,
    )


def _retry_delay_for_entry(entry: IndexProjectState) -> float | None:
    if entry.retries <= 0:
        return None
    index = min(entry.retries - 1, len(RETRY_DELAYS_MS) - 1)
    return RETRY_DELAYS_MS[index] / 1000.0


def _write_pid_hint(paths: AtlasPaths, state: IndexWatcherState) -> IndexWatcherState:
    if state.pid is None:
        if paths.index_watcher_pid_path.is_file():
            with suppress(OSError):
                paths.index_watcher_pid_path.unlink()
        return state
    paths.index_watcher_pid_path.write_text(str(state.pid), encoding="utf-8")
    return state


def _watcher_stop_requested(paths: AtlasPaths) -> bool:
    return paths.index_watcher_stop_path.is_file()


def _send_signal(pid: int, signum: int) -> bool:
    with suppress(OSError):
        pgid = os.getpgid(pid)
        if pgid > 0 and pgid != os.getpgrp():
            os.killpg(pgid, signum)
            return True
    try:
        os.kill(pid, signum)
        return True
    except OSError:
        return False


def _write_stop_signal(paths: AtlasPaths, requested_at: float | None) -> None:
    payload = {"requested_at": _fmt_ts(requested_at)}
    _atomic_write_json(paths.index_watcher_stop_path, payload)


def _clear_stop_signal(paths: AtlasPaths) -> None:
    if paths.index_watcher_stop_path.is_file():
        with suppress(OSError):
            paths.index_watcher_stop_path.unlink()


def _mark_refresh_result(
    state: IndexWatcherState,
    target: IndexWatchTarget,
    *,
    started_at: float,
    finished_at: float,
    return_code: int,
    error: str | None = None,
) -> bool:
    entry = _set_project_entry(state, target)
    entry.in_flight = False
    entry.last_refresh_started_at = started_at
    entry.last_refresh_finished_at = finished_at
    entry.last_file_mtime = _project_snapshot_mtime(target.project_path)
    entry.queued = False
    entry.queue_due_at = None

    if return_code == 0:
        entry.status = "fresh"
        entry.queue_depth = 0
        entry.retries = 0
        entry.next_retry_at = None
        entry.last_error = None
        return True

    entry.status = "error"
    entry.retries += 1
    entry.last_error = error or "index refresh failed"
    delay = _retry_delay_for_entry(entry)
    entry.next_retry_at = (finished_at + delay) if delay is not None else None
    return False


def run_index(
    project_root: Path,
    *,
    dexterity_root: Path,
    shadow_root: Path,
    dexter_bin: str = "dexter",
) -> subprocess.CompletedProcess[str]:
    global _ACTIVE_INDEX_PROCESS
    shadow_project_root = ensure_shadow_project_root(project_root, shadow_root)
    command = [
        "mix",
        "dexterity.index",
        "--repo-root",
        str(shadow_project_root),
        "--dexter-bin",
        dexter_bin,
    ]
    process = subprocess.Popen(
        command,
        cwd=str(dexterity_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=os.environ.copy(),
        start_new_session=True,
    )
    _ACTIVE_INDEX_PROCESS = process
    try:
        stdout, stderr = process.communicate()
        return subprocess.CompletedProcess(
            command,
            process.returncode,
            stdout,
            stderr,
        )
    finally:
        if _ACTIVE_INDEX_PROCESS is process:
            _ACTIVE_INDEX_PROCESS = None


def _execute_refresh_once(
    state: IndexWatcherState,
    target: IndexWatchTarget,
    *,
    dexterity_root: Path,
    dexter_bin: str,
    shadow_root: Path,
) -> bool:
    entry = _set_project_entry(state, target)
    if entry.in_flight:
        return False

    entry.in_flight = True
    entry.status = "warming"
    entry.last_error = None

    started_at = time.time()
    completed = run_index(
        target.project_path,
        dexterity_root=dexterity_root,
        shadow_root=shadow_root,
        dexter_bin=dexter_bin,
    )
    finished_at = time.time()
    return _mark_refresh_result(
        state,
        target,
        started_at=started_at,
        finished_at=finished_at,
        return_code=completed.returncode,
        error=(completed.stderr.strip() or completed.stdout.strip()),
    )


def _can_refresh_now(entry: IndexProjectState, *, now: float, require_queued: bool) -> bool:
    if entry.in_flight:
        return False
    if entry.next_retry_at is not None and entry.next_retry_at > now:
        return False
    if require_queued and not entry.queued:
        return False
    return not (entry.queue_due_at is not None and entry.queue_due_at > now)


def _mark_file_mtime(entry: IndexProjectState, project_root: Path) -> None:
    entry.last_file_mtime = _project_snapshot_mtime(project_root)


def _refresh_file_mtimes(
    state: IndexWatcherState,
    targets: list[IndexWatchTarget],
    *,
    now: float,
    debounce_ms: int,
) -> None:
    debounce_seconds = max(0.0, debounce_ms / 1000.0)
    for target in targets:
        entry = _set_project_entry(state, target)
        current_mtime = _project_snapshot_mtime(target.project_path)
        if current_mtime <= entry.last_file_mtime:
            continue

        entry.last_file_mtime = current_mtime
        if not entry.queued:
            entry.queue_depth += 1
        entry.queued = True
        entry.status = "stale"
        entry.queue_due_at = now + debounce_seconds if entry.queue_due_at is None else max(
            entry.queue_due_at, now + debounce_seconds
        )


def _drain_stale_inflight(state: IndexWatcherState) -> None:
    for entry in state.projects.values():
        if entry.in_flight:
            continue
        if not entry.queued:
            entry.queue_due_at = None


def _merge_newer_persisted_refreshes(paths: AtlasPaths, state: IndexWatcherState) -> None:
    persisted, _ = load_state(paths)
    for key, persisted_entry in persisted.projects.items():
        current = state.projects.get(key)
        if current is None:
            state.projects[key] = persisted_entry
            continue
        if current.in_flight:
            continue
        persisted_finished = persisted_entry.last_refresh_finished_at or 0.0
        current_finished = current.last_refresh_finished_at or 0.0
        if persisted_finished > current_finished:
            state.projects[key] = persisted_entry


def _run_cycle(
    paths: AtlasPaths,
    state: IndexWatcherState,
    targets: list[IndexWatchTarget],
    *,
    poll_interval_ms: int,
    debounce_ms: int,
    dexterity_root: Path,
    dexter_bin: str,
    shadow_root: Path,
) -> IndexWatcherState:
    if state.running:
        state.heartbeat_at = time.time()

    now = time.time()
    _refresh_file_mtimes(state, targets, now=now, debounce_ms=debounce_ms)

    now = time.time()
    for target in targets:
        entry = state.projects[target.project_key]
        if not _can_refresh_now(entry, now=now, require_queued=True):
            continue
        _execute_refresh_once(
            state=state,
            target=target,
            dexterity_root=dexterity_root,
            dexter_bin=dexter_bin,
            shadow_root=shadow_root,
        )

    state.heartbeat_at = time.time()
    if _stop_requested(paths):
        state.running = False

    _drain_stale_inflight(state)
    _merge_newer_persisted_refreshes(paths, state)
    save_state(paths, _write_pid_hint(paths, state))

    if state.running and poll_interval_ms > 0 and not _stop_requested(paths):
        time.sleep(max(0.0, poll_interval_ms / 1000.0))
    return state


def _clear_state_stale_process(
    paths: AtlasPaths,
    state: IndexWatcherState,
    *,
    current_pid: int,
) -> None:
    if state.running and state.pid and state.pid != current_pid and not _is_alive(state.pid):
        state.running = False
        state.pid = None
        state.stop_requested_at = None
        _clear_stop_signal(paths)


def start_watch(
    paths: AtlasPaths,
    targets: list[IndexWatchTarget],
    *,
    dexterity_root: Path,
    dexter_bin: str,
    shadow_root: Path,
    daemon: bool = False,
    poll_interval_ms: int = DEFAULT_POLL_INTERVAL_MS,
    debounce_ms: int = DEFAULT_DEBOUNCE_MS,
    ttl_ms: int = DEFAULT_TTL_MS,
    once: bool = False,
) -> IndexWatcherState:
    del ttl_ms

    global _SIGNAL_STOP_REQUESTED
    _SIGNAL_STOP_REQUESTED = False
    old_sigterm = signal.getsignal(signal.SIGTERM)
    old_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGTERM, _request_signal_stop)
    signal.signal(signal.SIGINT, _request_signal_stop)
    try:
        if not targets:
            state = IndexWatcherState(running=False)
            save_state(paths, _write_pid_hint(paths, state))
            return state

        state, _ = load_state(paths)
        _clear_state_stale_process(paths, state, current_pid=os.getpid())
        if watcher_is_active(state) and state.pid != os.getpid():
            return state
        state.running = True
        state.watcher_type = "poll"
        state.pid = os.getpid()
        state.started_at = time.time()
        state.heartbeat_at = state.started_at
        state.stop_requested_at = None

        now = time.time()
        for target in targets:
            entry = _set_project_entry(state, target)
            if not entry.project_path:
                entry.project_path = str(target.project_path)
            if entry.last_file_mtime <= 0.0:
                entry.last_file_mtime = 0.0
            if entry.last_refresh_finished_at is None and not entry.queued:
                entry.queued = True
                entry.queue_depth += 1
                entry.queue_due_at = now
                entry.status = "missing"

        save_state(paths, _write_pid_hint(paths, state))
        _clear_stop_signal(paths)

        cycles_left: int | None
        if once:
            cycles_left = 1
        elif daemon:
            cycles_left = None
        else:
            cycles_left = 1

        while state.running and (cycles_left is None or cycles_left > 0):
            state = _run_cycle(
                paths=paths,
                state=state,
                targets=targets,
                poll_interval_ms=poll_interval_ms,
                debounce_ms=debounce_ms,
                dexterity_root=dexterity_root,
                dexter_bin=dexter_bin,
                shadow_root=shadow_root,
            )
            if cycles_left is not None:
                cycles_left -= 1

            if cycles_left is not None and cycles_left <= 0:
                state.running = False
            if _stop_requested(paths):
                break

        if state.pid == os.getpid():
            state.pid = None
        if state.running:
            state.running = False
        state.started_at = None
        state.stop_requested_at = None
        _clear_stop_signal(paths)
        save_state(paths, _write_pid_hint(paths, state))
        return state
    finally:
        signal.signal(signal.SIGTERM, old_sigterm)
        signal.signal(signal.SIGINT, old_sigint)


def refresh_projects(
    paths: AtlasPaths,
    targets: list[IndexWatchTarget],
    *,
    dexterity_root: Path,
    dexter_bin: str,
    shadow_root: Path,
) -> IndexWatcherState:
    state, _ = load_state(paths)
    if not targets:
        save_state(paths, _write_pid_hint(paths, state))
        return state

    now = time.time()
    for target in targets:
        entry = _set_project_entry(state, target)
        entry.queued = True
        entry.queue_due_at = now
        entry.queue_depth += 1

    return _run_cycle(
        paths=paths,
        state=state,
        targets=targets,
        poll_interval_ms=0,
        debounce_ms=0,
        dexterity_root=dexterity_root,
        dexter_bin=dexter_bin,
        shadow_root=shadow_root,
    )


def ensure_project_freshness(
    paths: AtlasPaths,
    target: IndexWatchTarget,
    *,
    ttl_ms: int,
    wait_fresh_ms: int = 0,
    now: float | None = None,
    dexterity_root: Path,
    dexter_bin: str = "dexter",
    shadow_root: Path,
    allow_stale: bool = True,
) -> tuple[IndexFreshness, bool]:
    state, _ = load_state(paths)
    entry = _set_project_entry(state, target)
    _mark_file_mtime(entry, target.project_path)
    start = now if now is not None else time.time()
    status = _snapshot_status(entry, now=start, ttl_ms=ttl_ms)
    if status == "fresh":
        save_state(paths, _write_pid_hint(paths, state))
        return _freshness_payload(
            entry,
            now=start,
            ttl_ms=ttl_ms,
            waited_ms=0,
            wait_outcome="fresh",
        ), False

    if (
        not allow_stale
        and not entry.in_flight
        and _can_refresh_now(entry, now=start, require_queued=False)
    ):
        _execute_refresh_once(
            state,
            target,
            dexterity_root=dexterity_root,
            dexter_bin=dexter_bin,
            shadow_root=shadow_root,
        )
        save_state(paths, _write_pid_hint(paths, state))

    if wait_fresh_ms <= 0:
        save_state(paths, _write_pid_hint(paths, state))
        return _freshness_payload(
            entry,
            now=time.time(),
            ttl_ms=ttl_ms,
            waited_ms=0,
            wait_outcome="not_waited",
        ), False

    deadline = start + (wait_fresh_ms / 1000.0)
    while time.time() < deadline:
        time.sleep(min(DEFAULT_REFRESH_WAIT_SECONDS, max(0.0, deadline - time.time())))
        state, _ = load_state(paths)
        entry = state.projects[target.project_key]
        status = _snapshot_status(entry, now=time.time(), ttl_ms=ttl_ms)
        if status == "fresh":
            break
        if (
            not entry.in_flight
            and _can_refresh_now(entry, now=time.time(), require_queued=False)
        ):
            _execute_refresh_once(
                state,
                target,
                dexterity_root=dexterity_root,
                dexter_bin=dexter_bin,
                shadow_root=shadow_root,
            )
            save_state(paths, _write_pid_hint(paths, state))

    now = time.time()
    status = _snapshot_status(entry, now=now, ttl_ms=ttl_ms)
    outcome = "waited" if status == "fresh" else "timeout"
    waited_ms = max(0, int((now - start) * 1000))
    return _freshness_payload(
        entry,
        now=now,
        ttl_ms=ttl_ms,
        waited_ms=waited_ms,
        wait_outcome=outcome,
    ), True


def ensure_index_freshness_records(
    paths: AtlasPaths,
    targets: list[IndexWatchTarget],
    *,
    ttl_ms: int = DEFAULT_TTL_MS,
    wait_fresh_ms: int = 0,
    dexterity_root: Path,
    dexter_bin: str,
    shadow_root: Path,
    allow_stale: bool = True,
) -> tuple[list[IndexFreshness], IndexWatcherState]:
    records: list[IndexFreshness] = []
    state: IndexWatcherState | None = None
    for target in targets:
        record, _ = ensure_project_freshness(
            paths=paths,
            target=target,
            ttl_ms=ttl_ms,
            wait_fresh_ms=wait_fresh_ms,
            dexterity_root=dexterity_root,
            dexter_bin=dexter_bin,
            shadow_root=shadow_root,
            allow_stale=allow_stale,
        )
        records.append(record)
    state, _ = load_state(paths)
    return records, state


def status_payload(
    paths: AtlasPaths,
    *,
    ttl_ms: int,
    targets: list[IndexWatchTarget] | None = None,
) -> dict[str, Any]:
    state, _ = load_state(paths)

    if targets is None:
        targets = _iter_candidate_project_roots(paths)

    for target in targets:
        entry = _set_project_entry(state, target)
        _mark_file_mtime(entry, target.project_path)

    now = time.time()
    summary = {
        "projects_total": len(targets),
        "fresh": 0,
        "warming": 0,
        "stale": 0,
        "error": 0,
    }

    project_payloads: list[dict[str, Any]] = []
    for target in sorted(targets, key=lambda item: item.project_key):
        entry = state.projects[target.project_key]
        entry_status = _snapshot_status(entry, now=now, ttl_ms=ttl_ms)
        if entry_status == "fresh":
            summary["fresh"] += 1
        elif entry_status == "warming":
            summary["warming"] += 1
        elif entry_status == "error":
            summary["error"] += 1
        else:
            summary["stale"] += 1

        project_payloads.append(
            {
                "project_key": entry.project_key,
                "project_ref": entry.project_ref,
                "project_path": entry.project_path,
                "status": entry_status,
                "age_ms": _fresh_age_ms(now, entry),
                "in_flight": entry.in_flight,
                "queued": entry.queued,
                "queue_depth": entry.queue_depth,
                "retry_count": entry.retries,
                "last_error": entry.last_error,
                "last_refresh_started_at": _fmt_ts(entry.last_refresh_started_at),
                "last_refresh_finished_at": _fmt_ts(entry.last_refresh_finished_at),
                "next_retry_at": _fmt_ts(entry.next_retry_at),
                "queue_due_at": _fmt_ts(entry.queue_due_at),
                "last_file_mtime": _fmt_mtime(entry.last_file_mtime),
            }
        )

    state.heartbeat_at = time.time()
    global_queue_depth = sum(entry.queue_depth for entry in state.projects.values())
    save_state(paths, _write_pid_hint(paths, state))
    return {
        "daemon": {
            "running": watcher_is_active(state),
            "pid": state.pid,
            "watcher_type": state.watcher_type,
            "started_at": _fmt_ts(state.started_at),
            "heartbeat_at": _fmt_ts(state.heartbeat_at),
            "stop_requested_at": _fmt_ts(state.stop_requested_at),
        },
        "summary": summary,
        "projects": project_payloads,
        "global_queue_depth": global_queue_depth,
        "generated_at": _fmt_ts(now),
    }


def watcher_is_active(state: IndexWatcherState) -> bool:
    return bool(state.running and state.pid and _is_alive(state.pid))


def stop_watch(paths: AtlasPaths, *, force: bool = False) -> dict[str, Any]:
    state, _ = load_state(paths)
    state.stop_requested_at = time.time()
    stop_requested_at = state.stop_requested_at
    signal_sent = False
    force_escalated = False
    stopped = False
    pid = state.pid

    _write_stop_signal(paths, stop_requested_at)
    if pid and _is_alive(pid):
        signal_sent = _send_signal(pid, signal.SIGKILL if force else signal.SIGTERM)

    if pid and not force:
        deadline = time.time() + DEFAULT_STOP_WAIT_SECONDS
        while time.time() < deadline and _is_alive(pid):
            time.sleep(0.05)
        state, _ = load_state(paths)
        if not _is_alive(pid):
            stopped = True
            state.running = False
            state.pid = None
            state.started_at = None
            state.stop_requested_at = None
            _clear_stop_signal(paths)
        else:
            force_escalated = _send_signal(pid, signal.SIGKILL)
            deadline = time.time() + DEFAULT_STOP_WAIT_SECONDS
            while time.time() < deadline and _is_alive(pid):
                time.sleep(0.05)
            state, _ = load_state(paths)
            if not _is_alive(pid):
                stopped = True
                state.running = False
                state.pid = None
                state.started_at = None
                state.stop_requested_at = None
                state.heartbeat_at = None
                _clear_stop_signal(paths)
            else:
                stopped = False

    if force:
        stopped = True
        state.running = False
        state.pid = None
        state.started_at = None
        state.stop_requested_at = None
        state.heartbeat_at = None

    save_state(paths, _write_pid_hint(paths, state))
    if force:
        _clear_stop_signal(paths)

    return {
        "requested_at": _fmt_ts(stop_requested_at),
        "stopped": stopped,
        "signal_sent": signal_sent,
        "force_escalated": force_escalated,
        "force": force,
        "pid": state.pid,
        "running": watcher_is_active(state),
    }
