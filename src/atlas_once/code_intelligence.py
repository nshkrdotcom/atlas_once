from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .config import AtlasPaths
from .index_watcher import (
    DEFAULT_TTL_MS,
    IndexFreshness,
    ensure_project_freshness,
    make_watch_target,
    record_refresh_result,
)
from .intelligence_service import call_intelligence_service, mcp_request_for_query
from .ranked_context import RankedRuntime, load_ranked_default_runtime
from .registry import resolve_project_ref
from .runtime import AtlasCliError, ExitCode
from .shadow_workspace import ensure_shadow_project_root, shadow_intelligence_lock

TRANSIENT_BACKEND_PATTERNS = (
    "database busy",
    "storeserver",
    "store server",
    "build directory",
    "could not obtain lock",
    "lock on the build",
    "already started",
)
DEFAULT_BACKEND_ATTEMPTS = 3
DEFAULT_BACKEND_RETRY_DELAY_SECONDS = 0.05
QUERY_CACHE_SCHEMA_VERSION = 1
QUERY_CACHE_ENV = "ATLAS_ONCE_INTELLIGENCE_CACHE"
DEFAULT_EXCLUDED_REL_PREFIXES = (
    "_build/",
    "deps/",
    ".elixir_ls/",
)
DEFAULT_EXCLUDED_REL_SEGMENTS = (
    "/deps/",
    "/_build/",
    "/.elixir_ls/",
)
PATH_IN_BACKTICKS = re.compile(r"`([^`]+)`")
RESULT_GROUPS = (
    "implementation",
    "config",
    "support",
    "tests",
    "examples",
    "docs",
    "other",
    "external",
)
CATEGORY_WEIGHT = {category: index for index, category in enumerate(RESULT_GROUPS)}
READ_ONLY_DEXTER_ACTIONS = {"lookup", "refs", "references"}


@dataclass(frozen=True)
class IntelligenceTarget:
    reference: str
    project_ref: str
    project_root: Path
    shadow_root: Path
    runtime: RankedRuntime


@dataclass(frozen=True)
class IntelligenceRun:
    command: list[str]
    cwd: Path
    returncode: int
    stdout: str
    stderr: str
    attempts: int = 1
    cached: bool = False


@dataclass(frozen=True)
class IntelligenceIndexResult:
    run: IntelligenceRun
    skipped: bool = False
    freshness: dict[str, Any] | None = None
    stamp: str | None = None


def _is_path_like(reference: str) -> bool:
    return (
        reference in {".", ".."}
        or reference.startswith("/")
        or reference.startswith("~/")
        or "/" in reference
    )


def find_project_root(path: Path) -> Path:
    current = path.expanduser()
    if not current.is_absolute():
        current = Path.cwd() / current
    current = current.resolve()
    if current.is_file():
        current = current.parent

    for marker in ("mix.exs", ".git"):
        probe = current
        while True:
            if (probe / marker).exists():
                return probe
            if probe.parent == probe:
                break
            probe = probe.parent

    return current


def current_directory_is_mix_project() -> bool:
    return (find_project_root(Path.cwd()) / "mix.exs").is_file()


def resolve_intelligence_target(
    paths: AtlasPaths,
    reference: str | None = None,
    *,
    runtime: RankedRuntime | None = None,
) -> IntelligenceTarget:
    raw_reference = (reference or ".").strip() or "."
    resolved_runtime = runtime or load_ranked_default_runtime(paths)

    if _is_path_like(raw_reference) or Path(raw_reference).expanduser().exists():
        project_root = find_project_root(Path(raw_reference))
        project_ref = project_root.name
    else:
        record = resolve_project_ref(paths, raw_reference)
        project_root = Path(record.path).expanduser().resolve()
        project_ref = record.name

    if not (project_root / "mix.exs").is_file():
        raise AtlasCliError(
            ExitCode.VALIDATION,
            "not_mix_project",
            f"Atlas code intelligence requires an Elixir Mix project: {project_root}",
            {"project_root": str(project_root), "reference": raw_reference},
        )

    shadow_project_root = ensure_shadow_project_root(project_root, resolved_runtime.shadow_root)
    return IntelligenceTarget(
        reference=raw_reference,
        project_ref=project_ref,
        project_root=project_root,
        shadow_root=shadow_project_root,
        runtime=resolved_runtime,
    )


def target_dict(target: IntelligenceTarget) -> dict[str, str]:
    return {
        "reference": target.reference,
        "project_ref": target.project_ref,
        "repo_root": str(target.project_root),
        "shadow_root": str(target.shadow_root),
        "dexterity_root": str(target.runtime.dexterity_root),
        "dexter_bin": target.runtime.dexter_bin,
    }


def _run(
    command: list[str],
    *,
    cwd: Path,
) -> IntelligenceRun:
    result = subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
        env=os.environ.copy(),
    )
    return IntelligenceRun(
        command=command,
        cwd=cwd,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def _is_transient_backend_failure(run: IntelligenceRun) -> bool:
    if run.returncode == 0:
        return False
    text = f"{run.stderr}\n{run.stdout}".lower()
    return any(pattern in text for pattern in TRANSIENT_BACKEND_PATTERNS)


def _run_with_retries(
    command: list[str],
    *,
    cwd: Path,
    max_attempts: int = DEFAULT_BACKEND_ATTEMPTS,
) -> IntelligenceRun:
    attempts = 0
    while True:
        attempts += 1
        run = _run(command, cwd=cwd)
        run = replace(run, attempts=attempts)
        if run.returncode == 0:
            return run
        if attempts >= max_attempts or not _is_transient_backend_failure(run):
            return run
        time.sleep(DEFAULT_BACKEND_RETRY_DELAY_SECONDS * (2 ** (attempts - 1)))


def _query_cache_enabled() -> bool:
    raw = os.environ.get(QUERY_CACHE_ENV)
    return raw is None or raw.strip().lower() not in {"0", "false", "no", "off"}


def _cache_root(paths: AtlasPaths) -> Path:
    return paths.state_home / "code" / "query_cache"


def _storage_index_stamp(target: IntelligenceTarget) -> str:
    entries: list[tuple[str, int, int]] = []
    candidates = [target.shadow_root / ".dexter.db"]
    dexterity_store = target.shadow_root / ".dexterity"
    if dexterity_store.exists():
        candidates.extend(path for path in sorted(dexterity_store.rglob("*")) if path.is_file())

    for candidate in candidates:
        if not candidate.exists() or not candidate.is_file():
            continue
        stat = candidate.stat()
        try:
            relative = candidate.relative_to(target.shadow_root).as_posix()
        except ValueError:
            relative = str(candidate)
        entries.append((relative, stat.st_mtime_ns, stat.st_size))

    if not entries:
        return "missing"
    return hashlib.sha256(
        json.dumps(entries, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def _query_cache_key(
    target: IntelligenceTarget,
    *,
    namespace: str,
    command: list[str],
    index_stamp: str | None,
) -> tuple[str, str]:
    stamp = index_stamp or _storage_index_stamp(target)
    payload = {
        "schema_version": QUERY_CACHE_SCHEMA_VERSION,
        "namespace": namespace,
        "project_root": str(target.project_root),
        "shadow_root": str(target.shadow_root),
        "index_stamp": stamp,
        "command": command,
    }
    key = hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    return key, stamp


def _load_cached_run(
    paths: AtlasPaths,
    target: IntelligenceTarget,
    *,
    namespace: str,
    command: list[str],
    cwd: Path,
    index_stamp: str | None,
) -> tuple[IntelligenceRun | None, dict[str, Any]]:
    if not _query_cache_enabled():
        return None, {"enabled": False, "hit": False, "stored": False}

    key, stamp = _query_cache_key(
        target,
        namespace=namespace,
        command=command,
        index_stamp=index_stamp,
    )
    metadata: dict[str, Any] = {
        "enabled": True,
        "hit": False,
        "stored": False,
        "key": key,
        "index_stamp": stamp,
    }
    path = _cache_root(paths) / f"{key}.json"
    if not path.exists():
        return None, metadata
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        path.unlink(missing_ok=True)
        return None, metadata

    if payload.get("schema_version") != QUERY_CACHE_SCHEMA_VERSION:
        path.unlink(missing_ok=True)
        return None, metadata

    metadata["hit"] = True
    return (
        IntelligenceRun(
            command=command,
            cwd=cwd,
            returncode=0,
            stdout=str(payload.get("stdout") or ""),
            stderr=str(payload.get("stderr") or ""),
            attempts=0,
            cached=True,
        ),
        metadata,
    )


def _store_cached_run(
    paths: AtlasPaths,
    key: str | None,
    run: IntelligenceRun,
) -> bool:
    if key is None or run.returncode != 0 or not _query_cache_enabled():
        return False
    root = _cache_root(paths)
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": QUERY_CACHE_SCHEMA_VERSION,
        "created_at": time.time(),
        "stdout": run.stdout,
        "stderr": run.stderr,
    }
    tmp_path = root / f"{key}.tmp"
    final_path = root / f"{key}.json"
    tmp_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    tmp_path.replace(final_path)
    return True


def _run_cacheable_read(
    paths: AtlasPaths,
    target: IntelligenceTarget,
    *,
    namespace: str,
    command: list[str],
    cwd: Path,
    index_stamp: str | None,
    service_run: tuple[IntelligenceRun, dict[str, Any]] | None = None,
) -> tuple[IntelligenceRun, dict[str, Any]]:
    cached, metadata = _load_cached_run(
        paths,
        target,
        namespace=namespace,
        command=command,
        cwd=cwd,
        index_stamp=index_stamp,
    )
    if cached is not None:
        return cached, metadata

    if service_run is not None:
        run, service = service_run
        metadata["stored"] = _store_cached_run(
            paths,
            metadata.get("key") if isinstance(metadata.get("key"), str) else None,
            run,
        )
        metadata["service"] = service
        return run, metadata

    run = _run_with_retries(command, cwd=cwd)
    key = metadata.get("key") if isinstance(metadata.get("key"), str) else None
    metadata["stored"] = _store_cached_run(paths, key, run)
    return run, metadata


def _raise_on_failure(run: IntelligenceRun, *, kind: str, fallback: str) -> None:
    if run.returncode == 0:
        return
    message = run.stderr.strip() or run.stdout.strip() or fallback
    raise AtlasCliError(
        ExitCode.EXTERNAL,
        kind,
        message,
        {
            "command": run.command,
            "cwd": str(run.cwd),
            "returncode": run.returncode,
            "stderr": run.stderr,
            "stdout": run.stdout,
            "attempts": run.attempts,
        },
    )


def _map_shadow_string(value: str, target: IntelligenceTarget) -> str:
    return value.replace(str(target.shadow_root), str(target.project_root))


def map_shadow_paths(value: Any, target: IntelligenceTarget) -> Any:
    if isinstance(value, str):
        return _map_shadow_string(value, target)
    if isinstance(value, list):
        return [map_shadow_paths(item, target) for item in value]
    if isinstance(value, dict):
        return {key: map_shadow_paths(item, target) for key, item in value.items()}
    return value


def repo_relative_arg(value: str, target: IntelligenceTarget) -> str:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        return value
    try:
        return candidate.resolve().relative_to(target.project_root).as_posix()
    except ValueError:
        return value


def shadow_path_arg(value: str, target: IntelligenceTarget) -> str:
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        try:
            rel_path = candidate.resolve().relative_to(target.project_root)
        except ValueError:
            return str(candidate.resolve())
    else:
        rel_path = Path(value)
    return str((target.shadow_root / rel_path).resolve())


def _normalize_query_positionals(
    action: str,
    positional: list[str],
    target: IntelligenceTarget,
) -> list[str]:
    if action in {"blast", "blast_count", "cochanges"} and positional:
        return [repo_relative_arg(positional[0], target), *positional[1:]]
    return positional


def _normalize_query_options(
    option_args: list[str] | None,
    target: IntelligenceTarget,
) -> list[str]:
    if not option_args:
        return []
    path_options = {"--active-file", "--mentioned-file", "--edited-file", "--changed-file"}
    normalized: list[str] = []
    index = 0
    while index < len(option_args):
        item = option_args[index]
        normalized.append(item)
        if item in path_options and index + 1 < len(option_args):
            normalized.append(repo_relative_arg(option_args[index + 1], target))
            index += 2
            continue
        index += 1
    return normalized


def _freshness_dict(record: IndexFreshness) -> dict[str, Any]:
    return {
        "project_key": record.project_key,
        "project_ref": record.project_ref,
        "status": record.status,
        "age_ms": record.age_ms,
        "wait_outcome": record.wait_outcome,
        "waited_ms": record.waited_ms,
        "last_error": record.last_error,
        "last_refresh_started_at": record.last_refresh_started_at,
        "last_refresh_finished_at": record.last_refresh_finished_at,
    }


def _freshness_cache_stamp(
    freshness: dict[str, Any] | None,
    *,
    fallback: float | None = None,
) -> str:
    if fallback is not None:
        return f"watcher:{fallback}"
    if freshness is not None:
        finished_at = freshness.get("last_refresh_finished_at")
        if finished_at is not None:
            return f"watcher:{finished_at}"
    return "unknown"


def _watcher_freshness(
    paths: AtlasPaths,
    target: IntelligenceTarget,
    *,
    ttl_ms: int,
) -> dict[str, Any] | None:
    record, _ = ensure_project_freshness(
        paths=paths,
        target=make_watch_target(target.project_root, project_ref=target.project_ref),
        ttl_ms=ttl_ms,
        wait_fresh_ms=0,
        dexterity_root=target.runtime.dexterity_root,
        dexter_bin=target.runtime.dexter_bin,
        shadow_root=target.runtime.shadow_root,
        allow_stale=True,
    )
    return _freshness_dict(record)


def _ensure_target_index(
    paths: AtlasPaths,
    target: IntelligenceTarget,
    *,
    force: bool,
    ttl_ms: int,
) -> IntelligenceIndexResult:
    freshness = None if force else _watcher_freshness(paths, target, ttl_ms=ttl_ms)
    if freshness is not None and freshness.get("status") == "fresh":
        return IntelligenceIndexResult(
            IntelligenceRun([], target.runtime.dexterity_root, 0, "", ""),
            skipped=True,
            freshness=freshness,
            stamp=_freshness_cache_stamp(freshness),
        )

    command = [
        "mix",
        "dexterity.index",
        "--repo-root",
        str(target.shadow_root),
        "--dexter-bin",
        target.runtime.dexter_bin,
    ]
    started_at = time.time()
    run = _run_with_retries(command, cwd=target.runtime.dexterity_root)
    finished_at = time.time()
    record_refresh_result(
        paths,
        make_watch_target(target.project_root, project_ref=target.project_ref),
        started_at=started_at,
        finished_at=finished_at,
        return_code=run.returncode,
        error=run.stderr.strip() or run.stdout.strip() or None,
    )
    _raise_on_failure(
        run,
        kind="dexterity_index_failed",
        fallback=f"dexterity.index failed for {target.project_root}",
    )
    return IntelligenceIndexResult(
        run,
        skipped=False,
        freshness=freshness,
        stamp=_freshness_cache_stamp(freshness, fallback=finished_at),
    )


def _path_is_repo_source(path_text: str, target: IntelligenceTarget) -> bool:
    text = path_text.strip()
    if not text:
        return True

    candidate = Path(text).expanduser()
    if candidate.is_absolute():
        try:
            rel_path = candidate.resolve().relative_to(target.project_root)
        except ValueError:
            return False
        rel_text = rel_path.as_posix()
    else:
        rel_text = text.removeprefix("./")
        if rel_text.startswith("../"):
            return False

    if rel_text.startswith(DEFAULT_EXCLUDED_REL_PREFIXES):
        return False
    return not any(segment in f"/{rel_text}" for segment in DEFAULT_EXCLUDED_REL_SEGMENTS)


def _item_path(item: Any) -> str | None:
    if isinstance(item, dict):
        for key in ("file", "path"):
            value = item.get(key)
            if isinstance(value, str):
                return value
        return None
    if isinstance(item, list) and item and isinstance(item[0], str):
        return item[0]
    if isinstance(item, str):
        return item
    return None


def _path_part(value: str) -> str:
    text = value.strip()
    if not text:
        return text
    if text.startswith("- "):
        text = text[2:].strip()
    if ":" in text:
        prefix = text.split(":", 1)[0]
        if "/" in prefix or prefix.endswith((".ex", ".exs", ".md")) or prefix == "mix.exs":
            return prefix
    return text


def _repo_relative_path_text(path_text: str, target: IntelligenceTarget) -> str | None:
    text = _path_part(path_text).removeprefix("./")
    if not text:
        return None

    candidate = Path(text).expanduser()
    if candidate.is_absolute():
        try:
            return candidate.resolve().relative_to(target.project_root).as_posix()
        except ValueError:
            return None
    if text.startswith("../"):
        return None
    return text


def _path_category(path_text: str | None, target: IntelligenceTarget) -> str:
    if path_text is None:
        return "other"
    normalized_path = _path_part(path_text)
    relative = _repo_relative_path_text(normalized_path, target)
    if relative is None or not _path_is_repo_source(normalized_path, target):
        return "external"
    lower = relative.lower()
    if lower.startswith("test/support/") or lower.startswith("tests/support/"):
        return "support"
    if lower.startswith(("lib/", "src/")):
        return "implementation"
    if lower == "mix.exs" or lower.startswith("config/"):
        return "config"
    if lower.startswith(("test/", "tests/")):
        return "tests"
    if lower.startswith("examples/"):
        return "examples"
    if lower.startswith(("docs/", "guides/")) or lower == "readme.md" or lower.endswith(".md"):
        return "docs"
    return "other"


def _group_result_by_path(result: Any, target: IntelligenceTarget) -> dict[str, list[Any]] | None:
    if not isinstance(result, list):
        return None
    groups: dict[str, list[Any]] = {category: [] for category in RESULT_GROUPS}
    for item in result:
        category = _path_category(_item_path(item), target)
        groups[category].append(item)
    return {category: items for category, items in groups.items() if items}


def _sort_result_by_path_category(result: Any, target: IntelligenceTarget) -> Any:
    if not isinstance(result, list):
        return result
    ranked = sorted(
        enumerate(result),
        key=lambda pair: (
            CATEGORY_WEIGHT.get(_path_category(_item_path(pair[1]), target), len(RESULT_GROUPS)),
            pair[0],
        ),
    )
    return [item for _, item in ranked]


def _filter_structured_result(
    result: Any,
    target: IntelligenceTarget,
) -> tuple[Any, dict[str, Any]]:
    if not isinstance(result, list):
        return result, {"mode": "repo_source", "original_count": None, "filtered_count": None}

    filtered: list[Any] = []
    removed = 0
    for item in result:
        path = _item_path(item)
        if path is not None and not _path_is_repo_source(path, target):
            removed += 1
            continue
        filtered.append(item)
    return filtered, {
        "mode": "repo_source",
        "original_count": len(result),
        "filtered_count": len(filtered),
        "removed_count": removed,
    }


def _backtick_path_is_noise(value: str, target: IntelligenceTarget) -> bool:
    looks_like_path = (
        "/" in value
        or value.startswith(".")
        or value.startswith("~")
        or value.endswith((".ex", ".exs"))
        or value == "mix.exs"
    )
    return looks_like_path and not _path_is_repo_source(value, target)


def _filter_impact_text(text: str, target: IntelligenceTarget) -> tuple[str, dict[str, Any]]:
    lines = text.splitlines()
    filtered: list[str] = []
    removed = 0
    for line in lines:
        backtick_values = PATH_IN_BACKTICKS.findall(line)
        if any(_backtick_path_is_noise(value, target) for value in backtick_values):
            removed += 1
            continue
        filtered.append(line)
    return "\n".join(filtered) + ("\n" if text.endswith("\n") and filtered else ""), {
        "mode": "repo_source_text",
        "original_count": len(lines),
        "filtered_count": len(filtered),
        "removed_count": removed,
    }


def _filter_result(
    result: Any,
    target: IntelligenceTarget,
    *,
    text: bool = False,
) -> tuple[Any, dict[str, Any]]:
    if text and isinstance(result, str):
        return _filter_impact_text(result, target)
    return _filter_structured_result(result, target)


def _index_payload(index: IntelligenceIndexResult, target: IntelligenceTarget) -> dict[str, Any]:
    return {
        "command": index.run.command,
        "returncode": index.run.returncode,
        "stdout": _map_shadow_string(index.run.stdout, target),
        "stderr": _map_shadow_string(index.run.stderr, target),
        "attempts": index.run.attempts,
        "skipped": index.skipped,
        "freshness": index.freshness,
    }


def ensure_intelligence_index(
    paths: AtlasPaths,
    reference: str | None = None,
    *,
    runtime: RankedRuntime | None = None,
    force: bool = True,
    ttl_ms: int = DEFAULT_TTL_MS,
) -> tuple[IntelligenceTarget, IntelligenceRun]:
    target = resolve_intelligence_target(paths, reference, runtime=runtime)
    try:
        with shadow_intelligence_lock(target.shadow_root):
            index = _ensure_target_index(paths, target, force=force, ttl_ms=ttl_ms)
    except TimeoutError as exc:
        raise AtlasCliError(
            ExitCode.LOCKED,
            "intelligence_lock_timeout",
            str(exc),
            {"shadow_root": str(target.shadow_root)},
        ) from exc
    return target, index.run


def _service_run_for_query(
    paths: AtlasPaths,
    target: IntelligenceTarget,
    *,
    action: str,
    positional: list[str],
    option_args: list[str],
    command: list[str],
) -> tuple[IntelligenceRun, dict[str, Any]] | None:
    mcp_call = mcp_request_for_query(action, positional, option_args)
    if mcp_call is None:
        return None
    response = call_intelligence_service(
        paths=paths,
        target=target,
        tool=mcp_call.tool,
        arguments=mcp_call.arguments,
    )
    if response is None:
        return None
    service_result = response.get("result")
    if not isinstance(service_result, dict) or "result" not in service_result:
        return None
    payload = {
        "ok": True,
        "command": action,
        "result": service_result["result"],
    }
    service = {
        "used": True,
        "transport": response.get("transport", "mcp_service"),
        "worker": response.get("worker", {}),
        "tool": mcp_call.tool,
    }
    return (
        IntelligenceRun(
            command=command,
            cwd=target.runtime.dexterity_root,
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
            attempts=0,
        ),
        service,
    )


def run_dexterity_query(
    paths: AtlasPaths,
    action: str,
    positional: list[str],
    *,
    reference: str | None = None,
    option_args: list[str] | None = None,
    filter_repo_source: bool = False,
    filter_text: bool = False,
    ttl_ms: int = DEFAULT_TTL_MS,
) -> dict[str, Any]:
    target = resolve_intelligence_target(paths, reference)
    try:
        with shadow_intelligence_lock(target.shadow_root):
            index = _ensure_target_index(paths, target, force=False, ttl_ms=ttl_ms)
            normalized_positionals = _normalize_query_positionals(action, positional, target)
            normalized_options = _normalize_query_options(option_args, target)
            command = [
                "mix",
                "dexterity.query",
                action,
                *normalized_positionals,
                "--repo-root",
                str(target.shadow_root),
                "--dexter-bin",
                target.runtime.dexter_bin,
                "--json",
                *normalized_options,
            ]
            service_run = _service_run_for_query(
                paths,
                target,
                action=action,
                positional=normalized_positionals,
                option_args=normalized_options,
                command=command,
            )
            run, cache = _run_cacheable_read(
                paths,
                target,
                namespace=f"dexterity.query:{action}",
                command=command,
                cwd=target.runtime.dexterity_root,
                index_stamp=index.stamp,
                service_run=service_run,
            )
    except TimeoutError as exc:
        raise AtlasCliError(
            ExitCode.LOCKED,
            "intelligence_lock_timeout",
            str(exc),
            {"shadow_root": str(target.shadow_root)},
        ) from exc
    _raise_on_failure(
        run,
        kind="dexterity_query_failed",
        fallback=f"dexterity.query {action} failed for {target.project_root}",
    )
    try:
        payload = json.loads(run.stdout)
    except json.JSONDecodeError as exc:
        raise AtlasCliError(
            ExitCode.EXTERNAL,
            "invalid_dexterity_json",
            f"dexterity.query {action} did not return JSON",
            {"stdout": run.stdout, "stderr": run.stderr},
        ) from exc

    mapped_payload = map_shadow_paths(payload, target)
    result = mapped_payload.get("result") if isinstance(mapped_payload, dict) else mapped_payload
    filter_payload: dict[str, Any] | None = None
    if filter_repo_source:
        result, filter_payload = _filter_result(result, target, text=filter_text)
    if action == "symbols":
        result = _sort_result_by_path_category(result, target)
    result_groups = (
        _group_result_by_path(result, target) if action in {"symbols", "references"} else None
    )
    return {
        "project": target_dict(target),
        "tool": {
            "kind": "dexterity",
            "command": command,
            "cwd": str(target.runtime.dexterity_root),
            "returncode": run.returncode,
            "attempts": run.attempts,
            "cached": run.cached,
            "cache": cache,
            "transport": "cache"
            if run.cached
            else cache.get("service", {}).get("transport", "subprocess"),
            "service": cache.get("service", {"used": False}),
        },
        "index": _index_payload(index, target),
        "filter": filter_payload
        or {"mode": "none", "original_count": None, "filtered_count": None},
        "result": result,
        "result_groups": result_groups or {},
        "raw": mapped_payload,
    }


def run_dexterity_map(
    paths: AtlasPaths,
    *,
    reference: str | None = None,
    option_args: list[str] | None = None,
    ttl_ms: int = DEFAULT_TTL_MS,
) -> dict[str, Any]:
    target = resolve_intelligence_target(paths, reference)
    try:
        with shadow_intelligence_lock(target.shadow_root):
            index = _ensure_target_index(paths, target, force=False, ttl_ms=ttl_ms)
            normalized_options = _normalize_query_options(option_args, target)
            command = [
                "mix",
                "dexterity.map",
                "--repo-root",
                str(target.shadow_root),
                *normalized_options,
            ]
            run = _run_with_retries(command, cwd=target.runtime.dexterity_root)
    except TimeoutError as exc:
        raise AtlasCliError(
            ExitCode.LOCKED,
            "intelligence_lock_timeout",
            str(exc),
            {"shadow_root": str(target.shadow_root)},
        ) from exc
    _raise_on_failure(
        run,
        kind="dexterity_map_failed",
        fallback=f"dexterity.map failed for {target.project_root}",
    )
    return {
        "project": target_dict(target),
        "tool": {
            "kind": "dexterity",
            "command": command,
            "cwd": str(target.runtime.dexterity_root),
            "returncode": run.returncode,
            "attempts": run.attempts,
        },
        "index": _index_payload(index, target),
        "result": _map_shadow_string(run.stdout, target),
        "stderr": _map_shadow_string(run.stderr, target),
    }


def run_dexter_cli(
    paths: AtlasPaths,
    action: str,
    positional: list[str],
    *,
    reference: str | None = None,
    option_args: list[str] | None = None,
    ensure_index: bool = True,
    ttl_ms: int = DEFAULT_TTL_MS,
) -> dict[str, Any]:
    target = resolve_intelligence_target(paths, reference)
    try:
        with shadow_intelligence_lock(target.shadow_root):
            if ensure_index:
                index = _ensure_target_index(paths, target, force=False, ttl_ms=ttl_ms)
            else:
                index = IntelligenceIndexResult(
                    IntelligenceRun([], target.runtime.dexterity_root, 0, "", ""),
                    skipped=True,
                    freshness=None,
                )

            dexter_action = "references" if action == "refs" else action
            mapped_positionals = list(positional)
            if dexter_action == "reindex" and mapped_positionals:
                mapped_positionals = [shadow_path_arg(mapped_positionals[0], target)]
            if dexter_action == "init":
                mapped_positionals = [str(target.shadow_root), *mapped_positionals]

            command = [
                target.runtime.dexter_bin,
                dexter_action,
                *mapped_positionals,
                *(option_args or []),
            ]
            if dexter_action in READ_ONLY_DEXTER_ACTIONS:
                run, cache = _run_cacheable_read(
                    paths,
                    target,
                    namespace=f"dexter:{dexter_action}",
                    command=command,
                    cwd=target.shadow_root,
                    index_stamp=index.stamp,
                )
            else:
                run = _run_with_retries(command, cwd=target.shadow_root)
                cache = {"enabled": _query_cache_enabled(), "hit": False, "stored": False}
    except TimeoutError as exc:
        raise AtlasCliError(
            ExitCode.LOCKED,
            "intelligence_lock_timeout",
            str(exc),
            {"shadow_root": str(target.shadow_root)},
        ) from exc
    _raise_on_failure(
        run,
        kind="dexter_cli_failed",
        fallback=f"dexter {dexter_action} failed for {target.project_root}",
    )
    return {
        "project": target_dict(target),
        "tool": {
            "kind": "dexter",
            "command": command,
            "cwd": str(target.shadow_root),
            "returncode": run.returncode,
            "attempts": run.attempts,
            "cached": run.cached,
            "cache": cache,
        },
        "index": _index_payload(index, target),
        "stdout": _map_shadow_string(run.stdout, target),
        "stderr": _map_shadow_string(run.stderr, target),
    }
