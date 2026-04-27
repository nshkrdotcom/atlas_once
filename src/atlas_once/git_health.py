from __future__ import annotations

import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import AtlasPaths, ensure_state
from .fleet import (
    RepoModel,
    canonical_repo_path,
    git_health_config,
    load_repos,
    select_repos,
)


@dataclass(frozen=True)
class GitStatusRecord:
    repo_ref: str
    path: str
    exists: bool
    is_git_repo: bool
    branch: str | None = None
    head: str | None = None
    upstream: str | None = None
    upstream_missing: bool = False
    ahead: int = 0
    behind: int = 0
    diverged: bool = False
    working_dirty: bool = False
    index_dirty: bool = False
    untracked_count: int = 0
    conflicted: bool = False
    stashed: bool = False
    submodules: list[dict[str, Any]] = field(default_factory=list)
    cached: bool = False
    stale: bool = False
    age_ms: int = 0
    last_checked: str | None = None
    refresh_duration_ms: int = 0
    signature: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def read_cache(paths: AtlasPaths) -> dict[str, Any]:
    ensure_state(paths)
    if not paths.git_health_latest_path.is_file():
        return {"timestamp": None, "repos": []}
    try:
        payload = json.loads(paths.git_health_latest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"timestamp": None, "repos": [], "errors": [{"kind": "invalid_cache_json"}]}
    return payload if isinstance(payload, dict) else {"timestamp": None, "repos": []}


def health_by_path(paths: AtlasPaths) -> dict[str, dict[str, Any]]:
    payload = read_cache(paths)
    repos = payload.get("repos", [])
    if not isinstance(repos, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in repos:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        if path:
            result[canonical_repo_path(RepoModel(ref="", path=str(path)))] = item
    return result


def refresh_git_health(
    paths: AtlasPaths,
    repos: list[RepoModel],
    *,
    max_workers: int | None = None,
    timeout_seconds: float | None = None,
    stale_after_ms: int | None = None,
    source: str = "foreground_refresh",
) -> dict[str, Any]:
    config = git_health_config(paths)
    max_workers = max_workers or int(config.get("max_workers") or 6)
    timeout_seconds = timeout_seconds or float(config.get("command_timeout_seconds") or 10)
    stale_after_ms = stale_after_ms or int(config.get("stale_after_ms") or 30000)
    started = time.monotonic()
    cache = health_by_path(paths)
    records: list[GitStatusRecord] = []
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        futures = [
            executor.submit(
                _probe_repo,
                repo,
                cache.get(canonical_repo_path(repo)),
                timeout_seconds,
            )
            for repo in repos
        ]
        for future in as_completed(futures):
            records.append(future.result())
    payload = _payload_from_records(records, source=source, stale_after_ms=stale_after_ms)
    payload["refresh_duration_ms"] = int((time.monotonic() - started) * 1000)
    _atomic_write_json(paths.git_health_latest_path, payload)
    _append_jsonl(
        paths.git_health_events_path,
        {
            "timestamp": _now_iso(),
            "event": "git_health.tick",
            "source": source,
            "repo_count": payload["repo_count"],
            "dirty_count": payload["dirty_count"],
            "stale_count": payload["stale_count"],
            "duration_ms": payload["refresh_duration_ms"],
        },
    )
    return payload


def status_for_selectors(
    paths: AtlasPaths,
    selectors: list[str] | None,
    *,
    manifest: str | None = None,
    manifest_format: str = "json",
    refresh: bool = False,
    stale_after_ms: int | None = None,
    timeout_seconds: float | None = None,
    max_workers: int | None = None,
) -> dict[str, Any]:
    config = git_health_config(paths)
    stale_after_ms = stale_after_ms or int(config.get("stale_after_ms") or 30000)
    repos = load_repos(paths, manifest=manifest, manifest_format=manifest_format)
    cache_by_path = health_by_path(paths)
    selection = select_repos(repos, selectors, health_by_path=cache_by_path)
    if refresh or not paths.git_health_latest_path.exists():
        payload = refresh_git_health(
            paths,
            selection.repos,
            max_workers=max_workers,
            timeout_seconds=timeout_seconds,
            stale_after_ms=stale_after_ms,
            source="foreground_refresh",
        )
    else:
        records = []
        now = time.time()
        for repo in selection.repos:
            cached = cache_by_path.get(canonical_repo_path(repo))
            if cached is None:
                records.append(asdict(_missing_cache_record(repo)))
                continue
            records.append(_mark_cached_staleness(cached, now, stale_after_ms))
        payload = _payload_from_dict_records(records, source="cache", stale_after_ms=stale_after_ms)
    payload["selector"] = ",".join(selection.selector)
    payload["selector_errors"] = selection.errors
    payload["background"] = background_status(paths)
    return payload


def background_status(paths: AtlasPaths) -> dict[str, Any]:
    payload = read_cache(paths)
    return {
        "enabled": bool(git_health_config(paths).get("background_enabled", True)),
        "task": "git_health",
        "last_tick_at": payload.get("timestamp"),
        "repo_count": int(payload.get("repo_count") or 0),
        "dirty_count": int(payload.get("dirty_count") or 0),
        "stale_count": int(payload.get("stale_count") or 0),
        "last_error": _last_cache_error(payload),
        "fresh": int(payload.get("stale_count") or 0) == 0 if payload.get("timestamp") else False,
    }


def run_background_tick(paths: AtlasPaths) -> dict[str, Any]:
    config = git_health_config(paths)
    if not config.get("enabled", True) or not config.get("background_enabled", True):
        return {"enabled": False, "task": "git_health", "skipped": True}
    repos = load_repos(paths)
    return refresh_git_health(
        paths,
        repos,
        max_workers=int(config.get("max_workers") or 6),
        timeout_seconds=float(config.get("command_timeout_seconds") or 10),
        stale_after_ms=int(config.get("stale_after_ms") or 30000),
        source="background_worker",
    )


def _payload_from_records(
    records: list[GitStatusRecord],
    *,
    source: str,
    stale_after_ms: int,
) -> dict[str, Any]:
    return _payload_from_dict_records(
        [asdict(record) for record in records],
        source=source,
        stale_after_ms=stale_after_ms,
    )


def _payload_from_dict_records(
    records: list[dict[str, Any]],
    *,
    source: str,
    stale_after_ms: int,
) -> dict[str, Any]:
    now = time.time()
    repos = [_mark_cached_staleness(record, now, stale_after_ms) for record in records]
    repos = sorted(
        repos,
        key=lambda item: (
            str(item.get("repo_ref", "")).lower(),
            str(item.get("path", "")),
        ),
    )
    return {
        "timestamp": _now_iso(),
        "source": source,
        "repo_count": len(repos),
        "dirty_count": sum(1 for item in repos if _is_dirty(item)),
        "unpushed_count": sum(1 for item in repos if int(item.get("ahead") or 0) > 0),
        "stale_count": sum(1 for item in repos if item.get("stale")),
        "repos": repos,
    }


def _missing_cache_record(repo: RepoModel) -> GitStatusRecord:
    return GitStatusRecord(
        repo_ref=repo.ref,
        path=canonical_repo_path(repo),
        exists=Path(repo.path).exists(),
        is_git_repo=False,
        cached=False,
        stale=True,
        errors=[{"kind": "missing_cache", "message": "No cached git-health record."}],
    )


def _probe_repo(
    repo: RepoModel,
    cached: dict[str, Any] | None,
    timeout_seconds: float,
) -> GitStatusRecord:
    started = time.monotonic()
    path = Path(repo.path).expanduser()
    canonical = canonical_repo_path(repo)
    if not path.exists():
        return _record_error(repo, canonical, started, "missing_path", "Repo path does not exist.")
    git_dir = _resolve_git_dir(path)
    if git_dir is None:
        return _record_error(repo, canonical, started, "not_git_repo", "Path is not a git repo.")
    signature = _git_signature(git_dir)
    if cached and cached.get("signature") == signature:
        cached_record = dict(cached)
        cached_record["cached"] = True
        cached_record["last_checked"] = _now_iso()
        cached_record["refresh_duration_ms"] = int((time.monotonic() - started) * 1000)
        fields = GitStatusRecord.__dataclass_fields__
        return GitStatusRecord(
            **{key: cached_record[key] for key in fields if key in cached_record}
        )
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(path),
                "status",
                "--porcelain=v2",
                "--branch",
                "--untracked-files=normal",
                "--ahead-behind",
            ],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _record_error(repo, canonical, started, "git_timeout", "git status timed out.")
    if result.returncode != 0:
        return _record_error(repo, canonical, started, "git_status_failed", result.stderr.strip())
    record = _parse_porcelain(repo, canonical, result.stdout)
    return GitStatusRecord(
        **{
            **asdict(record),
            "signature": signature,
            "last_checked": _now_iso(),
            "refresh_duration_ms": int((time.monotonic() - started) * 1000),
        }
    )


def _record_error(
    repo: RepoModel,
    path: str,
    started: float,
    kind: str,
    message: str,
) -> GitStatusRecord:
    return GitStatusRecord(
        repo_ref=repo.ref,
        path=path,
        exists=Path(path).exists(),
        is_git_repo=False,
        last_checked=_now_iso(),
        refresh_duration_ms=int((time.monotonic() - started) * 1000),
        errors=[{"kind": kind, "message": message}],
    )


def _resolve_git_dir(repo_path: Path) -> Path | None:
    dotgit = repo_path / ".git"
    if dotgit.is_dir():
        return dotgit
    if dotgit.is_file():
        text = dotgit.read_text(encoding="utf-8", errors="replace").strip()
        if text.startswith("gitdir:"):
            gitdir = Path(text.split(":", 1)[1].strip())
            if not gitdir.is_absolute():
                gitdir = repo_path / gitdir
            return gitdir.resolve(strict=False) if gitdir.exists() else None
    return None


def _git_signature(git_dir: Path) -> dict[str, Any]:
    signature: dict[str, Any] = {}
    for name in ("HEAD", "index", "FETCH_HEAD", "logs/HEAD", "packed-refs"):
        path = git_dir / name
        if not path.exists():
            continue
        stat = path.stat()
        key = name.replace("/", "_").lower()
        signature[f"{key}_mtime_ns"] = stat.st_mtime_ns
        signature[f"{key}_size"] = stat.st_size
        if name == "HEAD":
            signature["head_text"] = path.read_text(encoding="utf-8", errors="replace").strip()
    return signature


def _parse_porcelain(repo: RepoModel, path: str, stdout: str) -> GitStatusRecord:
    branch: str | None = None
    head: str | None = None
    upstream: str | None = None
    ahead = 0
    behind = 0
    working_dirty = False
    index_dirty = False
    untracked_count = 0
    conflicted = False
    stashed = False
    for line in stdout.splitlines():
        if line.startswith("# branch.head "):
            branch = line.removeprefix("# branch.head ").strip()
        elif line.startswith("# branch.oid "):
            head = line.removeprefix("# branch.oid ").strip()
        elif line.startswith("# branch.upstream "):
            upstream = line.removeprefix("# branch.upstream ").strip()
        elif line.startswith("# branch.ab "):
            parts = line.split()
            for part in parts:
                if part.startswith("+"):
                    ahead = int(part[1:])
                elif part.startswith("-"):
                    behind = int(part[1:])
        elif line.startswith("1 ") or line.startswith("2 "):
            parts = line.split()
            xy = parts[1] if len(parts) > 1 else ".."
            index_dirty = index_dirty or xy[0] not in {".", " "}
            working_dirty = working_dirty or xy[1] not in {".", " "}
        elif line.startswith("? "):
            untracked_count += 1
        elif line.startswith("u "):
            conflicted = True
            index_dirty = True
            working_dirty = True
        elif line.startswith("! "):
            continue
    if branch == "(detached)":
        upstream = upstream or None
    return GitStatusRecord(
        repo_ref=repo.ref,
        path=path,
        exists=True,
        is_git_repo=True,
        branch=branch,
        head=head,
        upstream=upstream,
        upstream_missing=upstream is None,
        ahead=ahead,
        behind=behind,
        diverged=ahead > 0 and behind > 0,
        working_dirty=working_dirty,
        index_dirty=index_dirty,
        untracked_count=untracked_count,
        conflicted=conflicted,
        stashed=stashed,
    )


def _mark_cached_staleness(
    record: dict[str, Any],
    now: float,
    stale_after_ms: int,
) -> dict[str, Any]:
    updated = dict(record)
    last_checked = updated.get("last_checked")
    age_ms = _age_ms(last_checked, now)
    updated["age_ms"] = age_ms
    updated["stale"] = age_ms < 0 or age_ms > stale_after_ms
    updated["cached"] = bool(updated.get("cached", True))
    return updated


def _age_ms(last_checked: Any, now: float) -> int:
    if not last_checked:
        return -1
    try:
        parsed = time.strptime(str(last_checked), "%Y-%m-%dT%H:%M:%S%z")
        return int((now - time.mktime(parsed)) * 1000)
    except ValueError:
        return -1


def _is_dirty(record: dict[str, Any]) -> bool:
    return bool(
        record.get("working_dirty")
        or record.get("index_dirty")
        or record.get("untracked_count")
        or record.get("conflicted")
    )


def _last_cache_error(payload: dict[str, Any]) -> str | None:
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        return str(errors[-1])
    return None
