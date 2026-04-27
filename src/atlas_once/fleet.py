from __future__ import annotations

import fnmatch
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import AtlasPaths, ensure_state
from .registry import ProjectRecord, load_registry
from .runtime import AtlasCliError, ExitCode


@dataclass(frozen=True)
class RepoModel:
    ref: str
    path: str
    groups: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    default_branch: str = ""
    remote_hint: str = "origin"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RepoSelection:
    selector: list[str]
    repos: list[RepoModel]
    errors: list[dict[str, Any]]


def repo_model_dict(repo: RepoModel) -> dict[str, Any]:
    return asdict(repo)


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list | tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _repo_from_project(record: ProjectRecord) -> RepoModel:
    groups = {
        record.primary_language,
        record.owner_scope,
        record.relation,
        *record.languages,
    }
    groups.update(key for key, enabled in record.capabilities.items() if enabled)
    if "atlas" in {record.name.lower(), record.slug.lower(), *record.aliases}:
        groups.add("atlas")
    origin = record.vcs.get("origin") if isinstance(record.vcs, dict) else None
    remote_hint = "origin" if isinstance(origin, dict) else ""
    return RepoModel(
        ref=record.slug or record.name,
        path=record.path,
        groups=sorted(item for item in groups if item and item != "unknown"),
        aliases=sorted({record.name, record.slug, Path(record.path).name, *record.aliases}),
        default_branch="main",
        remote_hint=remote_hint,
        raw=asdict(record),
    )


def _repo_from_mapping(item: dict[str, Any], index: int) -> RepoModel:
    ref = str(item.get("ref") or item.get("name") or item.get("slug") or "").strip()
    path = str(item.get("path") or item.get("repo_path") or item.get("root") or "").strip()
    if not ref:
        ref = Path(path).name if path else f"repo-{index + 1}"
    if not path:
        raise AtlasCliError(
            ExitCode.VALIDATION,
            "invalid_manifest_repo",
            f"Manifest repo {ref!r} is missing path.",
            {"repo": item},
        )
    return RepoModel(
        ref=ref,
        path=str(Path(path).expanduser().resolve()),
        groups=_string_list(item.get("groups")),
        aliases=_string_list(item.get("aliases")),
        default_branch=str(item.get("default_branch") or "main"),
        remote_hint=str(item.get("remote_hint") or "origin"),
        raw=dict(item),
    )


def load_fleet_config(paths: AtlasPaths) -> dict[str, Any]:
    ensure_state(paths)
    if not paths.fleet_config_path.is_file():
        return {}
    payload = json.loads(paths.fleet_config_path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def bootstrap_fleet_config(paths: AtlasPaths) -> bool:
    ensure_state(paths)
    if paths.fleet_config_path.exists():
        return False
    payload = {
        "manifest": {
            "default": str(paths.registry_path),
            "allow_override": True,
            "allowed_formats": ["json", "toml"],
        },
        "git_health": {
            "enabled": True,
            "background_enabled": True,
            "poll_interval_ms": 5000,
            "stale_after_ms": 30000,
            "signature_cache_ttl_ms": 60000,
            "max_workers": 6,
            "command_timeout_seconds": 10,
        },
        "selectors": {
            "default": "@all",
            "groups": {"dirty": ["@dirty"], "unpushed": ["@unpushed"]},
        },
    }
    paths.fleet_config_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return True


def git_health_config(paths: AtlasPaths) -> dict[str, Any]:
    defaults = {
        "enabled": True,
        "background_enabled": True,
        "poll_interval_ms": 5000,
        "stale_after_ms": 30000,
        "signature_cache_ttl_ms": 60000,
        "max_workers": 6,
        "command_timeout_seconds": 10,
    }
    configured = load_fleet_config(paths).get("git_health", {})
    if isinstance(configured, dict):
        defaults.update(configured)
    return defaults


def load_repos(
    paths: AtlasPaths,
    manifest: str | None = None,
    manifest_format: str = "json",
) -> list[RepoModel]:
    ensure_state(paths)
    if manifest:
        return _load_manifest(Path(manifest).expanduser().resolve(), manifest_format)
    return [_repo_from_project(record) for record in load_registry(paths)]


def _load_manifest(path: Path, manifest_format: str) -> list[RepoModel]:
    if not path.is_file():
        raise AtlasCliError(
            ExitCode.NOT_FOUND,
            "manifest_not_found",
            f"Manifest not found: {path}",
            {"manifest": str(path)},
        )
    if manifest_format != "json":
        raise AtlasCliError(
            ExitCode.VALIDATION,
            "unsupported_manifest_format",
            "Only JSON manifests are supported in this build.",
            {"manifest_format": manifest_format},
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        raw_items = payload.get("repos", payload.get("projects", []))
        items = raw_items if isinstance(raw_items, list) else []
    else:
        items = []
    return [_repo_from_mapping(dict(item), index) for index, item in enumerate(items)]


def canonical_repo_path(repo: RepoModel) -> str:
    path = Path(repo.path).expanduser()
    try:
        return str(path.resolve(strict=False))
    except OSError:
        return str(path.absolute())


def dedupe_repos(repos: list[RepoModel]) -> list[RepoModel]:
    by_path: dict[str, RepoModel] = {}
    for repo in repos:
        by_path.setdefault(canonical_repo_path(repo), repo)
    return sorted(by_path.values(), key=lambda item: (item.ref.lower(), canonical_repo_path(item)))


def select_repos(
    repos: list[RepoModel],
    selectors: list[str] | None,
    *,
    health_by_path: dict[str, dict[str, Any]] | None = None,
) -> RepoSelection:
    requested = selectors or ["@all"]
    includes = [item for item in requested if not item.startswith("!")]
    excludes = [item[1:] for item in requested if item.startswith("!")]
    if not includes:
        includes = ["@all"]
    errors: list[dict[str, Any]] = []
    selected: dict[str, RepoModel] = {}
    for selector in includes:
        for repo in _resolve_selector(repos, selector, health_by_path, errors):
            selected.setdefault(canonical_repo_path(repo), repo)
    for selector in excludes:
        for repo in _resolve_selector(repos, selector, health_by_path, errors):
            selected.pop(canonical_repo_path(repo), None)
    return RepoSelection(requested, dedupe_repos(list(selected.values())), errors)


def _resolve_selector(
    repos: list[RepoModel],
    selector: str,
    health_by_path: dict[str, dict[str, Any]] | None,
    errors: list[dict[str, Any]],
) -> list[RepoModel]:
    text = selector.strip()
    if not text or text == "@all":
        return list(repos)
    if text.startswith("@group:"):
        group = text.split(":", 1)[1].lower()
        return [repo for repo in repos if group in {item.lower() for item in repo.groups}]
    if text in {"@dirty", "@unpushed", "@stale"}:
        health_by_path = health_by_path or {}
        return [
            repo
            for repo in repos
            if _health_matches(health_by_path.get(canonical_repo_path(repo), {}), text)
        ]
    lowered = text.lower()
    exact = [repo for repo in repos if lowered in _repo_names(repo)]
    if exact:
        return exact
    if any(char in text for char in "*?["):
        matches = [
            repo
            for repo in repos
            if fnmatch.fnmatch(canonical_repo_path(repo), text)
            or fnmatch.fnmatch(Path(repo.path).name, text)
            or fnmatch.fnmatch(repo.ref, text)
        ]
        if matches:
            return matches
    candidate = Path(text).expanduser()
    if candidate.exists():
        resolved = str(candidate.resolve())
        matches = [repo for repo in repos if canonical_repo_path(repo) == resolved]
        if matches:
            return matches
    errors.append({"kind": "unresolved_selector", "selector": selector})
    return []


def _health_matches(status: dict[str, Any], selector: str) -> bool:
    if selector == "@dirty":
        return bool(
            status.get("working_dirty")
            or status.get("index_dirty")
            or status.get("untracked_count")
            or status.get("conflicted")
        )
    if selector == "@unpushed":
        return int(status.get("ahead") or 0) > 0
    if selector == "@stale":
        return bool(status.get("stale"))
    return False


def _repo_names(repo: RepoModel) -> set[str]:
    return {
        repo.ref.lower(),
        Path(repo.path).name.lower(),
        *[alias.lower() for alias in repo.aliases],
    }
