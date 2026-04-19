from __future__ import annotations

import fnmatch
import hashlib
import json
import math
import os
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import AtlasPaths, load_profile_state, load_settings
from .index_watcher import (
    DEFAULT_TTL_MS,
    IndexFreshness,
    ensure_project_freshness,
    make_watch_target,
)
from .mix_ctx import discover_projects, iter_regular_files
from .profiles import get_ranked_context_template
from .registry import (
    ProjectRecord,
    load_registry,
    manual_project,
    resolve_project_ref,
    scan_registry,
)
from .shadow_workspace import ensure_shadow_project_root
from .util import read_text

ELIXIR_SUFFIXES = {".ex", ".exs"}
SOURCE_SKIP_DIRS = {
    ".git",
    "_build",
    "deps",
    "node_modules",
    "dist",
    "target",
    "tmp",
    "__pycache__",
    ".venv",
    "venv",
}
DEFAULT_EXCLUDED_PROJECT_PREFIXES = (
    "_legacy/",
    "test/",
    "tests/",
    "fixtures/",
    "examples/",
    "example/",
    "support/",
    "tmp/",
    "dist/",
    "deps/",
    "doc/",
    "docs/",
    "bench/",
    "vendor/",
)
DEFAULT_EXCLUDED_PROJECT_CATEGORIES = (
    "legacy",
    "test",
    "fixture",
    "example",
    "support",
    "tmp",
    "dist",
    "dependency",
    "doc",
    "bench",
    "vendor",
)


@dataclass(frozen=True)
class RankedPolicy:
    include_readme: bool
    top_files: int | None
    top_percent: float | None
    overscan_limit: int | None
    max_bytes: int | None = None
    max_tokens: int | None = None
    priority_tier: int = 100
    exclude_path_prefixes: tuple[str, ...] = ()
    exclude_globs: tuple[str, ...] = ()
    exclude: bool = False


@dataclass(frozen=True)
class RankedRuntime:
    dexterity_root: Path
    dexter_bin: str
    shadow_root: Path


@dataclass(frozen=True)
class RankedBundle:
    config_name: str
    files: list[Path]
    text: str
    source_roots: list[Path]


@dataclass(frozen=True)
class RankedSelectedFile:
    abs_path: Path
    output_rel: str
    repo_label: str
    project_rel_path: str
    byte_size: int = 0
    token_estimate: int = 0


@dataclass(frozen=True)
class RankedPreparedManifest:
    config_name: str
    manifest_path: Path
    config_hash: str
    prepared_at: str
    files: list[RankedSelectedFile]
    source_roots: list[Path]
    repo_count: int
    project_count: int
    selection_mode: str = "count"
    consumed_bytes: int = 0
    consumed_tokens_estimate: int = 0
    budget_max_bytes: int | None = None
    budget_max_tokens: int | None = None
    repo_manifest_paths: list[str] = field(default_factory=list)
    repos: list[PreparedRepoSummary] = field(default_factory=list)


@dataclass(frozen=True)
class RankedContextsState:
    profile_name: str
    content_sha256: str


@dataclass(frozen=True)
class RankedContextsSeedResult:
    path: Path
    profile_name: str
    status: str


@dataclass(frozen=True)
class RankedGroupItem:
    ref: str | None
    path: str | None
    variant: str


@dataclass(frozen=True)
class RankedGroupSelector:
    owner_scope: str | None
    has_language: str | None
    primary_language: str | None
    relation: str | None
    exclude_forks: bool
    roots: list[str]
    variant: str


@dataclass(frozen=True)
class RankedRepoVariantConfig:
    strategy: str | None
    policy: RankedPolicy
    projects: dict[str, RankedPolicy]
    project_discovery: RankedProjectDiscovery


@dataclass(frozen=True)
class RankedRepoDefinition:
    key: str
    path: str | None
    ref: str | None
    label: str | None
    default_variant: RankedRepoVariantConfig
    variants: dict[str, RankedRepoVariantConfig]


@dataclass(frozen=True)
class RankedGroupConfig:
    name: str
    items: list[RankedGroupItem]
    selectors: list[RankedGroupSelector]


@dataclass(frozen=True)
class RankedRegistryDefaults:
    self_owners: list[str]


@dataclass(frozen=True)
class RankedConfig:
    default_runtime: RankedRuntime
    registry: RankedRegistryDefaults
    strategies: dict[str, RankedPolicy]
    default_project_discovery: RankedProjectDiscovery
    repos: dict[str, RankedRepoDefinition]
    groups: dict[str, RankedGroupConfig]


@dataclass(frozen=True)
class ResolvedRepoVariant:
    key: str
    repo_record: ProjectRecord
    repo_root: Path
    repo_label: str
    variant_name: str
    strategy: str
    runtime: RankedRuntime
    policy: RankedPolicy
    projects: dict[str, RankedPolicy]
    project_discovery: RankedProjectDiscovery


@dataclass(frozen=True)
class RankedProjectDiscovery:
    exclude_path_prefixes: tuple[str, ...] = DEFAULT_EXCLUDED_PROJECT_PREFIXES
    include_path_prefixes: tuple[str, ...] = ()
    exclude_categories: tuple[str, ...] = DEFAULT_EXCLUDED_PROJECT_CATEGORIES
    include_categories: tuple[str, ...] = ()


@dataclass(frozen=True)
class RankableProject:
    rel_path: str
    abs_path: Path
    category: str
    excluded: bool
    exclusion_reason: str | None = None


@dataclass(frozen=True)
class PreparedProjectSummary:
    project_rel_path: str
    category: str
    excluded: bool
    exclusion_reason: str | None
    selected_count: int
    fallback_used: bool
    shadow_root: str | None
    selected_bytes: int = 0
    selected_tokens_estimate: int = 0
    priority_tier: int = 100
    selection_mode: str = "count"


@dataclass(frozen=True)
class PreparedRepoSummary:
    repo_key: str
    repo_label: str
    repo_root: Path
    variant_name: str
    strategy: str
    project_count: int
    projects: list[PreparedProjectSummary]
    selected_bytes: int = 0
    selected_tokens_estimate: int = 0
    selection_mode: str = "count"
    unmatched_project_overrides: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RepoPreparedManifest:
    repo_key: str
    repo_label: str
    repo_root: Path
    variant_name: str
    strategy: str
    manifest_path: Path
    variant_hash: str
    prepared_at: str
    files: list[RankedSelectedFile]
    project_count: int
    selected_bytes: int = 0
    selected_tokens_estimate: int = 0
    selection_mode: str = "count"
    projects: list[PreparedProjectSummary] = field(default_factory=list)
    unmatched_project_overrides: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RankedCandidateFile:
    abs_path: Path
    output_rel: str
    repo_label: str
    project_rel_path: str
    scope_rel_path: str
    byte_size: int
    token_estimate: int


@dataclass(frozen=True)
class BudgetSelection:
    files: list[RankedCandidateFile]
    consumed_bytes: int
    consumed_tokens_estimate: int
    truncated: bool


@dataclass
class ProjectPreparedSelection:
    summary: PreparedProjectSummary
    files: list[RankedCandidateFile]


ProgressCallback = Callable[[str], None]
UNSET_RANKED_POLICY = RankedPolicy(
    include_readme=True,
    top_files=None,
    top_percent=None,
    overscan_limit=None,
)


BUILTIN_STRATEGY_DEFAULTS: dict[str, RankedPolicy] = {
    "elixir_ranked_v1": RankedPolicy(
        include_readme=True,
        top_files=10,
        top_percent=None,
        overscan_limit=50,
        max_bytes=60_000,
        max_tokens=15_000,
    ),
    "python_default_v1": RankedPolicy(
        include_readme=True,
        top_files=10,
        top_percent=None,
        overscan_limit=None,
        max_bytes=40_000,
        max_tokens=10_000,
    ),
    "rust_default_v1": RankedPolicy(
        include_readme=True,
        top_files=10,
        top_percent=None,
        overscan_limit=None,
        max_bytes=40_000,
        max_tokens=10_000,
    ),
    "node_default_v1": RankedPolicy(
        include_readme=True,
        top_files=10,
        top_percent=None,
        overscan_limit=None,
        max_bytes=40_000,
        max_tokens=10_000,
    ),
    "generic_default_v1": RankedPolicy(
        include_readme=True,
        top_files=10,
        top_percent=None,
        overscan_limit=None,
        max_bytes=40_000,
        max_tokens=10_000,
    ),
}


def ensure_ranked_contexts_config(
    paths: AtlasPaths, profile_name: str, *, force: bool = False
) -> RankedContextsSeedResult:
    template = get_ranked_context_template(profile_name)
    if template is None:
        return RankedContextsSeedResult(
            path=paths.ranked_contexts_path,
            profile_name=profile_name,
            status="unavailable",
        )

    config_path = paths.ranked_contexts_path
    desired_text = _render_ranked_contexts(template)
    desired_hash = _sha256_text(desired_text)
    current_text = config_path.read_text(encoding="utf-8") if config_path.is_file() else None
    current_hash = _sha256_text(current_text) if current_text is not None else None
    state = load_ranked_contexts_state(paths)

    if current_text == desired_text:
        save_ranked_contexts_state(
            paths,
            RankedContextsState(profile_name=profile_name, content_sha256=desired_hash),
        )
        return RankedContextsSeedResult(
            path=config_path,
            profile_name=profile_name,
            status="unchanged",
        )

    if force or current_text is None:
        _write_ranked_contexts_text(config_path, desired_text)
        save_ranked_contexts_state(
            paths,
            RankedContextsState(profile_name=profile_name, content_sha256=desired_hash),
        )
        return RankedContextsSeedResult(
            path=config_path,
            profile_name=profile_name,
            status="installed" if current_text is None else "updated",
        )

    if state is not None and state.content_sha256 == current_hash:
        _write_ranked_contexts_text(config_path, desired_text)
        save_ranked_contexts_state(
            paths,
            RankedContextsState(profile_name=profile_name, content_sha256=desired_hash),
        )
        return RankedContextsSeedResult(
            path=config_path,
            profile_name=profile_name,
            status="updated",
        )

    return RankedContextsSeedResult(
        path=config_path,
        profile_name=profile_name,
        status="preserved_custom",
    )


def load_ranked_contexts_state(paths: AtlasPaths) -> RankedContextsState | None:
    state_path = paths.ranked_contexts_state_path
    if not state_path.is_file():
        return None

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    return RankedContextsState(
        profile_name=str(payload["profile_name"]),
        content_sha256=str(payload["content_sha256"]),
    )


def save_ranked_contexts_state(paths: AtlasPaths, state: RankedContextsState) -> None:
    state_path = paths.ranked_contexts_state_path
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(asdict(state), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def read_ranked_contexts_text(paths: AtlasPaths) -> str:
    _maybe_reconcile_ranked_contexts_config(paths)
    config_path = paths.ranked_contexts_path
    if not config_path.is_file():
        raise SystemExit(_missing_ranked_contexts_message(config_path))
    return config_path.read_text(encoding="utf-8")


def load_ranked_contexts_payload(paths: AtlasPaths) -> dict[str, Any]:
    payload = json.loads(read_ranked_contexts_text(paths))
    if not isinstance(payload, dict):
        raise SystemExit(
            f"ranked context config must be a JSON object: {paths.ranked_contexts_path}"
        )
    return payload


def _maybe_reconcile_ranked_contexts_config(paths: AtlasPaths) -> None:
    state = load_profile_state(paths)
    if state is None:
        return
    ensure_ranked_contexts_config(paths, state.name)


def prepare_ranked_manifest(
    paths: AtlasPaths,
    config_name: str,
    *,
    progress: ProgressCallback | None = None,
) -> RankedPreparedManifest:
    prepared = _build_prepared_manifest(paths, config_name, progress=progress)
    prepared.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    prepared.manifest_path.write_text(
        json.dumps(prepared_manifest_dict(prepared), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return prepared


def load_prepared_ranked_manifest(paths: AtlasPaths, config_name: str) -> RankedPreparedManifest:
    manifest_path = _prepared_manifest_path(paths, config_name)
    if not manifest_path.is_file():
        raise SystemExit(_missing_prepared_manifest_message(config_name, manifest_path))

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"Invalid prepared ranked manifest: {manifest_path}")
    if int(payload.get("version", 0)) != 1:
        raise SystemExit(f"Unsupported prepared ranked manifest version: {manifest_path}")
    if str(payload.get("config_name", "")) != config_name:
        raise SystemExit(f"Prepared ranked manifest does not match config: {manifest_path}")

    files_payload = payload.get("files", [])
    if not isinstance(files_payload, list):
        raise SystemExit(f"Invalid prepared ranked manifest files: {manifest_path}")

    files: list[RankedSelectedFile] = []
    for item in files_payload:
        if not isinstance(item, dict):
            raise SystemExit(f"Invalid prepared ranked manifest row: {manifest_path}")
        files.append(
            RankedSelectedFile(
                abs_path=Path(str(item["path"])).expanduser().resolve(),
                output_rel=_strip_output_prefix(str(item["output_path"])),
                repo_label=str(item.get("repo_label", "")),
                project_rel_path=str(item.get("project_rel_path", ".")),
                byte_size=int(item.get("byte_size", 0)),
                token_estimate=int(item.get("token_estimate", 0)),
            )
        )

    source_roots_payload = payload.get("source_roots", [])
    if not isinstance(source_roots_payload, list):
        raise SystemExit(f"Invalid prepared ranked manifest source_roots: {manifest_path}")

    repo_summaries = [
        _load_prepared_repo_summary(item)
        for item in payload.get("repos", [])
        if isinstance(item, dict)
    ]

    return RankedPreparedManifest(
        config_name=config_name,
        manifest_path=manifest_path,
        config_hash=str(payload["config_hash"]),
        prepared_at=str(payload["prepared_at"]),
        files=files,
        source_roots=[Path(str(item)).expanduser().resolve() for item in source_roots_payload],
        repo_count=int(payload.get("repo_count", 0)),
        project_count=int(payload.get("project_count", 0)),
        selection_mode=str(payload.get("selection_mode", "count")),
        consumed_bytes=int(payload.get("consumed_bytes", 0)),
        consumed_tokens_estimate=int(payload.get("consumed_tokens_estimate", 0)),
        budget_max_bytes=(
            int(payload["budget"]["max_bytes"])
            if _budget_payload_has_value(payload, "max_bytes")
            else None
        ),
        budget_max_tokens=(
            int(payload["budget"]["max_tokens"])
            if _budget_payload_has_value(payload, "max_tokens")
            else None
        ),
        repo_manifest_paths=[str(item) for item in payload.get("repo_manifest_paths", [])],
        repos=repo_summaries,
    )


def prepared_manifest_dict(prepared: RankedPreparedManifest) -> dict[str, object]:
    return {
        "version": 1,
        "config_name": prepared.config_name,
        "manifest_path": str(prepared.manifest_path),
        "config_hash": prepared.config_hash,
        "prepared_at": prepared.prepared_at,
        "repo_count": prepared.repo_count,
        "project_count": prepared.project_count,
        "file_count": len(prepared.files),
        "selection_mode": prepared.selection_mode,
        "consumed_bytes": prepared.consumed_bytes,
        "consumed_tokens_estimate": prepared.consumed_tokens_estimate,
        "budget": {
            "max_bytes": prepared.budget_max_bytes,
            "max_tokens": prepared.budget_max_tokens,
        },
        "source_roots": [str(path) for path in prepared.source_roots],
        "repo_manifest_paths": prepared.repo_manifest_paths,
        "repos": [_prepared_repo_summary_dict(item) for item in prepared.repos],
        "files": [
            {
                "path": str(item.abs_path),
                "output_path": f"./{item.output_rel}",
                "repo_label": item.repo_label,
                "project_rel_path": item.project_rel_path,
                "byte_size": item.byte_size,
                "token_estimate": item.token_estimate,
            }
            for item in prepared.files
        ],
    }


def prepared_ranked_manifest_staleness(
    paths: AtlasPaths,
    config_name: str,
    prepared: RankedPreparedManifest,
) -> str | None:
    current_hash = _ranked_config_hash(paths, config_name)
    if prepared.config_hash != current_hash:
        return _stale_prepared_manifest_message(config_name)

    for item in prepared.files:
        if not item.abs_path.is_file():
            return (
                f"Prepared ranked context stale for {config_name}: missing file {item.abs_path}."
            )
    return None


def ensure_prepared_ranked_manifest(
    paths: AtlasPaths,
    config_name: str,
    *,
    progress: ProgressCallback | None = None,
) -> tuple[RankedPreparedManifest, bool, str | None]:
    reason: str | None = None
    try:
        prepared = load_prepared_ranked_manifest(paths, config_name)
    except SystemExit as exc:
        reason = str(exc)
    else:
        reason = prepared_ranked_manifest_staleness(paths, config_name, prepared)
        if reason is None:
            return prepared, False, None

    prepared = prepare_ranked_manifest(paths, config_name, progress=progress)
    return prepared, True, reason


def render_prepared_ranked_bundle(paths: AtlasPaths, config_name: str) -> RankedBundle:
    prepared, _, _ = ensure_prepared_ranked_manifest(paths, config_name)

    parts: list[str] = []
    ordered_files: list[Path] = []
    seen_files: set[Path] = set()

    for item in prepared.files:
        if not item.abs_path.is_file():
            raise SystemExit(
                f"Prepared ranked context stale for {config_name}: missing file {item.abs_path}. "
                "The file changed during render; rerun the command."
            )
        _append_file(parts, ordered_files, seen_files, item.abs_path, item.output_rel)

    return RankedBundle(
        config_name=config_name,
        files=ordered_files,
        text="".join(parts),
        source_roots=prepared.source_roots,
    )


def collect_ranked_bundle(paths: AtlasPaths, config_name: str) -> RankedBundle:
    prepared = _build_prepared_manifest(paths, config_name, progress=None)
    return _render_ranked_bundle_from_prepared(prepared)


def load_ranked_default_runtime(paths: AtlasPaths) -> RankedRuntime:
    return _load_ranked_config(paths).default_runtime


def ranked_index_freshness_payload(
    paths: AtlasPaths,
    config_name: str,
    *,
    ttl_ms: int = DEFAULT_TTL_MS,
    wait_fresh_ms: int = 0,
    allow_stale: bool = True,
) -> dict[str, object]:
    config = _load_ranked_config(paths)
    try:
        group = config.groups[config_name]
    except KeyError as exc:
        raise SystemExit(f"Unknown ranked context config: {config_name}") from exc

    records: list[IndexFreshness] = []
    seen_projects: set[str] = set()
    for resolved in _resolve_group_repos(paths, config, group):
        if resolved.strategy != "elixir_ranked_v1":
            continue
        for project in _discover_rankable_projects(resolved.repo_root, resolved.project_discovery):
            project_policy = resolved.projects.get(project.rel_path, resolved.policy)
            if project.excluded or project_policy.exclude:
                continue
            target = make_watch_target(
                project.abs_path,
                project_ref=resolved.repo_record.name or resolved.repo_label,
            )
            if target.project_key in seen_projects:
                continue
            seen_projects.add(target.project_key)
            freshness, _ = ensure_project_freshness(
                paths=paths,
                target=target,
                ttl_ms=ttl_ms,
                wait_fresh_ms=wait_fresh_ms,
                dexterity_root=resolved.runtime.dexterity_root,
                dexter_bin=resolved.runtime.dexter_bin,
                shadow_root=resolved.runtime.shadow_root,
                allow_stale=allow_stale,
            )
            records.append(freshness)

    payload = _index_freshness_summary(
        records,
        ttl_ms=ttl_ms,
        wait_fresh_ms=wait_fresh_ms,
        allow_stale=allow_stale,
    )
    if not allow_stale and payload["ok"] is not True:
        raise SystemExit(
            f"Ranked index freshness failed for {config_name}: "
            f"{payload['stale_projects']} stale, "
            f"{payload['warming_projects']} warming, "
            f"{payload['error_projects']} error."
        )
    return payload


def _index_freshness_summary(
    records: list[IndexFreshness],
    *,
    ttl_ms: int,
    wait_fresh_ms: int,
    allow_stale: bool,
) -> dict[str, object]:
    fresh = sum(1 for item in records if item.status == "fresh")
    warming = sum(1 for item in records if item.status == "warming")
    errors = sum(1 for item in records if item.status == "error")
    stale = sum(1 for item in records if item.status not in {"fresh", "warming", "error"})
    waited_ms = max((item.waited_ms for item in records), default=0)
    if wait_fresh_ms <= 0:
        wait_outcome = "none"
    elif stale == 0 and warming == 0 and errors == 0:
        wait_outcome = "completed"
    else:
        wait_outcome = "timed_out"
    return {
        "index_version": 1,
        "ok": errors == 0 and (allow_stale or (stale == 0 and warming == 0)),
        "ttl_ms": ttl_ms,
        "fresh_projects": fresh,
        "stale_projects": stale,
        "warming_projects": warming,
        "error_projects": errors,
        "project_count": len(records),
        "index_wait_requested_ms": wait_fresh_ms,
        "index_waited_ms": waited_ms,
        "index_wait_outcome": wait_outcome,
        "allow_stale": allow_stale,
        "projects": [
            {
                "project_key": item.project_key,
                "project_ref": item.project_ref,
                "status": item.status,
                "age_ms": item.age_ms,
                "wait_outcome": item.wait_outcome,
                "waited_ms": item.waited_ms,
                "last_error": item.last_error,
                "last_refresh_started_at": item.last_refresh_started_at,
                "last_refresh_finished_at": item.last_refresh_finished_at,
                "last_file_mtime": item.last_file_mtime,
                "indexed_file_mtime": item.indexed_file_mtime,
                "last_source_signature": item.last_source_signature,
                "indexed_source_signature": item.indexed_source_signature,
            }
            for item in records
        ],
    }


def _build_prepared_manifest(
    paths: AtlasPaths,
    config_name: str,
    *,
    progress: ProgressCallback | None,
) -> RankedPreparedManifest:
    config = _load_ranked_config(paths)
    try:
        group = config.groups[config_name]
    except KeyError as exc:
        raise SystemExit(f"Unknown ranked context config: {config_name}") from exc

    resolved_repos = _resolve_group_repos(paths, config, group)
    selected_files: list[RankedSelectedFile] = []
    seen_files: set[Path] = set()
    source_roots: list[Path] = []
    repo_manifest_paths: list[str] = []
    project_count = 0
    repo_summaries: list[PreparedRepoSummary] = []
    consumed_bytes = 0
    consumed_tokens = 0
    group_budget_max_bytes: int | None = None
    group_budget_max_tokens: int | None = None

    for index, resolved in enumerate(resolved_repos, start=1):
        if len(resolved_repos) == 1:
            group_budget_max_bytes = resolved.policy.max_bytes
            group_budget_max_tokens = resolved.policy.max_tokens
        manifest, cache_hit = _prepare_repo_variant_manifest(
            paths,
            resolved,
            progress=progress,
        )
        repo_manifest_paths.append(str(manifest.manifest_path))
        if resolved.repo_root not in source_roots:
            source_roots.append(resolved.repo_root)
        project_count += manifest.project_count
        repo_summaries.append(_repo_manifest_summary(manifest))
        consumed_bytes += manifest.selected_bytes
        consumed_tokens += manifest.selected_tokens_estimate
        _emit_progress(
            progress,
            (
                f"[repo {index}/{len(resolved_repos)}] {resolved.repo_label} "
                f"variant={resolved.variant_name} strategy={resolved.strategy} "
                f"repo_cache={'hit' if cache_hit else 'miss'}"
            ),
        )
        for item in manifest.files:
            _append_selected_file(
                selected_files,
                seen_files,
                item.abs_path,
                item.output_rel,
                item.repo_label,
                item.project_rel_path,
                byte_size=item.byte_size,
                token_estimate=item.token_estimate,
            )

    manifest_path = _prepared_manifest_path(paths, config_name)
    selection_mode = (
        "budget" if any(item.selection_mode == "budget" for item in repo_summaries) else "count"
    )
    return RankedPreparedManifest(
        config_name=config_name,
        manifest_path=manifest_path,
        config_hash=_ranked_config_hash(paths, config_name),
        prepared_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        files=selected_files,
        source_roots=source_roots,
        repo_count=len(resolved_repos),
        project_count=project_count,
        selection_mode=selection_mode,
        consumed_bytes=consumed_bytes,
        consumed_tokens_estimate=consumed_tokens,
        budget_max_bytes=group_budget_max_bytes,
        budget_max_tokens=group_budget_max_tokens,
        repo_manifest_paths=repo_manifest_paths,
        repos=repo_summaries,
    )


def _load_ranked_config(paths: AtlasPaths) -> RankedConfig:
    config_path = paths.ranked_contexts_path
    payload = load_ranked_contexts_payload(paths)
    _validate_keys("ranked context config", payload, {"version", "defaults", "repos", "groups"})
    if int(payload.get("version", 0)) != 3:
        raise SystemExit("ranked context config version must be 3")

    defaults_payload = _as_dict(payload.get("defaults", {}), "defaults")
    _validate_keys(
        "defaults",
        defaults_payload,
        {"registry", "runtime", "strategies", "project_discovery"},
    )

    registry_payload = _as_dict(defaults_payload.get("registry", {}), "defaults.registry")
    _validate_keys("defaults.registry", registry_payload, {"self_owners"})
    registry_defaults = RankedRegistryDefaults(
        self_owners=[
            str(item).strip()
            for item in registry_payload.get("self_owners", [])
            if str(item).strip()
        ]
    )

    runtime_payload = _as_dict(defaults_payload.get("runtime", {}), "defaults.runtime")
    default_runtime = _parse_runtime(runtime_payload, config_path, paths=paths)

    default_project_discovery = _parse_project_discovery(
        defaults_payload.get("project_discovery", {}),
        "defaults.project_discovery",
        fallback=RankedProjectDiscovery(),
    )

    strategies = dict(BUILTIN_STRATEGY_DEFAULTS)
    strategies_payload = _as_dict(defaults_payload.get("strategies", {}), "defaults.strategies")
    for name, item in strategies_payload.items():
        strategy_payload = _as_dict(item, f"defaults.strategies.{name}")
        if name not in BUILTIN_STRATEGY_DEFAULTS:
            raise SystemExit(f"Unknown ranked strategy: {name}")
        strategies[name] = _parse_policy(
            strategy_payload,
            f"defaults.strategies.{name}",
            fallback=BUILTIN_STRATEGY_DEFAULTS[name],
        )

    repos_payload = _as_dict(payload.get("repos", {}), "repos")
    repos: dict[str, RankedRepoDefinition] = {}
    for repo_key, item in repos_payload.items():
        repos[str(repo_key)] = _parse_repo_definition(
            str(repo_key),
            item,
            strategies,
            default_project_discovery,
        )

    groups_payload = _as_dict(payload.get("groups", {}), "groups")
    groups: dict[str, RankedGroupConfig] = {}
    for group_name, item in groups_payload.items():
        groups[str(group_name)] = _parse_group(str(group_name), item)

    return RankedConfig(
        default_runtime=default_runtime,
        registry=registry_defaults,
        strategies=strategies,
        default_project_discovery=default_project_discovery,
        repos=repos,
        groups=groups,
    )


def _parse_repo_definition(
    repo_key: str,
    item: object,
    strategies: dict[str, RankedPolicy],
    default_project_discovery: RankedProjectDiscovery,
) -> RankedRepoDefinition:
    payload = _as_dict(item, f"repos.{repo_key}")
    _validate_keys(
        f"repos.{repo_key}",
        payload,
        {
            "path",
            "ref",
            "label",
            "strategy",
            "include_readme",
            "top_files",
            "top_percent",
            "overscan_limit",
            "max_bytes",
            "max_tokens",
            "priority_tier",
            "exclude_path_prefixes",
            "exclude_globs",
            "projects",
            "variants",
            "project_discovery",
        },
    )

    path = _optional_string(payload.get("path"))
    ref = _optional_string(payload.get("ref")) or (repo_key if path is None else None)
    if path is not None and ref is not None and payload.get("ref") is not None:
        raise SystemExit(f"repos.{repo_key} must not set both path and ref")

    base_policy = _parse_policy(
        payload,
        f"repos.{repo_key}",
        fallback=UNSET_RANKED_POLICY,
    )
    base_strategy = _parse_strategy_name(payload.get("strategy"), f"repos.{repo_key}.strategy")
    base_projects = _parse_project_overrides(
        payload.get("projects", {}),
        f"repos.{repo_key}.projects",
        base_policy,
    )
    base_project_discovery = _parse_project_discovery(
        payload.get("project_discovery", {}),
        f"repos.{repo_key}.project_discovery",
        fallback=default_project_discovery,
    )
    default_variant = RankedRepoVariantConfig(
        strategy=base_strategy,
        policy=base_policy,
        projects=base_projects,
        project_discovery=base_project_discovery,
    )

    variants_payload = _as_dict(payload.get("variants", {}), f"repos.{repo_key}.variants")
    variants: dict[str, RankedRepoVariantConfig] = {}
    for variant_name, variant_item in variants_payload.items():
        variant_payload = _as_dict(variant_item, f"repos.{repo_key}.variants.{variant_name}")
        _validate_keys(
            f"repos.{repo_key}.variants.{variant_name}",
            variant_payload,
            {
                "strategy",
                "include_readme",
                "top_files",
                "top_percent",
                "overscan_limit",
                "max_bytes",
                "max_tokens",
                "priority_tier",
                "exclude_path_prefixes",
                "exclude_globs",
                "projects",
                "project_discovery",
            },
        )
        variant_policy = _parse_policy(
            variant_payload,
            f"repos.{repo_key}.variants.{variant_name}",
            fallback=base_policy,
        )
        variant_projects = dict(base_projects)
        variant_projects.update(
            _parse_project_overrides(
                variant_payload.get("projects", {}),
                f"repos.{repo_key}.variants.{variant_name}.projects",
                variant_policy,
            )
        )
        variant_project_discovery = _parse_project_discovery(
            variant_payload.get("project_discovery", {}),
            f"repos.{repo_key}.variants.{variant_name}.project_discovery",
            fallback=base_project_discovery,
        )
        variants[str(variant_name)] = RankedRepoVariantConfig(
            strategy=_parse_strategy_name(
                variant_payload.get("strategy"),
                f"repos.{repo_key}.variants.{variant_name}.strategy",
            )
            or base_strategy,
            policy=variant_policy,
            projects=variant_projects,
            project_discovery=variant_project_discovery,
        )

    return RankedRepoDefinition(
        key=repo_key,
        path=path,
        ref=ref,
        label=_optional_string(payload.get("label")),
        default_variant=default_variant,
        variants=variants,
    )


def _parse_project_overrides(
    payload: object, label: str, fallback_policy: RankedPolicy
) -> dict[str, RankedPolicy]:
    payload_dict = _as_dict(payload, label)
    projects: dict[str, RankedPolicy] = {}
    for rel_path, override in payload_dict.items():
        override_dict = _as_dict(override, f"{label}.{rel_path}")
        _validate_keys(
            f"{label}.{rel_path}",
            override_dict,
            {
                "exclude",
                "include_readme",
                "top_files",
                "top_percent",
                "overscan_limit",
                "max_bytes",
                "max_tokens",
                "priority_tier",
                "exclude_path_prefixes",
                "exclude_globs",
            },
        )
        if "include" in override_dict:
            raise SystemExit(f"{label}.{rel_path}.include is not supported")
        projects[str(rel_path)] = _parse_policy(
            override_dict,
            f"{label}.{rel_path}",
            fallback=fallback_policy,
            allow_exclude=True,
        )
    return projects


def _parse_group(group_name: str, item: object) -> RankedGroupConfig:
    payload = _as_dict(item, f"groups.{group_name}")
    _validate_keys(f"groups.{group_name}", payload, {"items", "selectors"})
    items_payload = payload.get("items", [])
    selectors_payload = payload.get("selectors", [])
    if not isinstance(items_payload, list) or not isinstance(selectors_payload, list):
        raise SystemExit(f"groups.{group_name} items/selectors must be arrays")

    items: list[RankedGroupItem] = []
    for index, row in enumerate(items_payload):
        row_dict = _as_dict(row, f"groups.{group_name}.items[{index}]")
        _validate_keys(f"groups.{group_name}.items[{index}]", row_dict, {"ref", "path", "variant"})
        path = _optional_string(row_dict.get("path"))
        ref = _optional_string(row_dict.get("ref"))
        if bool(path) == bool(ref):
            raise SystemExit(
                f"groups.{group_name}.items[{index}] "
                "must set exactly one of path or ref"
            )
        items.append(
            RankedGroupItem(
                ref=ref,
                path=path,
                variant=_optional_string(row_dict.get("variant")) or "default",
            )
        )

    selectors: list[RankedGroupSelector] = []
    for index, row in enumerate(selectors_payload):
        row_dict = _as_dict(row, f"groups.{group_name}.selectors[{index}]")
        _validate_keys(
            f"groups.{group_name}.selectors[{index}]",
            row_dict,
            {
                "owner_scope",
                "has_language",
                "primary_language",
                "relation",
                "exclude_forks",
                "roots",
                "variant",
            },
        )
        roots_payload = row_dict.get("roots", [])
        if not isinstance(roots_payload, list):
            raise SystemExit(f"groups.{group_name}.selectors[{index}].roots must be an array")
        selectors.append(
            RankedGroupSelector(
                owner_scope=_optional_string(row_dict.get("owner_scope")),
                has_language=_optional_string(row_dict.get("has_language")),
                primary_language=_optional_string(row_dict.get("primary_language")),
                relation=_optional_string(row_dict.get("relation")),
                exclude_forks=_parse_bool(
                    row_dict.get("exclude_forks", False),
                    f"groups.{group_name}.selectors[{index}].exclude_forks",
                ),
                roots=[
                    str(Path(str(item)).expanduser().resolve())
                    for item in roots_payload
                    if str(item).strip()
                ],
                variant=_optional_string(row_dict.get("variant")) or "default",
            )
        )

    if not items and not selectors:
        raise SystemExit(f"groups.{group_name} must define items or selectors")
    return RankedGroupConfig(name=group_name, items=items, selectors=selectors)


def _parse_strategy_name(value: object, label: str) -> str | None:
    strategy = _optional_string(value)
    if strategy is None:
        return None
    if strategy not in BUILTIN_STRATEGY_DEFAULTS:
        raise SystemExit(f"{label} must be one of: {', '.join(sorted(BUILTIN_STRATEGY_DEFAULTS))}")
    return strategy


def _resolve_group_repos(
    paths: AtlasPaths, config: RankedConfig, group: RankedGroupConfig
) -> list[ResolvedRepoVariant]:
    registry = _load_registry_or_scan(paths)
    records_by_path = {record.path: record for record in registry}

    resolved: list[ResolvedRepoVariant] = []
    seen: set[tuple[str, str]] = set()

    for item in group.items:
        resolved_variant = _resolve_repo_variant(
            paths=paths,
            config=config,
            registry_records=registry,
            registry_by_path=records_by_path,
            ref=item.ref,
            path=item.path,
            variant_name=item.variant,
        )
        key = (resolved_variant.repo_record.path, resolved_variant.variant_name)
        if key not in seen:
            seen.add(key)
            resolved.append(resolved_variant)

    for selector in group.selectors:
        for record in _filter_registry_records(registry, selector):
            resolved_variant = _resolve_repo_variant(
                paths=paths,
                config=config,
                registry_records=registry,
                registry_by_path=records_by_path,
                ref=record.name,
                path=None,
                variant_name=selector.variant,
            )
            key = (resolved_variant.repo_record.path, resolved_variant.variant_name)
            if key not in seen:
                seen.add(key)
                resolved.append(resolved_variant)

    return resolved


def _load_registry_or_scan(paths: AtlasPaths) -> list[ProjectRecord]:
    registry = load_registry(paths)
    if registry:
        return registry
    settings = load_settings(paths)
    if settings.project_roots:
        return scan_registry(paths, settings)
    return []


def _filter_registry_records(
    registry: list[ProjectRecord], selector: RankedGroupSelector
) -> list[ProjectRecord]:
    records = sorted(registry, key=lambda item: item.name.lower())
    filtered: list[ProjectRecord] = []
    for record in records:
        if selector.owner_scope is not None and record.owner_scope != selector.owner_scope:
            continue
        if selector.has_language is not None and selector.has_language.lower() not in {
            language.lower() for language in record.languages
        }:
            continue
        if (
            selector.primary_language is not None
            and record.primary_language.lower() != selector.primary_language.lower()
        ):
            continue
        if selector.relation is not None and record.relation != selector.relation:
            continue
        if selector.exclude_forks and record.relation == "fork":
            continue
        if selector.roots:
            record_path = Path(record.path).resolve()
            if not any(_path_within_root(record_path, Path(root)) for root in selector.roots):
                continue
        filtered.append(record)
    return filtered


def _resolve_repo_variant(
    *,
    paths: AtlasPaths,
    config: RankedConfig,
    registry_records: list[ProjectRecord],
    registry_by_path: dict[str, ProjectRecord],
    ref: str | None,
    path: str | None,
    variant_name: str,
) -> ResolvedRepoVariant:
    repo_record = (
        resolve_project_ref(paths, ref or "", auto_scan=not bool(registry_records))
        if ref is not None
        else manual_project(path or "")
    )
    repo_root_text = repo_record.path if repo_record.path else str(Path(path or "").expanduser())
    repo_root = Path(repo_root_text).resolve()
    repo_record = registry_by_path.get(str(repo_root), repo_record)
    repo_definition = _match_repo_definition(config.repos, repo_record, ref, path)

    if repo_definition is None:
        repo_definition = RankedRepoDefinition(
            key=repo_record.slug or repo_root.name,
            path=None if ref is not None else str(repo_root),
            ref=repo_record.name if ref is not None else None,
            label=None,
            default_variant=RankedRepoVariantConfig(
                strategy=None,
                policy=UNSET_RANKED_POLICY,
                projects={},
                project_discovery=config.default_project_discovery,
            ),
            variants={},
        )

    if variant_name == "default":
        variant = repo_definition.default_variant
    else:
        try:
            variant = repo_definition.variants[variant_name]
        except KeyError as exc:
            raise SystemExit(
                f"Unknown repo variant {variant_name!r} for {repo_definition.key}"
            ) from exc

    strategy = (
        variant.strategy
        or repo_definition.default_variant.strategy
        or _auto_strategy(repo_record, repo_root)
    )
    policy = _resolved_strategy_policy(strategy, config.strategies, variant.policy)
    if strategy != "elixir_ranked_v1" and variant.projects:
        raise SystemExit(
            f"Repo {repo_definition.key} variant {variant_name} uses project overrides, "
            "but only elixir_ranked_v1 supports nested project controls."
        )

    return ResolvedRepoVariant(
        key=repo_definition.key,
        repo_record=repo_record,
        repo_root=repo_root,
        repo_label=repo_definition.label or repo_root.name,
        variant_name=variant_name,
        strategy=strategy,
        runtime=config.default_runtime,
        policy=policy,
        projects=variant.projects,
        project_discovery=variant.project_discovery,
    )


def _match_repo_definition(
    repo_definitions: dict[str, RankedRepoDefinition],
    repo_record: ProjectRecord,
    ref: str | None,
    path: str | None,
) -> RankedRepoDefinition | None:
    for key, repo in repo_definitions.items():
        if ref is not None:
            if repo.ref and repo.ref.lower() == ref.lower():
                return repo
            if key.lower() == ref.lower():
                return repo
            if repo_record.name and repo_record.name.lower() == key.lower():
                return repo
        if (
            path is not None
            and repo.path
            and Path(repo.path).expanduser().resolve() == Path(path).expanduser().resolve()
        ):
            return repo
        if (
            repo_record.path
            and repo.path
            and Path(repo.path).expanduser().resolve() == Path(repo_record.path).resolve()
        ):
            return repo
    return None


def _auto_strategy(repo_record: ProjectRecord, repo_root: Path | None = None) -> str:
    if repo_record.capabilities.get("elixir_ranked_v1"):
        return "elixir_ranked_v1"
    if repo_record.capabilities.get("python_default_v1"):
        return "python_default_v1"
    if repo_record.capabilities.get("rust_default_v1"):
        return "rust_default_v1"
    if repo_record.capabilities.get("node_default_v1"):
        return "node_default_v1"
    inferred = _infer_strategy_from_repo_root(repo_root)
    if inferred is not None:
        return inferred
    return "generic_default_v1"


def _infer_strategy_from_repo_root(repo_root: Path | None) -> str | None:
    if repo_root is None or not repo_root.is_dir():
        return None
    if (repo_root / "mix.exs").is_file():
        return "elixir_ranked_v1"
    if any((project.abs_path / "mix.exs").is_file() for project in discover_projects(repo_root)):
        return "elixir_ranked_v1"
    if (repo_root / "pyproject.toml").is_file():
        return "python_default_v1"
    if (repo_root / "Cargo.toml").is_file():
        return "rust_default_v1"
    if (repo_root / "package.json").is_file():
        return "node_default_v1"
    return None


def _resolved_strategy_policy(
    strategy: str, strategies: dict[str, RankedPolicy], policy: RankedPolicy
) -> RankedPolicy:
    default_policy = strategies.get(strategy, BUILTIN_STRATEGY_DEFAULTS[strategy])
    return RankedPolicy(
        include_readme=policy.include_readme,
        top_files=policy.top_files if policy.top_files is not None else default_policy.top_files,
        top_percent=(
            policy.top_percent
            if policy.top_percent is not None
            else default_policy.top_percent
        ),
        overscan_limit=(
            policy.overscan_limit
            if policy.overscan_limit is not None
            else default_policy.overscan_limit
        ),
        max_bytes=policy.max_bytes if policy.max_bytes is not None else default_policy.max_bytes,
        max_tokens=(
            policy.max_tokens if policy.max_tokens is not None else default_policy.max_tokens
        ),
        priority_tier=policy.priority_tier,
        exclude_path_prefixes=(
            policy.exclude_path_prefixes or default_policy.exclude_path_prefixes
        ),
        exclude_globs=policy.exclude_globs or default_policy.exclude_globs,
        exclude=policy.exclude,
    )


def _prepare_repo_variant_manifest(
    paths: AtlasPaths,
    resolved: ResolvedRepoVariant,
    *,
    progress: ProgressCallback | None,
) -> tuple[RepoPreparedManifest, bool]:
    manifest_path = _repo_variant_manifest_path(paths, resolved)
    variant_hash = _repo_variant_hash(paths, resolved)
    if manifest_path.is_file():
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and str(payload.get("variant_hash", "")) == variant_hash:
            cached_manifest = _load_repo_prepared_manifest(payload)
            if _repo_manifest_files_present(cached_manifest):
                return cached_manifest, True

    if resolved.strategy == "elixir_ranked_v1":
        manifest = _build_elixir_repo_manifest(
            resolved,
            manifest_path,
            variant_hash,
            progress=progress,
        )
    else:
        manifest = _build_source_repo_manifest(resolved, manifest_path, variant_hash)

    manifest.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest.manifest_path.write_text(
        json.dumps(_repo_prepared_manifest_dict(manifest), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest, False


def _repo_manifest_files_present(manifest: RepoPreparedManifest) -> bool:
    return all(item.abs_path.is_file() for item in manifest.files)


def _build_elixir_repo_manifest(
    resolved: ResolvedRepoVariant,
    manifest_path: Path,
    variant_hash: str,
    *,
    progress: ProgressCallback | None,
) -> RepoPreparedManifest:
    repo_candidates: list[RankedCandidateFile] = []
    project_count = 0
    project_rows: list[ProjectPreparedSelection] = []

    repo_readme = resolved.repo_root / "README.md"
    if (
        resolved.policy.include_readme
        and repo_readme.is_file()
        and _candidate_allowed("README.md", resolved.policy)
    ):
        repo_candidates.append(
            _candidate_for_file(
                repo_readme,
                output_rel=f"{resolved.repo_label}/README.md",
                repo_label=resolved.repo_label,
                project_rel_path=".",
                scope_rel_path="README.md",
            )
        )

    projects = _discover_rankable_projects(resolved.repo_root, resolved.project_discovery)
    unmatched_project_overrides = _unknown_project_overrides(resolved.projects, projects)
    for override_index, override in enumerate(unmatched_project_overrides, start=1):
        _emit_progress(
            progress,
            (
                f"  [override {override_index}/{len(unmatched_project_overrides)}] "
                f"repo={resolved.repo_label} variant={resolved.variant_name} "
                f"step=warn reason=unknown-project-override override={override}"
            ),
        )

    for project_index, project in enumerate(projects, start=1):
        prefix = (
            f"  [project {project_index}/{len(projects)}] {project.rel_path} "
            f"repo={resolved.repo_label} variant={resolved.variant_name}"
        )
        project_policy = resolved.projects.get(project.rel_path, resolved.policy)
        excluded = project.excluded or project_policy.exclude
        exclusion_reason = project.exclusion_reason or (
            "policy_excluded" if project_policy.exclude else None
        )
        if excluded:
            _emit_progress(
                progress,
                f"{prefix} step=skip reason={exclusion_reason or 'excluded'}",
            )
            project_rows.append(
                ProjectPreparedSelection(
                    summary=PreparedProjectSummary(
                        project_rel_path=project.rel_path,
                        category=project.category,
                        excluded=True,
                        exclusion_reason=exclusion_reason or "excluded",
                        selected_count=0,
                        fallback_used=False,
                        shadow_root=None,
                        selected_bytes=0,
                        selected_tokens_estimate=0,
                        priority_tier=project_policy.priority_tier,
                        selection_mode=_selection_mode_for_policy(project_policy),
                    ),
                    files=[],
                )
            )
            continue

        project_count += 1
        project_candidates: list[RankedCandidateFile] = []
        project_readme = project.abs_path / "README.md"
        if (
            project_policy.include_readme
            and project_readme.is_file()
            and _candidate_allowed("README.md", project_policy)
        ):
            rel_readme = project_readme.relative_to(resolved.repo_root).as_posix()
            project_candidates.append(
                _candidate_for_file(
                    project_readme,
                    output_rel=f"{resolved.repo_label}/{rel_readme}",
                    repo_label=resolved.repo_label,
                    project_rel_path=project.rel_path,
                    scope_rel_path="README.md",
                )
            )

        limit = _project_limit(project.abs_path, project_policy)
        if limit <= 0:
            _emit_progress(progress, f"{prefix} step=skip reason=empty-lib")
            project_rows.append(
                ProjectPreparedSelection(
                    summary=PreparedProjectSummary(
                        project_rel_path=project.rel_path,
                        category=project.category,
                        excluded=False,
                        exclusion_reason="empty_lib",
                        selected_count=0,
                        fallback_used=False,
                        shadow_root=None,
                        selected_bytes=0,
                        selected_tokens_estimate=0,
                        priority_tier=project_policy.priority_tier,
                        selection_mode=_selection_mode_for_policy(project_policy),
                    ),
                    files=[],
                )
            )
            continue

        shadow_root = ensure_shadow_project_root(project.abs_path, resolved.runtime.shadow_root)
        ranked_rel_paths, fallback_used = _query_ranked_files(
            project.abs_path,
            resolved.runtime,
            limit,
            project_policy.overscan_limit,
            progress=progress,
            progress_prefix=prefix,
            shadow_root=shadow_root,
        )
        for rel_path in ranked_rel_paths:
            if not _candidate_allowed(rel_path, project_policy):
                continue
            file_path = project.abs_path / rel_path
            rel_to_repo = file_path.relative_to(resolved.repo_root).as_posix()
            project_candidates.append(
                _candidate_for_file(
                    file_path,
                    output_rel=f"{resolved.repo_label}/{rel_to_repo}",
                    repo_label=resolved.repo_label,
                    project_rel_path=project.rel_path,
                    scope_rel_path=rel_path,
                )
            )

        project_selection = _apply_budget_selection(project_candidates, project_policy)
        selected_count = _code_file_count(project_selection.files)
        exclusion_reason = None
        if project_selection.truncated and selected_count == 0 and project_candidates:
            exclusion_reason = "project_budget_exhausted"

        _emit_progress(
            progress,
            f"{prefix} step=selected count={selected_count}"
            + (" fallback=true" if fallback_used else ""),
        )
        project_rows.append(
            ProjectPreparedSelection(
                summary=PreparedProjectSummary(
                    project_rel_path=project.rel_path,
                    category=project.category,
                    excluded=False,
                    exclusion_reason=exclusion_reason,
                    selected_count=selected_count,
                    fallback_used=fallback_used,
                    shadow_root=str(shadow_root),
                    selected_bytes=project_selection.consumed_bytes,
                    selected_tokens_estimate=project_selection.consumed_tokens_estimate,
                    priority_tier=project_policy.priority_tier,
                    selection_mode=_selection_mode_for_policy(project_policy),
                ),
                files=project_selection.files,
            )
        )

    for row in sorted(
        project_rows,
        key=lambda item: (item.summary.priority_tier, item.summary.project_rel_path),
    ):
        repo_candidates.extend(row.files)

    repo_selection = _apply_budget_selection(repo_candidates, resolved.policy)
    selected_files = [_selected_file_from_candidate(item) for item in repo_selection.files]
    selected_by_project: dict[str, list[RankedCandidateFile]] = {}
    for item in repo_selection.files:
        selected_by_project.setdefault(item.project_rel_path, []).append(item)

    project_summaries: list[PreparedProjectSummary] = []
    for row in project_rows:
        final_files = selected_by_project.get(row.summary.project_rel_path, [])
        selected_count = _code_file_count(final_files)
        selected_bytes = sum(item.byte_size for item in final_files)
        selected_tokens = sum(item.token_estimate for item in final_files)
        exclusion_reason = row.summary.exclusion_reason
        if (
            repo_selection.truncated
            and row.files
            and len(final_files) < len(row.files)
            and exclusion_reason in {None, "project_budget_exhausted"}
        ):
            exclusion_reason = "repo_budget_exhausted"
        project_summaries.append(
            PreparedProjectSummary(
                project_rel_path=row.summary.project_rel_path,
                category=row.summary.category,
                excluded=row.summary.excluded,
                exclusion_reason=exclusion_reason,
                selected_count=selected_count,
                fallback_used=row.summary.fallback_used,
                shadow_root=row.summary.shadow_root,
                selected_bytes=selected_bytes,
                selected_tokens_estimate=selected_tokens,
                priority_tier=row.summary.priority_tier,
                selection_mode=row.summary.selection_mode,
            )
        )

    repo_selection_mode = (
        "budget"
        if _selection_mode_for_policy(resolved.policy) == "budget"
        or any(item.selection_mode == "budget" for item in project_summaries)
        else "count"
    )
    return RepoPreparedManifest(
        repo_key=resolved.key,
        repo_label=resolved.repo_label,
        repo_root=resolved.repo_root,
        variant_name=resolved.variant_name,
        strategy=resolved.strategy,
        manifest_path=manifest_path,
        variant_hash=variant_hash,
        prepared_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        files=selected_files,
        project_count=project_count,
        selected_bytes=repo_selection.consumed_bytes,
        selected_tokens_estimate=repo_selection.consumed_tokens_estimate,
        selection_mode=repo_selection_mode,
        projects=project_summaries,
        unmatched_project_overrides=unmatched_project_overrides,
    )


def _build_source_repo_manifest(
    resolved: ResolvedRepoVariant,
    manifest_path: Path,
    variant_hash: str,
) -> RepoPreparedManifest:
    candidates: list[RankedCandidateFile] = []
    repo_readme = resolved.repo_root / "README.md"
    if (
        resolved.policy.include_readme
        and repo_readme.is_file()
        and _candidate_allowed("README.md", resolved.policy)
    ):
        candidates.append(
            _candidate_for_file(
                repo_readme,
                output_rel=f"{resolved.repo_label}/README.md",
                repo_label=resolved.repo_label,
                project_rel_path=".",
                scope_rel_path="README.md",
            )
        )

    for file_path in _ranked_source_files_for_strategy(resolved):
        rel_to_repo = file_path.relative_to(resolved.repo_root).as_posix()
        if not _candidate_allowed(rel_to_repo, resolved.policy):
            continue
        candidates.append(
            _candidate_for_file(
                file_path,
                output_rel=f"{resolved.repo_label}/{rel_to_repo}",
                repo_label=resolved.repo_label,
                project_rel_path=".",
                scope_rel_path=rel_to_repo,
            )
        )

    selected = _apply_budget_selection(candidates, resolved.policy)
    selected_files = [_selected_file_from_candidate(item) for item in selected.files]
    selected_count = _code_file_count(selected.files)

    return RepoPreparedManifest(
        repo_key=resolved.key,
        repo_label=resolved.repo_label,
        repo_root=resolved.repo_root,
        variant_name=resolved.variant_name,
        strategy=resolved.strategy,
        manifest_path=manifest_path,
        variant_hash=variant_hash,
        prepared_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        files=selected_files,
        project_count=1,
        selected_bytes=selected.consumed_bytes,
        selected_tokens_estimate=selected.consumed_tokens_estimate,
        selection_mode=_selection_mode_for_policy(resolved.policy),
        projects=[
            PreparedProjectSummary(
                project_rel_path=".",
                category="root",
                excluded=False,
                exclusion_reason=(
                    "repo_budget_exhausted"
                    if selected.truncated and selected_count == 0
                    else None
                ),
                selected_count=selected_count,
                fallback_used=False,
                shadow_root=None,
                selected_bytes=selected.consumed_bytes,
                selected_tokens_estimate=selected.consumed_tokens_estimate,
                priority_tier=resolved.policy.priority_tier,
                selection_mode=_selection_mode_for_policy(resolved.policy),
            )
        ],
    )


def _ranked_source_files_for_strategy(resolved: ResolvedRepoVariant) -> list[Path]:
    if resolved.strategy == "python_default_v1":
        return _select_ranked_source_candidates(
            resolved.repo_root,
            [".py"],
            preferred_dirs=("src", resolved.repo_root.name, "lib"),
            policy=resolved.policy,
        )
    if resolved.strategy == "rust_default_v1":
        return _select_ranked_source_candidates(
            resolved.repo_root,
            [".rs"],
            preferred_dirs=("src",),
            policy=resolved.policy,
        )
    if resolved.strategy == "node_default_v1":
        return _select_ranked_source_candidates(
            resolved.repo_root,
            [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"],
            preferred_dirs=("src", "lib", "packages"),
            policy=resolved.policy,
        )
    return _select_ranked_source_candidates(
        resolved.repo_root,
        [".py", ".rs", ".go", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".ex", ".exs"],
        preferred_dirs=("src", "lib"),
        policy=resolved.policy,
    )


def _select_ranked_source_candidates(
    repo_root: Path,
    suffixes: list[str],
    *,
    preferred_dirs: tuple[str, ...],
    policy: RankedPolicy,
) -> list[Path]:
    candidates: list[tuple[int, str, Path]] = []
    normalized_suffixes = {suffix.lower() for suffix in suffixes}
    for path in _iter_repo_files(repo_root):
        rel = path.relative_to(repo_root).as_posix()
        if path.suffix.lower() not in normalized_suffixes:
            continue
        if rel.startswith("test/") or rel.startswith("tests/"):
            continue
        priority = len(preferred_dirs)
        for index, prefix in enumerate(preferred_dirs):
            if rel == prefix or rel.startswith(f"{prefix}/"):
                priority = index
                break
        candidates.append((priority, rel, path))

    ordered = [item[2] for item in sorted(candidates, key=lambda row: (row[0], row[1]))]
    return _limit_selected_files(ordered, policy)


def _iter_repo_files(repo_root: Path) -> list[Path]:
    files: list[Path] = []
    for current_root, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [
            name
            for name in sorted(dirnames)
            if name not in SOURCE_SKIP_DIRS and not name.startswith(".")
        ]
        for filename in sorted(filenames):
            path = Path(current_root) / filename
            if path.is_file():
                files.append(path)
    return files


def _limit_selected_files(paths: list[Path], policy: RankedPolicy) -> list[Path]:
    if not paths:
        return []
    if policy.top_files is not None:
        return paths[: min(policy.top_files, len(paths))]
    assert policy.top_percent is not None
    limit = min(max(1, math.ceil(len(paths) * policy.top_percent)), len(paths))
    return paths[:limit]


def _unknown_project_overrides(
    project_overrides: dict[str, RankedPolicy], projects: list[RankableProject]
) -> list[str]:
    known = {project.rel_path for project in projects}
    return sorted(set(project_overrides) - known)


def _repo_variant_manifest_path(paths: AtlasPaths, resolved: ResolvedRepoVariant) -> Path:
    safe_key = re.sub(r"[^A-Za-z0-9._-]+", "_", resolved.key).strip("._-") or "repo"
    safe_variant = re.sub(r"[^A-Za-z0-9._-]+", "_", resolved.variant_name).strip("._-") or "default"
    digest = hashlib.sha256(
        f"{resolved.repo_record.path}\0{resolved.variant_name}".encode()
    ).hexdigest()[:10]
    return paths.ranked_context_cache_root / "repos" / f"{safe_key}-{safe_variant}-{digest}.json"


def _repo_variant_hash(paths: AtlasPaths, resolved: ResolvedRepoVariant) -> str:
    payload = {
        "repo_record": asdict(resolved.repo_record),
        "variant": {
            "key": resolved.key,
            "variant_name": resolved.variant_name,
            "strategy": resolved.strategy,
            "policy": asdict(resolved.policy),
            "projects": {name: asdict(policy) for name, policy in resolved.projects.items()},
            "project_discovery": asdict(resolved.project_discovery),
        },
        "registry_hash": _registry_hash(paths),
    }
    return _sha256_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _repo_prepared_manifest_dict(manifest: RepoPreparedManifest) -> dict[str, object]:
    return {
        "version": 1,
        "repo_key": manifest.repo_key,
        "repo_label": manifest.repo_label,
        "repo_root": str(manifest.repo_root),
        "variant_name": manifest.variant_name,
        "strategy": manifest.strategy,
        "manifest_path": str(manifest.manifest_path),
        "variant_hash": manifest.variant_hash,
        "prepared_at": manifest.prepared_at,
        "project_count": manifest.project_count,
        "selected_bytes": manifest.selected_bytes,
        "selected_tokens_estimate": manifest.selected_tokens_estimate,
        "selection_mode": manifest.selection_mode,
        "unmatched_project_overrides": manifest.unmatched_project_overrides,
        "projects": [_prepared_project_summary_dict(item) for item in manifest.projects],
        "files": [
            {
                "path": str(item.abs_path),
                "output_path": f"./{item.output_rel}",
                "repo_label": item.repo_label,
                "project_rel_path": item.project_rel_path,
                "byte_size": item.byte_size,
                "token_estimate": item.token_estimate,
            }
            for item in manifest.files
        ],
    }


def _load_repo_prepared_manifest(payload: dict[str, Any]) -> RepoPreparedManifest:
    files_payload = payload.get("files", [])
    files: list[RankedSelectedFile] = []
    for item in files_payload:
        files.append(
            RankedSelectedFile(
                abs_path=Path(str(item["path"])).expanduser().resolve(),
                output_rel=_strip_output_prefix(str(item["output_path"])),
                repo_label=str(item.get("repo_label", "")),
                project_rel_path=str(item.get("project_rel_path", ".")),
                byte_size=int(item.get("byte_size", 0)),
                token_estimate=int(item.get("token_estimate", 0)),
            )
        )
    return RepoPreparedManifest(
        repo_key=str(payload["repo_key"]),
        repo_label=str(payload["repo_label"]),
        repo_root=Path(str(payload["repo_root"])).expanduser().resolve(),
        variant_name=str(payload["variant_name"]),
        strategy=str(payload["strategy"]),
        manifest_path=Path(str(payload["manifest_path"])).expanduser().resolve(),
        variant_hash=str(payload["variant_hash"]),
        prepared_at=str(payload["prepared_at"]),
        files=files,
        project_count=int(payload.get("project_count", 0)),
        selected_bytes=int(payload.get("selected_bytes", 0)),
        selected_tokens_estimate=int(payload.get("selected_tokens_estimate", 0)),
        selection_mode=str(payload.get("selection_mode", "count")),
        unmatched_project_overrides=[
            str(item).strip()
            for item in payload.get("unmatched_project_overrides", [])
            if str(item).strip()
        ],
        projects=[
            _load_prepared_project_summary(item)
            for item in payload.get("projects", [])
            if isinstance(item, dict)
        ],
    )


def _prepared_project_summary_dict(summary: PreparedProjectSummary) -> dict[str, object]:
    return {
        "project_rel_path": summary.project_rel_path,
        "category": summary.category,
        "excluded": summary.excluded,
        "exclusion_reason": summary.exclusion_reason,
        "selected_count": summary.selected_count,
        "fallback_used": summary.fallback_used,
        "shadow_root": summary.shadow_root,
        "selected_bytes": summary.selected_bytes,
        "selected_tokens_estimate": summary.selected_tokens_estimate,
        "priority_tier": summary.priority_tier,
        "selection_mode": summary.selection_mode,
    }


def _load_prepared_project_summary(payload: dict[str, Any]) -> PreparedProjectSummary:
    return PreparedProjectSummary(
        project_rel_path=str(payload.get("project_rel_path", ".")),
        category=str(payload.get("category", "root")),
        excluded=bool(payload.get("excluded", False)),
        exclusion_reason=_optional_string(payload.get("exclusion_reason")),
        selected_count=int(payload.get("selected_count", 0)),
        fallback_used=bool(payload.get("fallback_used", False)),
        shadow_root=_optional_string(payload.get("shadow_root")),
        selected_bytes=int(payload.get("selected_bytes", 0)),
        selected_tokens_estimate=int(payload.get("selected_tokens_estimate", 0)),
        priority_tier=int(payload.get("priority_tier", 100)),
        selection_mode=str(payload.get("selection_mode", "count")),
    )


def _repo_manifest_summary(manifest: RepoPreparedManifest) -> PreparedRepoSummary:
    return PreparedRepoSummary(
        repo_key=manifest.repo_key,
        repo_label=manifest.repo_label,
        repo_root=manifest.repo_root,
        variant_name=manifest.variant_name,
        strategy=manifest.strategy,
        project_count=manifest.project_count,
        projects=manifest.projects,
        selected_bytes=manifest.selected_bytes,
        selected_tokens_estimate=manifest.selected_tokens_estimate,
        selection_mode=manifest.selection_mode,
        unmatched_project_overrides=manifest.unmatched_project_overrides,
    )


def _prepared_repo_summary_dict(summary: PreparedRepoSummary) -> dict[str, object]:
    return {
        "repo_key": summary.repo_key,
        "repo_label": summary.repo_label,
        "repo_root": str(summary.repo_root),
        "variant_name": summary.variant_name,
        "strategy": summary.strategy,
        "project_count": summary.project_count,
        "projects": [_prepared_project_summary_dict(item) for item in summary.projects],
        "selected_bytes": summary.selected_bytes,
        "selected_tokens_estimate": summary.selected_tokens_estimate,
        "selection_mode": summary.selection_mode,
        "unmatched_project_overrides": summary.unmatched_project_overrides,
    }


def _load_prepared_repo_summary(payload: dict[str, Any]) -> PreparedRepoSummary:
    return PreparedRepoSummary(
        repo_key=str(payload.get("repo_key", "")),
        repo_label=str(payload.get("repo_label", "")),
        repo_root=Path(str(payload.get("repo_root", "."))).expanduser().resolve(),
        variant_name=str(payload.get("variant_name", "default")),
        strategy=str(payload.get("strategy", "")),
        project_count=int(payload.get("project_count", 0)),
        selected_bytes=int(payload.get("selected_bytes", 0)),
        selected_tokens_estimate=int(payload.get("selected_tokens_estimate", 0)),
        selection_mode=str(payload.get("selection_mode", "count")),
        unmatched_project_overrides=[
            str(item).strip()
            for item in payload.get("unmatched_project_overrides", [])
            if str(item).strip()
        ],
        projects=[
            _load_prepared_project_summary(item)
            for item in payload.get("projects", [])
            if isinstance(item, dict)
        ],
    )


def _render_ranked_bundle_from_prepared(prepared: RankedPreparedManifest) -> RankedBundle:
    parts: list[str] = []
    ordered_files: list[Path] = []
    seen_files: set[Path] = set()
    for item in prepared.files:
        _append_file(parts, ordered_files, seen_files, item.abs_path, item.output_rel)
    return RankedBundle(
        config_name=prepared.config_name,
        files=ordered_files,
        text="".join(parts),
        source_roots=prepared.source_roots,
    )
def _parse_runtime(
    payload: dict[str, Any],
    config_path: Path,
    *,
    paths: AtlasPaths,
    fallback: RankedRuntime | None = None,
) -> RankedRuntime:
    if fallback is None:
        dexterity_root = _optional_string(payload.get("dexterity_root"))
        if dexterity_root is None:
            raise SystemExit(f"{config_path} defaults.dexterity_root is required")
        shadow_root = _optional_string(payload.get("shadow_root"))
        return RankedRuntime(
            dexterity_root=Path(dexterity_root).expanduser().resolve(),
            dexter_bin=_optional_string(payload.get("dexter_bin")) or "dexter",
            shadow_root=(
                Path(shadow_root).expanduser().resolve()
                if shadow_root is not None
                else (paths.state_home / "code" / "shadows").resolve()
            ),
        )

    dexterity_root = _optional_string(payload.get("dexterity_root"))
    shadow_root = _optional_string(payload.get("shadow_root"))
    return RankedRuntime(
        dexterity_root=Path(dexterity_root).expanduser().resolve()
        if dexterity_root
        else fallback.dexterity_root,
        dexter_bin=_optional_string(payload.get("dexter_bin")) or fallback.dexter_bin,
        shadow_root=Path(shadow_root).expanduser().resolve()
        if shadow_root
        else fallback.shadow_root,
    )


def _parse_policy(
    payload: dict[str, Any],
    label: str,
    *,
    fallback: RankedPolicy | None = None,
    default: bool = False,
    allow_exclude: bool = False,
) -> RankedPolicy:
    top_files = payload.get("top_files")
    top_percent = payload.get("top_percent")
    if top_files is not None and top_percent is not None:
        raise SystemExit(f"{label} cannot set both top_files and top_percent")

    if top_files is not None:
        resolved_top_files = _parse_positive_int(top_files, f"{label}.top_files")
        resolved_top_percent = None
    elif top_percent is not None:
        resolved_top_files = None
        resolved_top_percent = _parse_percent(top_percent, f"{label}.top_percent")
    elif fallback is not None:
        resolved_top_files = fallback.top_files
        resolved_top_percent = fallback.top_percent
    elif default:
        resolved_top_files = 10
        resolved_top_percent = None
    else:
        raise SystemExit(f"{label} must set top_files or top_percent")

    include_readme = (
        _parse_bool(payload["include_readme"], f"{label}.include_readme")
        if "include_readme" in payload
        else (fallback.include_readme if fallback is not None else True)
    )
    overscan_limit = (
        _parse_positive_int(payload["overscan_limit"], f"{label}.overscan_limit")
        if "overscan_limit" in payload
        else (fallback.overscan_limit if fallback is not None else None)
    )
    max_bytes = (
        _parse_positive_int(payload["max_bytes"], f"{label}.max_bytes")
        if "max_bytes" in payload
        else (fallback.max_bytes if fallback is not None else None)
    )
    max_tokens = (
        _parse_positive_int(payload["max_tokens"], f"{label}.max_tokens")
        if "max_tokens" in payload
        else (fallback.max_tokens if fallback is not None else None)
    )
    priority_tier = (
        _parse_positive_int(payload["priority_tier"], f"{label}.priority_tier")
        if "priority_tier" in payload
        else (fallback.priority_tier if fallback is not None else 100)
    )
    exclude_path_prefixes = _parse_string_tuple(
        payload.get(
            "exclude_path_prefixes",
            fallback.exclude_path_prefixes if fallback is not None else (),
        ),
        f"{label}.exclude_path_prefixes",
        fallback.exclude_path_prefixes if fallback is not None else (),
    )
    exclude_globs = _parse_string_tuple(
        payload.get("exclude_globs", fallback.exclude_globs if fallback is not None else ()),
        f"{label}.exclude_globs",
        fallback.exclude_globs if fallback is not None else (),
    )
    exclude = (
        _parse_bool(payload.get("exclude", False), f"{label}.exclude") if allow_exclude else False
    )

    return RankedPolicy(
        include_readme=include_readme,
        top_files=resolved_top_files,
        top_percent=resolved_top_percent,
        overscan_limit=overscan_limit,
        max_bytes=max_bytes,
        max_tokens=max_tokens,
        priority_tier=priority_tier,
        exclude_path_prefixes=exclude_path_prefixes,
        exclude_globs=exclude_globs,
        exclude=exclude,
    )


def _parse_project_discovery(
    payload: object,
    label: str,
    *,
    fallback: RankedProjectDiscovery,
) -> RankedProjectDiscovery:
    payload_dict = _as_dict(payload, label)
    _validate_keys(
        label,
        payload_dict,
        {
            "exclude_path_prefixes",
            "include_path_prefixes",
            "exclude_categories",
            "include_categories",
        },
    )
    return RankedProjectDiscovery(
        exclude_path_prefixes=_parse_string_tuple(
            payload_dict.get("exclude_path_prefixes", fallback.exclude_path_prefixes),
            f"{label}.exclude_path_prefixes",
            fallback.exclude_path_prefixes,
        ),
        include_path_prefixes=_parse_string_tuple(
            payload_dict.get("include_path_prefixes", fallback.include_path_prefixes),
            f"{label}.include_path_prefixes",
            fallback.include_path_prefixes,
        ),
        exclude_categories=_parse_string_tuple(
            payload_dict.get("exclude_categories", fallback.exclude_categories),
            f"{label}.exclude_categories",
            fallback.exclude_categories,
        ),
        include_categories=_parse_string_tuple(
            payload_dict.get("include_categories", fallback.include_categories),
            f"{label}.include_categories",
            fallback.include_categories,
        ),
    )
def _discover_rankable_projects(
    repo_root: Path, discovery: RankedProjectDiscovery
) -> list[RankableProject]:
    projects: list[RankableProject] = []
    for project in discover_projects(repo_root):
        if not (project.abs_path / "mix.exs").is_file():
            continue
        category = _project_category(project.rel_path)
        excluded, reason = _project_exclusion(project.rel_path, category, discovery)
        projects.append(
            RankableProject(
                rel_path=project.rel_path,
                abs_path=project.abs_path,
                category=category,
                excluded=excluded,
                exclusion_reason=reason,
            )
        )
    return projects
def _project_limit(project_root: Path, policy: RankedPolicy) -> int:
    lib_files = _lib_files(project_root)
    if not lib_files:
        return 0
    if policy.top_files is not None:
        return min(policy.top_files, len(lib_files))
    assert policy.top_percent is not None
    return min(max(1, math.ceil(len(lib_files) * policy.top_percent)), len(lib_files))


def _lib_files(project_root: Path) -> list[Path]:
    return [
        path
        for path in iter_regular_files(project_root / "lib")
        if path.suffix.lower() in ELIXIR_SUFFIXES
    ]


def _query_ranked_files(
    project_root: Path,
    runtime: RankedRuntime,
    limit: int,
    overscan_limit: int | None,
    *,
    progress: ProgressCallback | None = None,
    progress_prefix: str = "",
    shadow_root: Path | None = None,
) -> tuple[list[str], bool]:
    runtime.dexterity_root.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    query_root = shadow_root or project_root

    _emit_progress(progress, f"{progress_prefix} step=index")
    index_cmd = [
        "mix",
        "dexterity.index",
        "--repo-root",
        str(query_root),
        "--dexter-bin",
        runtime.dexter_bin,
    ]
    index_result = subprocess.run(
        index_cmd,
        cwd=str(runtime.dexterity_root),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if index_result.returncode != 0:
        raise SystemExit(
            index_result.stderr.strip()
            or index_result.stdout.strip()
            or f"dexterity.index failed for {project_root}"
        )

    _emit_progress(progress, f"{progress_prefix} step=query")
    query_cmd = [
        "mix",
        "dexterity.query",
        "ranked_files",
        "--repo-root",
        str(query_root),
        "--dexter-bin",
        runtime.dexter_bin,
        "--include-prefix",
        "lib/",
        "--exclude-prefix",
        "deps/",
        "--limit",
        str(limit),
        "--json",
    ]
    if overscan_limit is not None:
        query_cmd.extend(["--overscan-limit", str(overscan_limit)])

    query_result = subprocess.run(
        query_cmd,
        cwd=str(runtime.dexterity_root),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if query_result.returncode != 0:
        raise SystemExit(
            query_result.stderr.strip()
            or query_result.stdout.strip()
            or f"dexterity.query ranked_files failed for {project_root}"
        )

    payload = json.loads(query_result.stdout)
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise SystemExit(f"Invalid ranked_files response for {project_root}")

    result = payload.get("result", [])
    if not isinstance(result, list):
        raise SystemExit(f"Invalid ranked_files payload for {project_root}")

    ranked_paths: list[str] = []
    seen: set[str] = set()
    for item in result:
        if not (isinstance(item, list) and len(item) == 2 and isinstance(item[0], str)):
            raise SystemExit(f"Invalid ranked_files row for {project_root}: {item!r}")
        rel_path = item[0]
        if rel_path in seen:
            continue
        if not rel_path.startswith("lib/"):
            continue
        full_path = project_root / rel_path
        if not full_path.is_file() or full_path.suffix.lower() not in ELIXIR_SUFFIXES:
            continue
        ranked_paths.append(rel_path)
        seen.add(rel_path)

    if ranked_paths:
        return ranked_paths[:limit], False

    return _fallback_ranked_files(project_root, limit), True


def _append_selected_file(
    selected_files: list[RankedSelectedFile],
    seen_files: set[Path],
    file_path: Path,
    output_rel: str,
    repo_label: str,
    project_rel_path: str,
    *,
    byte_size: int = 0,
    token_estimate: int = 0,
) -> None:
    resolved = file_path.resolve()
    if resolved in seen_files:
        return
    seen_files.add(resolved)
    selected_files.append(
        RankedSelectedFile(
            abs_path=resolved,
            output_rel=output_rel,
            repo_label=repo_label,
            project_rel_path=project_rel_path,
            byte_size=byte_size,
            token_estimate=token_estimate,
        )
    )


def _candidate_for_file(
    file_path: Path,
    *,
    output_rel: str,
    repo_label: str,
    project_rel_path: str,
    scope_rel_path: str,
) -> RankedCandidateFile:
    resolved = file_path.resolve()
    text = read_text(resolved)
    return RankedCandidateFile(
        abs_path=resolved,
        output_rel=output_rel,
        repo_label=repo_label,
        project_rel_path=project_rel_path,
        scope_rel_path=scope_rel_path,
        byte_size=resolved.stat().st_size,
        token_estimate=_estimate_text_tokens(text),
    )


def _estimate_text_tokens(text: str) -> int:
    if not text:
        return 0
    return math.ceil(len(text) / 4)


def _selection_mode_for_policy(policy: RankedPolicy) -> str:
    if policy.max_bytes is not None or policy.max_tokens is not None:
        return "budget"
    return "count"


def _candidate_allowed(scope_rel_path: str, policy: RankedPolicy) -> bool:
    normalized = scope_rel_path.lstrip("./")
    for prefix in policy.exclude_path_prefixes:
        candidate_prefix = prefix.strip().lstrip("./").rstrip("/")
        if not candidate_prefix:
            continue
        if normalized == candidate_prefix or normalized.startswith(f"{candidate_prefix}/"):
            return False
    for pattern in policy.exclude_globs:
        cleaned = pattern.strip()
        if cleaned and fnmatch.fnmatch(normalized, cleaned):
            return False
    return True


def _apply_budget_selection(
    candidates: list[RankedCandidateFile],
    policy: RankedPolicy,
) -> BudgetSelection:
    selected: list[RankedCandidateFile] = []
    consumed_bytes = 0
    consumed_tokens = 0
    truncated = False

    for candidate in candidates:
        next_bytes = consumed_bytes + candidate.byte_size
        next_tokens = consumed_tokens + candidate.token_estimate
        if policy.max_bytes is not None and next_bytes > policy.max_bytes:
            truncated = True
            continue
        if policy.max_tokens is not None and next_tokens > policy.max_tokens:
            truncated = True
            continue
        selected.append(candidate)
        consumed_bytes = next_bytes
        consumed_tokens = next_tokens

    return BudgetSelection(
        files=selected,
        consumed_bytes=consumed_bytes,
        consumed_tokens_estimate=consumed_tokens,
        truncated=truncated,
    )


def _selected_file_from_candidate(candidate: RankedCandidateFile) -> RankedSelectedFile:
    return RankedSelectedFile(
        abs_path=candidate.abs_path,
        output_rel=candidate.output_rel,
        repo_label=candidate.repo_label,
        project_rel_path=candidate.project_rel_path,
        byte_size=candidate.byte_size,
        token_estimate=candidate.token_estimate,
    )


def _code_file_count(candidates: list[RankedCandidateFile]) -> int:
    return sum(1 for item in candidates if Path(item.scope_rel_path).name.lower() != "readme.md")


def _append_file(
    parts: list[str],
    ordered_files: list[Path],
    seen_files: set[Path],
    file_path: Path,
    output_rel: str,
) -> None:
    resolved = file_path.resolve()
    if resolved in seen_files:
        return
    seen_files.add(resolved)
    ordered_files.append(resolved)

    contents = read_text(resolved)
    parts.append(f"# FILE: ./{output_rel}\n")
    parts.append(contents)
    if not contents.endswith("\n"):
        parts.append("\n")


def _emit_progress(progress: ProgressCallback | None, message: str) -> None:
    if progress is not None:
        progress(message)


def _fallback_ranked_files(project_root: Path, limit: int) -> list[str]:
    return [
        path.relative_to(project_root).as_posix()
        for path in sorted(
            _lib_files(project_root), key=lambda item: item.relative_to(project_root).as_posix()
        )
    ][:limit]


def _project_category(rel_path: str) -> str:
    if rel_path == ".":
        return "root"
    head = rel_path.split("/", 1)[0].lower()
    return {
        "apps": "app",
        "bridges": "bridge",
        "connectors": "connector",
        "core": "core",
        "_legacy": "legacy",
        "legacy": "legacy",
        "test": "test",
        "tests": "test",
        "fixtures": "fixture",
        "examples": "example",
        "example": "example",
        "support": "support",
        "tmp": "tmp",
        "dist": "dist",
        "deps": "dependency",
        "doc": "doc",
        "docs": "doc",
        "bench": "bench",
        "vendor": "vendor",
    }.get(head, head)


def _project_exclusion(
    rel_path: str,
    category: str,
    discovery: RankedProjectDiscovery,
) -> tuple[bool, str | None]:
    if rel_path == ".":
        return False, None
    if any(
        rel_path == prefix.rstrip("/") or rel_path.startswith(prefix)
        for prefix in discovery.include_path_prefixes
    ):
        return False, None
    if category in discovery.include_categories:
        return False, None
    for prefix in discovery.exclude_path_prefixes:
        normalized = prefix.rstrip("/")
        if (
            rel_path == normalized
            or rel_path.startswith(f"{normalized}/")
            or rel_path.startswith(prefix)
        ):
            return True, f"path_prefix:{normalized}"
    if category in discovery.exclude_categories:
        return True, f"category:{category}"
    return False, None


def _parse_string_tuple(value: object, label: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return fallback
    if not isinstance(value, list | tuple):
        raise SystemExit(f"{label} must be an array")
    items: list[str] = []
    for item in value:
        text = _optional_string(item)
        if text is not None:
            items.append(text)
    return tuple(items)


def _budget_payload_has_value(payload: dict[str, Any], key: str) -> bool:
    budget_payload = payload.get("budget")
    return isinstance(budget_payload, dict) and budget_payload.get(key) is not None


def _path_within_root(path: Path, root: Path) -> bool:
    resolved_path = path.resolve()
    resolved_root = root.expanduser().resolve()
    return resolved_path == resolved_root or resolved_root in resolved_path.parents


def _validate_keys(label: str, payload: dict[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise SystemExit(f"{label} has unknown key(s): {', '.join(unknown)}")


def _as_dict(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SystemExit(f"{label} must be an object")
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_bool(value: object, label: str) -> bool:
    if isinstance(value, bool):
        return value
    raise SystemExit(f"{label} must be true or false")


def _parse_positive_int(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise SystemExit(f"{label} must be a positive integer")
    if not isinstance(value, int | float | str):
        raise SystemExit(f"{label} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"{label} must be a positive integer") from exc
    if parsed <= 0:
        raise SystemExit(f"{label} must be a positive integer")
    return parsed


def _parse_percent(value: object, label: str) -> float:
    if isinstance(value, bool):
        raise SystemExit(f"{label} must be a float between 0 and 1")
    if not isinstance(value, int | float | str):
        raise SystemExit(f"{label} must be a float between 0 and 1")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"{label} must be a float between 0 and 1") from exc
    if parsed <= 0 or parsed > 1:
        raise SystemExit(f"{label} must be a float between 0 and 1")
    return parsed


def _render_ranked_contexts(payload: dict[str, object]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_ranked_contexts_text(config_path: Path, text: str) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(text, encoding="utf-8")


def _prepared_manifest_path(paths: AtlasPaths, config_name: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", config_name).strip("._-") or "ranked"
    digest = hashlib.sha256(config_name.encode("utf-8")).hexdigest()[:10]
    return paths.ranked_context_cache_root / f"{safe}-{digest}.json"


def _ranked_config_hash(paths: AtlasPaths, config_name: str) -> str:
    payload = load_ranked_contexts_payload(paths)
    groups = payload.get("groups")
    if not isinstance(groups, dict) or config_name not in groups:
        raise SystemExit(f"Unknown ranked context config: {config_name}")
    relevant = {
        "version": payload.get("version"),
        "defaults": payload.get("defaults"),
        "repos": payload.get("repos"),
        "group": groups[config_name],
        "registry_hash": _registry_hash(paths),
    }
    return _sha256_text(json.dumps(relevant, indent=2, sort_keys=True) + "\n")


def _registry_hash(paths: AtlasPaths) -> str:
    registry = [asdict(record) for record in load_registry(paths)]
    return _sha256_text(json.dumps(registry, indent=2, sort_keys=True) + "\n")


def _strip_output_prefix(output_path: str) -> str:
    return output_path[2:] if output_path.startswith("./") else output_path


def _missing_ranked_contexts_message(config_path: Path) -> str:
    return (
        f"Missing ranked context config: {config_path}. "
        "Run `atlas install` or `atlas config ranked install`."
    )


def _missing_prepared_manifest_message(config_name: str, manifest_path: Path) -> str:
    return (
        f"Missing prepared ranked context manifest: {manifest_path}. "
        f"`atlas context ranked {config_name}` will prepare it automatically; "
        f"run `atlas context ranked prepare {config_name}` only to prewarm explicitly."
    )


def _stale_prepared_manifest_message(config_name: str) -> str:
    return (
        f"Prepared ranked context stale for {config_name}: ranked config changed. "
        f"`atlas context ranked {config_name}` will prepare it automatically; "
        f"run `atlas context ranked prepare {config_name}` only to prewarm explicitly."
    )
