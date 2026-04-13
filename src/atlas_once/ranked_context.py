from __future__ import annotations

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
from .mix_ctx import Project, discover_projects, iter_regular_files
from .profiles import get_ranked_context_template
from .registry import (
    ProjectRecord,
    load_registry,
    manual_project,
    resolve_project_ref,
    scan_registry,
)
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


@dataclass(frozen=True)
class RankedPolicy:
    include_readme: bool
    top_files: int | None
    top_percent: float | None
    overscan_limit: int | None
    exclude: bool = False


@dataclass(frozen=True)
class RankedRuntime:
    dexterity_root: Path
    dexter_bin: str


@dataclass(frozen=True)
class RankedRepoConfig:
    path: str | None
    ref: str | None
    label: str | None
    policy: RankedPolicy
    projects: dict[str, RankedPolicy]


@dataclass(frozen=True)
class RankedConfig:
    name: str
    runtime: RankedRuntime
    default_policy: RankedPolicy
    repos: list[RankedRepoConfig]


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
    repo_manifest_paths: list[str] = field(default_factory=list)


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
    variant: str


@dataclass(frozen=True)
class RankedRepoVariantConfig:
    strategy: str | None
    policy: RankedPolicy
    projects: dict[str, RankedPolicy]


@dataclass(frozen=True)
class RankedRepoDefinition:
    key: str
    path: str | None
    ref: str | None
    label: str | None
    default_variant: RankedRepoVariantConfig
    variants: dict[str, RankedRepoVariantConfig]


@dataclass(frozen=True)
class RankedGroupConfigV2:
    name: str
    items: list[RankedGroupItem]
    selectors: list[RankedGroupSelector]


@dataclass(frozen=True)
class RankedRegistryDefaults:
    self_owners: list[str]


@dataclass(frozen=True)
class RankedConfigV2:
    default_runtime: RankedRuntime
    registry: RankedRegistryDefaults
    strategies: dict[str, RankedPolicy]
    repos: dict[str, RankedRepoDefinition]
    groups: dict[str, RankedGroupConfigV2]


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


ProgressCallback = Callable[[str], None]


BUILTIN_STRATEGY_DEFAULTS: dict[str, RankedPolicy] = {
    "elixir_ranked_v1": RankedPolicy(
        include_readme=True,
        top_files=10,
        top_percent=None,
        overscan_limit=50,
    ),
    "python_default_v1": RankedPolicy(
        include_readme=True,
        top_files=10,
        top_percent=None,
        overscan_limit=None,
    ),
    "rust_default_v1": RankedPolicy(
        include_readme=True,
        top_files=10,
        top_percent=None,
        overscan_limit=None,
    ),
    "node_default_v1": RankedPolicy(
        include_readme=True,
        top_files=10,
        top_percent=None,
        overscan_limit=None,
    ),
    "generic_default_v1": RankedPolicy(
        include_readme=True,
        top_files=10,
        top_percent=None,
        overscan_limit=None,
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


def load_ranked_configs(paths: AtlasPaths) -> dict[str, RankedConfig]:
    config_path = paths.ranked_contexts_path
    payload = load_ranked_contexts_payload(paths)
    _validate_keys("ranked context config", payload, {"version", "defaults", "configs"})

    if int(payload.get("version", 0)) != 1:
        raise SystemExit("ranked context config version must be 1")

    defaults_payload = _as_dict(payload.get("defaults"), "defaults")
    configs_payload = _as_dict(payload.get("configs"), "configs")

    default_runtime = _parse_runtime(defaults_payload, config_path)
    default_policy = _parse_policy(defaults_payload, "defaults", default=True)

    configs: dict[str, RankedConfig] = {}
    for name, item in configs_payload.items():
        config_payload = _as_dict(item, f"configs.{name}")
        _validate_keys(
            f"configs.{name}",
            config_payload,
            {
                "dexterity_root",
                "dexter_bin",
                "include_readme",
                "top_files",
                "top_percent",
                "overscan_limit",
                "repos",
            },
        )

        runtime = _parse_runtime(config_payload, config_path, fallback=default_runtime)
        policy = _parse_policy(config_payload, f"configs.{name}", fallback=default_policy)
        repos_payload = config_payload.get("repos")
        if not isinstance(repos_payload, list) or not repos_payload:
            raise SystemExit(f"configs.{name}.repos must be a non-empty list")

        repos = [
            _parse_repo(repo_item, name, index, policy)
            for index, repo_item in enumerate(repos_payload)
        ]
        configs[name] = RankedConfig(name=name, runtime=runtime, default_policy=policy, repos=repos)

    return configs


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
            )
        )

    source_roots_payload = payload.get("source_roots", [])
    if not isinstance(source_roots_payload, list):
        raise SystemExit(f"Invalid prepared ranked manifest source_roots: {manifest_path}")

    return RankedPreparedManifest(
        config_name=config_name,
        manifest_path=manifest_path,
        config_hash=str(payload["config_hash"]),
        prepared_at=str(payload["prepared_at"]),
        files=files,
        source_roots=[Path(str(item)).expanduser().resolve() for item in source_roots_payload],
        repo_count=int(payload.get("repo_count", 0)),
        project_count=int(payload.get("project_count", 0)),
        repo_manifest_paths=[str(item) for item in payload.get("repo_manifest_paths", [])],
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
        "source_roots": [str(path) for path in prepared.source_roots],
        "repo_manifest_paths": prepared.repo_manifest_paths,
        "files": [
            {
                "path": str(item.abs_path),
                "output_path": f"./{item.output_rel}",
                "repo_label": item.repo_label,
                "project_rel_path": item.project_rel_path,
            }
            for item in prepared.files
        ],
    }


def render_prepared_ranked_bundle(paths: AtlasPaths, config_name: str) -> RankedBundle:
    current_hash = _ranked_config_hash(paths, config_name)
    prepared = load_prepared_ranked_manifest(paths, config_name)
    if prepared.config_hash != current_hash:
        raise SystemExit(_stale_prepared_manifest_message(config_name))

    parts: list[str] = []
    ordered_files: list[Path] = []
    seen_files: set[Path] = set()

    for item in prepared.files:
        if not item.abs_path.is_file():
            raise SystemExit(
                f"Prepared ranked context stale for {config_name}: missing file {item.abs_path}. "
                f"Run `atlas context ranked prepare {config_name}`."
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


def _build_prepared_manifest(
    paths: AtlasPaths,
    config_name: str,
    *,
    progress: ProgressCallback | None,
) -> RankedPreparedManifest:
    payload = load_ranked_contexts_payload(paths)
    if int(payload.get("version", 0)) == 2:
        return _build_prepared_manifest_v2(paths, config_name, progress=progress)

    configs = load_ranked_configs(paths)
    try:
        config = configs[config_name]
    except KeyError as exc:
        raise SystemExit(f"Unknown ranked context config: {config_name}") from exc

    selected_files: list[RankedSelectedFile] = []
    seen_files: set[Path] = set()
    source_roots: list[Path] = []
    included_project_count = 0
    repo_total = len(config.repos)

    for repo_index, repo in enumerate(config.repos, start=1):
        repo_root = _resolve_repo_root(paths, repo)
        source_roots.append(repo_root)
        repo_label = repo.label or repo_root.name
        _emit_progress(progress, f"[repo {repo_index}/{repo_total}] {repo_label}")

        repo_readme = repo_root / "README.md"
        if repo.policy.include_readme and repo_readme.is_file():
            _append_selected_file(
                selected_files,
                seen_files,
                repo_readme,
                f"{repo_label}/README.md",
                repo_label,
                ".",
            )

        projects = _discover_rankable_projects(repo_root)
        _validate_project_overrides(repo, projects, repo_root)

        for project_index, project in enumerate(projects, start=1):
            prefix = (
                f"  [project {project_index}/{len(projects)}] {project.rel_path}"
                f" repo={repo_label}"
            )
            project_policy = repo.projects.get(project.rel_path, repo.policy)
            if project_policy.exclude:
                _emit_progress(progress, f"{prefix} step=skip reason=excluded")
                continue

            included_project_count += 1
            project_readme = project.abs_path / "README.md"
            if project_policy.include_readme and project_readme.is_file():
                rel_readme = project_readme.relative_to(repo_root).as_posix()
                _append_selected_file(
                    selected_files,
                    seen_files,
                    project_readme,
                    f"{repo_label}/{rel_readme}",
                    repo_label,
                    project.rel_path,
                )

            limit = _project_limit(project.abs_path, project_policy)
            if limit <= 0:
                _emit_progress(progress, f"{prefix} step=skip reason=empty-lib")
                continue

            ranked_rel_paths, fallback_used = _query_ranked_files(
                project.abs_path,
                config.runtime,
                limit,
                project_policy.overscan_limit,
                progress=progress,
                progress_prefix=prefix,
            )
            for rel_path in ranked_rel_paths:
                file_path = project.abs_path / rel_path
                rel_to_repo = file_path.relative_to(repo_root).as_posix()
                _append_selected_file(
                    selected_files,
                    seen_files,
                    file_path,
                    f"{repo_label}/{rel_to_repo}",
                    repo_label,
                    project.rel_path,
                )
            _emit_progress(
                progress,
                f"{prefix} step=selected count={len(ranked_rel_paths)}"
                + (" fallback=true" if fallback_used else ""),
            )

    manifest_path = _prepared_manifest_path(paths, config_name)
    return RankedPreparedManifest(
        config_name=config_name,
        manifest_path=manifest_path,
        config_hash=_ranked_config_hash(paths, config_name),
        prepared_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        files=selected_files,
        source_roots=source_roots,
        repo_count=len(config.repos),
        project_count=included_project_count,
    )


def _build_prepared_manifest_v2(
    paths: AtlasPaths,
    config_name: str,
    *,
    progress: ProgressCallback | None,
) -> RankedPreparedManifest:
    config = _load_ranked_config_v2(paths)
    try:
        group = config.groups[config_name]
    except KeyError as exc:
        raise SystemExit(f"Unknown ranked context config: {config_name}") from exc

    resolved_repos = _resolve_group_repos_v2(paths, config, group)
    selected_files: list[RankedSelectedFile] = []
    seen_files: set[Path] = set()
    source_roots: list[Path] = []
    repo_manifest_paths: list[str] = []
    project_count = 0

    for index, resolved in enumerate(resolved_repos, start=1):
        manifest, cache_hit = _prepare_repo_variant_manifest(
            paths,
            resolved,
            progress=progress,
        )
        repo_manifest_paths.append(str(manifest.manifest_path))
        if resolved.repo_root not in source_roots:
            source_roots.append(resolved.repo_root)
        project_count += manifest.project_count
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
            )

    manifest_path = _prepared_manifest_path(paths, config_name)
    return RankedPreparedManifest(
        config_name=config_name,
        manifest_path=manifest_path,
        config_hash=_ranked_config_hash(paths, config_name),
        prepared_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        files=selected_files,
        source_roots=source_roots,
        repo_count=len(resolved_repos),
        project_count=project_count,
        repo_manifest_paths=repo_manifest_paths,
    )


def _load_ranked_config_v2(paths: AtlasPaths) -> RankedConfigV2:
    config_path = paths.ranked_contexts_path
    payload = load_ranked_contexts_payload(paths)
    _validate_keys("ranked context config", payload, {"version", "defaults", "repos", "groups"})
    if int(payload.get("version", 0)) != 2:
        raise SystemExit("ranked context config version must be 2")

    defaults_payload = _as_dict(payload.get("defaults", {}), "defaults")
    _validate_keys("defaults", defaults_payload, {"registry", "runtime", "strategies"})

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
    default_runtime = _parse_runtime(runtime_payload, config_path)

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
        repos[str(repo_key)] = _parse_repo_definition_v2(str(repo_key), item, strategies)

    groups_payload = _as_dict(payload.get("groups", {}), "groups")
    groups: dict[str, RankedGroupConfigV2] = {}
    for group_name, item in groups_payload.items():
        groups[str(group_name)] = _parse_group_v2(str(group_name), item)

    return RankedConfigV2(
        default_runtime=default_runtime,
        registry=registry_defaults,
        strategies=strategies,
        repos=repos,
        groups=groups,
    )


def _parse_repo_definition_v2(
    repo_key: str, item: object, strategies: dict[str, RankedPolicy]
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
            "projects",
            "variants",
        },
    )

    path = _optional_string(payload.get("path"))
    ref = _optional_string(payload.get("ref")) or (repo_key if path is None else None)
    if path is not None and ref is not None and payload.get("ref") is not None:
        raise SystemExit(f"repos.{repo_key} must not set both path and ref")

    base_policy = _parse_policy(
        payload,
        f"repos.{repo_key}",
        fallback=BUILTIN_STRATEGY_DEFAULTS["generic_default_v1"],
    )
    base_strategy = _parse_strategy_name(payload.get("strategy"), f"repos.{repo_key}.strategy")
    base_projects = _parse_project_overrides_v2(
        payload.get("projects", {}),
        f"repos.{repo_key}.projects",
        base_policy,
    )
    default_variant = RankedRepoVariantConfig(
        strategy=base_strategy,
        policy=base_policy,
        projects=base_projects,
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
                "projects",
            },
        )
        variant_policy = _parse_policy(
            variant_payload,
            f"repos.{repo_key}.variants.{variant_name}",
            fallback=base_policy,
        )
        variant_projects = dict(base_projects)
        variant_projects.update(
            _parse_project_overrides_v2(
                variant_payload.get("projects", {}),
                f"repos.{repo_key}.variants.{variant_name}.projects",
                variant_policy,
            )
        )
        variants[str(variant_name)] = RankedRepoVariantConfig(
            strategy=_parse_strategy_name(
                variant_payload.get("strategy"),
                f"repos.{repo_key}.variants.{variant_name}.strategy",
            )
            or base_strategy,
            policy=variant_policy,
            projects=variant_projects,
        )

    return RankedRepoDefinition(
        key=repo_key,
        path=path,
        ref=ref,
        label=_optional_string(payload.get("label")),
        default_variant=default_variant,
        variants=variants,
    )


def _parse_project_overrides_v2(
    payload: object, label: str, fallback_policy: RankedPolicy
) -> dict[str, RankedPolicy]:
    payload_dict = _as_dict(payload, label)
    projects: dict[str, RankedPolicy] = {}
    for rel_path, override in payload_dict.items():
        override_dict = _as_dict(override, f"{label}.{rel_path}")
        _validate_keys(
            f"{label}.{rel_path}",
            override_dict,
            {"exclude", "include_readme", "top_files", "top_percent", "overscan_limit"},
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


def _parse_group_v2(group_name: str, item: object) -> RankedGroupConfigV2:
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
                "variant",
            },
        )
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
                variant=_optional_string(row_dict.get("variant")) or "default",
            )
        )

    if not items and not selectors:
        raise SystemExit(f"groups.{group_name} must define items or selectors")
    return RankedGroupConfigV2(name=group_name, items=items, selectors=selectors)


def _parse_strategy_name(value: object, label: str) -> str | None:
    strategy = _optional_string(value)
    if strategy is None:
        return None
    if strategy not in BUILTIN_STRATEGY_DEFAULTS:
        raise SystemExit(f"{label} must be one of: {', '.join(sorted(BUILTIN_STRATEGY_DEFAULTS))}")
    return strategy


def _resolve_group_repos_v2(
    paths: AtlasPaths, config: RankedConfigV2, group: RankedGroupConfigV2
) -> list[ResolvedRepoVariant]:
    registry = _load_registry_or_scan(paths)
    records_by_path = {record.path: record for record in registry}

    resolved: list[ResolvedRepoVariant] = []
    seen: set[tuple[str, str]] = set()

    for item in group.items:
        resolved_variant = _resolve_repo_variant_v2(
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
            resolved_variant = _resolve_repo_variant_v2(
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
        filtered.append(record)
    return filtered


def _resolve_repo_variant_v2(
    *,
    paths: AtlasPaths,
    config: RankedConfigV2,
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
    repo_definition = _match_repo_definition_v2(config.repos, repo_record, ref, path)

    if repo_definition is None:
        repo_definition = RankedRepoDefinition(
            key=repo_record.slug or repo_root.name,
            path=None if ref is not None else str(repo_root),
            ref=repo_record.name if ref is not None else None,
            label=None,
            default_variant=RankedRepoVariantConfig(
                strategy=None,
                policy=BUILTIN_STRATEGY_DEFAULTS["generic_default_v1"],
                projects={},
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
        or _auto_strategy(repo_record)
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
    )


def _match_repo_definition_v2(
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


def _auto_strategy(repo_record: ProjectRecord) -> str:
    if repo_record.capabilities.get("elixir_ranked_v1"):
        return "elixir_ranked_v1"
    if repo_record.capabilities.get("python_default_v1"):
        return "python_default_v1"
    if repo_record.capabilities.get("rust_default_v1"):
        return "rust_default_v1"
    if repo_record.capabilities.get("node_default_v1"):
        return "node_default_v1"
    return "generic_default_v1"


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
            return _load_repo_prepared_manifest(payload), True

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


def _build_elixir_repo_manifest(
    resolved: ResolvedRepoVariant,
    manifest_path: Path,
    variant_hash: str,
    *,
    progress: ProgressCallback | None,
) -> RepoPreparedManifest:
    selected_files: list[RankedSelectedFile] = []
    seen_files: set[Path] = set()
    project_count = 0

    repo_readme = resolved.repo_root / "README.md"
    if resolved.policy.include_readme and repo_readme.is_file():
        _append_selected_file(
            selected_files,
            seen_files,
            repo_readme,
            f"{resolved.repo_label}/README.md",
            resolved.repo_label,
            ".",
        )

    projects = _discover_rankable_projects(resolved.repo_root)
    _validate_project_overrides_v2(resolved.projects, projects, resolved.repo_root)

    for project_index, project in enumerate(projects, start=1):
        prefix = (
            f"  [project {project_index}/{len(projects)}] {project.rel_path} "
            f"repo={resolved.repo_label} variant={resolved.variant_name}"
        )
        project_policy = resolved.projects.get(project.rel_path, resolved.policy)
        if project_policy.exclude:
            _emit_progress(progress, f"{prefix} step=skip reason=excluded")
            continue

        project_count += 1
        project_readme = project.abs_path / "README.md"
        if project_policy.include_readme and project_readme.is_file():
            rel_readme = project_readme.relative_to(resolved.repo_root).as_posix()
            _append_selected_file(
                selected_files,
                seen_files,
                project_readme,
                f"{resolved.repo_label}/{rel_readme}",
                resolved.repo_label,
                project.rel_path,
            )

        limit = _project_limit(project.abs_path, project_policy)
        if limit <= 0:
            _emit_progress(progress, f"{prefix} step=skip reason=empty-lib")
            continue

        ranked_rel_paths, fallback_used = _query_ranked_files(
            project.abs_path,
            resolved.runtime,
            limit,
            project_policy.overscan_limit,
            progress=progress,
            progress_prefix=prefix,
        )
        for rel_path in ranked_rel_paths:
            file_path = project.abs_path / rel_path
            rel_to_repo = file_path.relative_to(resolved.repo_root).as_posix()
            _append_selected_file(
                selected_files,
                seen_files,
                file_path,
                f"{resolved.repo_label}/{rel_to_repo}",
                resolved.repo_label,
                project.rel_path,
            )
        _emit_progress(
            progress,
            f"{prefix} step=selected count={len(ranked_rel_paths)}"
            + (" fallback=true" if fallback_used else ""),
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
    )


def _build_source_repo_manifest(
    resolved: ResolvedRepoVariant,
    manifest_path: Path,
    variant_hash: str,
) -> RepoPreparedManifest:
    selected_files: list[RankedSelectedFile] = []
    seen_files: set[Path] = set()

    repo_readme = resolved.repo_root / "README.md"
    if resolved.policy.include_readme and repo_readme.is_file():
        _append_selected_file(
            selected_files,
            seen_files,
            repo_readme,
            f"{resolved.repo_label}/README.md",
            resolved.repo_label,
            ".",
        )

    for file_path in _ranked_source_files_for_strategy(resolved):
        rel_to_repo = file_path.relative_to(resolved.repo_root).as_posix()
        _append_selected_file(
            selected_files,
            seen_files,
            file_path,
            f"{resolved.repo_label}/{rel_to_repo}",
            resolved.repo_label,
            ".",
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
        project_count=1,
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


def _validate_project_overrides_v2(
    project_overrides: dict[str, RankedPolicy], projects: list[Project], repo_root: Path
) -> None:
    known = {project.rel_path for project in projects}
    unknown = sorted(set(project_overrides) - known)
    if unknown:
        raise SystemExit(f"Unknown mix project override(s) for {repo_root}: {', '.join(unknown)}")


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
        "files": [
            {
                "path": str(item.abs_path),
                "output_path": f"./{item.output_rel}",
                "repo_label": item.repo_label,
                "project_rel_path": item.project_rel_path,
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


def _parse_repo(
    item: object, config_name: str, index: int, fallback_policy: RankedPolicy
) -> RankedRepoConfig:
    payload = _as_dict(item, f"configs.{config_name}.repos[{index}]")
    _validate_keys(
        f"configs.{config_name}.repos[{index}]",
        payload,
        {
            "path",
            "ref",
            "label",
            "include_readme",
            "top_files",
            "top_percent",
            "overscan_limit",
            "projects",
        },
    )

    path = _optional_string(payload.get("path"))
    ref = _optional_string(payload.get("ref"))
    if bool(path) == bool(ref):
        raise SystemExit(
            f"configs.{config_name}.repos[{index}] must set exactly one of path or ref"
        )

    policy = _parse_policy(
        payload, f"configs.{config_name}.repos[{index}]", fallback=fallback_policy
    )
    project_payload = payload.get("projects", {})
    if not isinstance(project_payload, dict):
        raise SystemExit(f"configs.{config_name}.repos[{index}].projects must be an object")

    projects: dict[str, RankedPolicy] = {}
    for rel_path, override in project_payload.items():
        override_dict = _as_dict(
            override, f"configs.{config_name}.repos[{index}].projects.{rel_path}"
        )
        _validate_keys(
            f"configs.{config_name}.repos[{index}].projects.{rel_path}",
            override_dict,
            {"exclude", "include_readme", "top_files", "top_percent", "overscan_limit"},
        )
        if "include" in override_dict:
            raise SystemExit(
                f"configs.{config_name}.repos[{index}].projects.{rel_path}.include is not supported"
            )

        projects[str(rel_path)] = _parse_policy(
            override_dict,
            f"configs.{config_name}.repos[{index}].projects.{rel_path}",
            fallback=policy,
            allow_exclude=True,
        )

    return RankedRepoConfig(
        path=path,
        ref=ref,
        label=_optional_string(payload.get("label")),
        policy=policy,
        projects=projects,
    )


def _parse_runtime(
    payload: dict[str, Any], config_path: Path, fallback: RankedRuntime | None = None
) -> RankedRuntime:
    if fallback is None:
        dexterity_root = _optional_string(payload.get("dexterity_root"))
        if dexterity_root is None:
            raise SystemExit(f"{config_path} defaults.dexterity_root is required")
        return RankedRuntime(
            dexterity_root=Path(dexterity_root).expanduser().resolve(),
            dexter_bin=_optional_string(payload.get("dexter_bin")) or "dexter",
        )

    dexterity_root = _optional_string(payload.get("dexterity_root"))
    return RankedRuntime(
        dexterity_root=Path(dexterity_root).expanduser().resolve()
        if dexterity_root
        else fallback.dexterity_root,
        dexter_bin=_optional_string(payload.get("dexter_bin")) or fallback.dexter_bin,
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
    exclude = (
        _parse_bool(payload.get("exclude", False), f"{label}.exclude") if allow_exclude else False
    )

    return RankedPolicy(
        include_readme=include_readme,
        top_files=resolved_top_files,
        top_percent=resolved_top_percent,
        overscan_limit=overscan_limit,
        exclude=exclude,
    )


def _resolve_repo_root(paths: AtlasPaths, repo: RankedRepoConfig) -> Path:
    if repo.path is not None:
        repo_root = Path(repo.path).expanduser().resolve()
    else:
        repo_root = Path(resolve_project_ref(paths, repo.ref or "").path)

    if not repo_root.exists() or not repo_root.is_dir():
        raise SystemExit(f"Repo root does not exist: {repo_root}")
    return repo_root


def _discover_rankable_projects(repo_root: Path) -> list[Project]:
    return [
        project
        for project in discover_projects(repo_root)
        if (project.abs_path / "mix.exs").is_file()
    ]


def _validate_project_overrides(
    repo: RankedRepoConfig, projects: list[Project], repo_root: Path
) -> None:
    known = {project.rel_path for project in projects}
    unknown = sorted(set(repo.projects) - known)
    if unknown:
        raise SystemExit(f"Unknown mix project override(s) for {repo_root}: {', '.join(unknown)}")


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
) -> tuple[list[str], bool]:
    runtime.dexterity_root.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()

    _emit_progress(progress, f"{progress_prefix} step=index")
    index_cmd = [
        "mix",
        "dexterity.index",
        "--repo-root",
        str(project_root),
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
        str(project_root),
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
        )
    )


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
    version = int(payload.get("version", 0))
    if version == 2:
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

    configs = payload.get("configs")
    if not isinstance(configs, dict) or config_name not in configs:
        raise SystemExit(f"Unknown ranked context config: {config_name}")
    relevant = {
        "version": payload.get("version"),
        "defaults": payload.get("defaults"),
        "config": configs[config_name],
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
        f"Run `atlas context ranked prepare {config_name}`."
    )


def _stale_prepared_manifest_message(config_name: str) -> str:
    return (
        f"Prepared ranked context stale for {config_name}: ranked config changed. "
        f"Run `atlas context ranked prepare {config_name}`."
    )
