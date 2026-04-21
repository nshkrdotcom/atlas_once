from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import AtlasPaths, AtlasSettings, ensure_state, load_settings, save_settings

PROJECT_MARKERS = (
    ".git",
    "pyproject.toml",
    "mix.exs",
    "package.json",
    "Cargo.toml",
    "go.mod",
    ".jj",
)

INVENTORY_SKIP_DIRS = {
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

LANGUAGE_EXTENSIONS = {
    ".ex": "elixir",
    ".exs": "elixir",
    ".py": "python",
    ".rs": "rust",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
}

REMOTE_PATTERNS = (
    re.compile(
        r"^(?P<alias>[A-Za-z0-9._-]+):(?P<owner>[A-Za-z0-9._-]+)/(?P<repo>[A-Za-z0-9._-]+?)(?:\.git)?$"
    ),
    re.compile(
        r"^https://(?P<host>[^/]+)/(?P<owner>[A-Za-z0-9._-]+)/(?P<repo>[A-Za-z0-9._-]+?)(?:\.git)?$"
    ),
    re.compile(
        r"^git@(?P<host>[^:]+):(?P<owner>[A-Za-z0-9._-]+)/(?P<repo>[A-Za-z0-9._-]+?)(?:\.git)?$"
    ),
    re.compile(
        r"^ssh://git@(?P<host>[^/]+)/(?P<owner>[A-Za-z0-9._-]+)/(?P<repo>[A-Za-z0-9._-]+?)(?:\.git)?$"
    ),
)

ALIAS_HOSTS = {"n": "github.com"}
GIT_RUN = subprocess.run


@dataclass(frozen=True)
class ProjectRecord:
    name: str
    slug: str
    path: str
    root: str
    aliases: list[str]
    manual_aliases: list[str]
    markers: list[str]
    last_scanned: str
    repo_id: str = ""
    languages: list[str] = field(default_factory=list)
    primary_language: str = ""
    owner_scope: str = "unknown"
    relation: str = "unknown"
    classification_source: str = "unknown"
    vcs: dict[str, Any] = field(default_factory=dict)
    layout: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, bool] = field(default_factory=dict)


@dataclass(frozen=True)
class RegistryScanResult:
    projects: list[ProjectRecord]
    scanned_roots: list[str]
    reused_roots: list[str]


def manual_project(reference: str) -> ProjectRecord:
    path = Path(reference).expanduser()
    resolved_name = path.name or reference
    resolved = path.resolve() if path.exists() else None
    return ProjectRecord(
        name=resolved_name,
        slug=slug_for_name(resolved_name),
        path=str(resolved) if resolved is not None else "",
        root=str(resolved.parent) if resolved is not None else "",
        aliases=generate_aliases(resolved_name),
        manual_aliases=[],
        markers=detect_project_markers(resolved) if resolved is not None else [],
        last_scanned="manual",
        repo_id=f"local:{resolved_name}",
        languages=[],
        primary_language="",
        owner_scope="unknown",
        relation="unknown",
        classification_source="unknown",
        vcs={},
        layout={"mix_projects": []},
        capabilities={"generic_default_v1": True},
    )


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        normalized = value.strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        output.append(normalized)
    return output


def _split_words(name: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name.replace("-", " ").replace("_", " "))
    return [part.lower() for part in spaced.split() if part.strip()]


def generate_aliases(name: str) -> list[str]:
    words = _split_words(name)
    aliases = [
        name.lower(),
        "-".join(words),
        "_".join(words),
        "".join(words),
    ]
    if len(words) > 1:
        aliases.append("".join(word[0] for word in words))
    return _dedupe(aliases)


def slug_for_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def detect_project_markers(path: Path) -> list[str]:
    markers: list[str] = []
    for marker in PROJECT_MARKERS:
        if (path / marker).exists():
            markers.append(marker)
    return markers


def load_registry(paths: AtlasPaths) -> list[ProjectRecord]:
    ensure_state(paths)
    repo_registry_path = paths.registry_root / "repos.json"
    if repo_registry_path.is_file():
        payload = json.loads(repo_registry_path.read_text(encoding="utf-8"))
    elif paths.registry_path.is_file():
        payload = json.loads(paths.registry_path.read_text(encoding="utf-8"))
    else:
        return []
    return [ProjectRecord(**item) for item in payload]


def load_registry_meta(paths: AtlasPaths) -> dict[str, int]:
    if not paths.registry_meta_path.is_file():
        return {}
    payload = json.loads(paths.registry_meta_path.read_text(encoding="utf-8"))
    return {str(key): int(value) for key, value in payload.items()}


def save_registry_meta(paths: AtlasPaths, payload: dict[str, int]) -> None:
    paths.registry_meta_path.parent.mkdir(parents=True, exist_ok=True)
    paths.registry_meta_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def save_registry(paths: AtlasPaths, projects: list[ProjectRecord]) -> None:
    ensure_state(paths)
    payload = [asdict(project) for project in sorted(projects, key=lambda item: item.name.lower())]
    paths.registry_path.parent.mkdir(parents=True, exist_ok=True)
    paths.registry_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (paths.registry_root / "repos.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def root_signature(root: Path) -> int:
    return root.stat().st_mtime_ns


def scan_registry(
    paths: AtlasPaths,
    settings: AtlasSettings | None = None,
    changed_only: bool = False,
) -> list[ProjectRecord]:
    return scan_registry_with_stats(paths, settings=settings, changed_only=changed_only).projects


def scan_registry_with_stats(
    paths: AtlasPaths,
    settings: AtlasSettings | None = None,
    changed_only: bool = False,
) -> RegistryScanResult:
    ensure_state(paths)
    settings = settings or load_settings(paths)
    existing = {record.path: record for record in load_registry(paths)}
    existing_by_root: dict[str, list[ProjectRecord]] = {}
    for record in existing.values():
        existing_by_root.setdefault(record.root, []).append(record)
    projects: list[ProjectRecord] = []
    scanned_at = datetime.now().astimezone().isoformat()
    meta = load_registry_meta(paths)
    next_meta: dict[str, int] = {}
    scanned_roots: list[str] = []
    reused_roots: list[str] = []

    for root_text in settings.project_roots:
        root = Path(root_text).expanduser().resolve()
        if not root.is_dir():
            continue
        signature = root_signature(root)
        next_meta[str(root)] = signature

        if changed_only and meta.get(str(root)) == signature:
            reused_roots.append(str(root))
            projects.extend(existing_by_root.get(str(root), []))
            continue

        scanned_roots.append(str(root))
        for child in sorted(root.iterdir(), key=lambda item: item.name.lower()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            markers = detect_project_markers(child)
            if not markers:
                continue
            previous = existing.get(str(child.resolve()))
            projects.append(_scan_repo(child, root, scanned_at, markers, previous, settings))

    save_registry(paths, projects)
    save_registry_meta(paths, next_meta)
    return RegistryScanResult(
        projects=projects,
        scanned_roots=scanned_roots,
        reused_roots=reused_roots,
    )


def _scan_repo(
    repo_root: Path,
    discovered_root: Path,
    scanned_at: str,
    markers: list[str],
    previous: ProjectRecord | None,
    settings: AtlasSettings,
) -> ProjectRecord:
    manual_aliases = previous.manual_aliases if previous is not None else []
    aliases = _dedupe(generate_aliases(repo_root.name) + manual_aliases)
    vcs = _load_vcs(repo_root)
    mix_projects = _discover_mix_projects(repo_root)
    languages, primary_language = _detect_languages(repo_root, markers)
    owner_scope, relation, classification_source = _classify_repo(vcs, settings.self_owners)
    repo_id = _repo_id_for_repo(repo_root, vcs)

    return ProjectRecord(
        name=repo_root.name,
        slug=slug_for_name(repo_root.name),
        path=str(repo_root.resolve()),
        root=str(discovered_root),
        aliases=aliases,
        manual_aliases=_dedupe(manual_aliases),
        markers=markers,
        last_scanned=scanned_at,
        repo_id=repo_id,
        languages=languages,
        primary_language=primary_language,
        owner_scope=owner_scope,
        relation=relation,
        classification_source=classification_source,
        vcs=vcs,
        layout={"mix_projects": mix_projects},
        capabilities=_capabilities(markers, languages, mix_projects),
    )


def _load_vcs(repo_root: Path) -> dict[str, Any]:
    result = GIT_RUN(
        ["git", "-C", str(repo_root), "remote", "-v"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return {}

    remotes: dict[str, dict[str, str]] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[2] != "(fetch)":
            continue
        remote_name = parts[0]
        parsed = _parse_remote(parts[1])
        if parsed is None:
            remotes[remote_name] = {"name": remote_name, "url": parts[1]}
        else:
            parsed["name"] = remote_name
            parsed["url"] = parts[1]
            remotes[remote_name] = parsed
    return remotes


def _parse_remote(url: str) -> dict[str, str] | None:
    normalized = url.strip()
    for pattern in REMOTE_PATTERNS:
        match = pattern.match(normalized)
        if match is None:
            continue
        data = match.groupdict()
        host = data.get("host")
        alias = data.get("alias")
        if host is None and alias is not None:
            host = ALIAS_HOSTS.get(alias, alias)
        owner = data.get("owner")
        repo = data.get("repo")
        if owner and repo and host:
            return {"host": host, "owner": owner, "repo": repo}
    return None


def _discover_mix_projects(repo_root: Path) -> list[dict[str, str]]:
    projects: list[dict[str, str]] = []
    for current_root, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [
            name
            for name in sorted(dirnames)
            if name not in INVENTORY_SKIP_DIRS and not name.startswith(".")
        ]
        if "mix.exs" not in filenames:
            continue
        current = Path(current_root)
        rel_path = current.relative_to(repo_root).as_posix()
        rel_path = "." if rel_path == "." else rel_path
        projects.append({"rel_path": rel_path, "role": _mix_project_role(rel_path)})
    return sorted(projects, key=lambda item: (item["rel_path"] != ".", item["rel_path"]))


def _mix_project_role(rel_path: str) -> str:
    if rel_path == ".":
        return "root"
    if rel_path.startswith("apps/"):
        return "app"
    if rel_path.startswith("core/"):
        return "core"
    if rel_path.startswith("bridges/"):
        return "bridge"
    if rel_path.startswith("connectors/"):
        return "connector"
    if rel_path.startswith("examples/") or rel_path.startswith("docs/"):
        return "example"
    if "fixture" in rel_path.split("/"):
        return "fixture"
    if rel_path.startswith("archive/") or "/archive/" in rel_path:
        return "archive"
    return rel_path.split("/", 1)[0]


def _detect_languages(repo_root: Path, markers: list[str]) -> tuple[list[str], str]:
    counts: dict[str, int] = {}
    for _current_root, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [
            name
            for name in sorted(dirnames)
            if name not in INVENTORY_SKIP_DIRS and not name.startswith(".")
        ]
        for filename in filenames:
            suffix = Path(filename).suffix.lower()
            language = LANGUAGE_EXTENSIONS.get(suffix)
            if language is None:
                continue
            counts[language] = counts.get(language, 0) + 1

    marker_languages: list[str] = []
    if "mix.exs" in markers:
        marker_languages.append("elixir")
    if "pyproject.toml" in markers:
        marker_languages.append("python")
    if "Cargo.toml" in markers:
        marker_languages.append("rust")
    if "package.json" in markers:
        marker_languages.append("javascript")
    if "go.mod" in markers:
        marker_languages.append("go")

    ordered = sorted(
        {language for language in [*counts.keys(), *marker_languages]},
        key=lambda item: (-counts.get(item, 0), item),
    )
    if not ordered:
        return [], ""
    primary = ordered[0]
    return ordered, primary


def _classify_repo(vcs: dict[str, Any], self_owners: list[str]) -> tuple[str, str, str]:
    owners = {owner.lower() for owner in self_owners}
    origin = vcs.get("origin")
    upstream = vcs.get("upstream")
    origin_owner = str(origin.get("owner", "")).lower() if isinstance(origin, dict) else ""
    upstream_owner = str(upstream.get("owner", "")).lower() if isinstance(upstream, dict) else ""
    origin_repo = str(origin.get("repo", "")).lower() if isinstance(origin, dict) else ""
    upstream_repo = str(upstream.get("repo", "")).lower() if isinstance(upstream, dict) else ""

    if origin_owner or upstream_owner:
        owner_scope = "self" if origin_owner in owners and origin_owner else "external"
        if (
            origin_owner
            and upstream_owner
            and origin_repo
            and origin_repo == upstream_repo
            and origin_owner != upstream_owner
        ):
            relation = "fork"
        elif owner_scope == "self":
            relation = "primary"
        else:
            relation = "external"
        return owner_scope, relation, "local_remote_heuristic"

    return "unknown", "unknown", "unknown"


def _repo_id_for_repo(repo_root: Path, vcs: dict[str, Any]) -> str:
    origin = vcs.get("origin")
    if isinstance(origin, dict):
        host = str(origin.get("host", "")).strip()
        owner = str(origin.get("owner", "")).strip()
        repo = str(origin.get("repo", "")).strip()
        if host and owner and repo:
            return f"{host}/{owner}/{repo}"
    return f"local:{repo_root.name}"


def _capabilities(
    markers: list[str], languages: list[str], mix_projects: list[dict[str, str]]
) -> dict[str, bool]:
    language_set = set(languages)
    return {
        "elixir_ranked_v1": "elixir" in language_set or "mix.exs" in markers or bool(mix_projects),
        "python_default_v1": "python" in language_set or "pyproject.toml" in markers,
        "rust_default_v1": "rust" in language_set or "Cargo.toml" in markers,
        "node_default_v1": bool({"javascript", "typescript"} & language_set)
        or "package.json" in markers,
        "generic_default_v1": True,
    }


def resolve_project_ref(
    paths: AtlasPaths, reference: str, auto_scan: bool = False
) -> ProjectRecord:
    candidate_path = Path(reference).expanduser()
    if _reference_looks_like_path(reference) and candidate_path.exists():
        resolved = candidate_path.resolve()
        return manual_project(str(resolved))

    registry = load_registry(paths)
    if not registry and auto_scan:
        registry = scan_registry(paths)

    ref = reference.lower().strip()
    exact_matches = [
        record
        for record in registry
        if ref
        in {
            record.name.lower(),
            record.slug.lower(),
            Path(record.path).name.lower(),
            *record.aliases,
        }
    ]
    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(exact_matches) > 1:
        names = ", ".join(record.name for record in exact_matches)
        raise SystemExit(f"Ambiguous project reference '{reference}': {names}")

    prefix_matches = [
        record
        for record in registry
        if record.name.lower().startswith(ref)
        or record.slug.lower().startswith(ref)
        or any(alias.startswith(ref) for alias in record.aliases)
    ]
    unique = {record.path: record for record in prefix_matches}
    if len(unique) == 1:
        return next(iter(unique.values()))
    if len(unique) > 1:
        names = ", ".join(record.name for record in unique.values())
        raise SystemExit(f"Ambiguous project reference '{reference}': {names}")

    if candidate_path.exists():
        raise SystemExit(
            f"Unknown project reference: {reference}. "
            f"Use ./{reference} to resolve the cwd-relative path."
        )

    raise SystemExit(f"Unknown project reference: {reference}")


def _reference_looks_like_path(reference: str) -> bool:
    return (
        Path(reference).is_absolute()
        or reference.startswith(("~", ".", os.sep))
        or "/" in reference
        or "\\" in reference
    )


def resolve_or_placeholder(paths: AtlasPaths, reference: str) -> ProjectRecord:
    try:
        return resolve_project_ref(paths, reference)
    except SystemExit:
        return manual_project(reference)


def add_root(paths: AtlasPaths, root_text: str) -> AtlasSettings:
    settings = ensure_state(paths)
    root = str(Path(root_text).expanduser().resolve())
    roots = [*settings.project_roots]
    if root not in roots:
        roots.append(root)
    updated = AtlasSettings(
        data_home=settings.data_home,
        code_root=settings.code_root,
        project_roots=sorted(roots),
        self_owners=settings.self_owners,
        auto_sync_relationships=settings.auto_sync_relationships,
        review_window_days=settings.review_window_days,
    )
    save_settings(paths, updated)
    return updated


def remove_root(paths: AtlasPaths, root_text: str) -> AtlasSettings:
    settings = ensure_state(paths)
    root = str(Path(root_text).expanduser().resolve())
    updated = AtlasSettings(
        data_home=settings.data_home,
        code_root=settings.code_root,
        project_roots=[item for item in settings.project_roots if item != root],
        self_owners=settings.self_owners,
        auto_sync_relationships=settings.auto_sync_relationships,
        review_window_days=settings.review_window_days,
    )
    save_settings(paths, updated)
    return updated


def add_alias(paths: AtlasPaths, reference: str, alias: str) -> ProjectRecord:
    registry = scan_registry(paths)
    target = resolve_project_ref(paths, reference, auto_scan=False)
    updated: list[ProjectRecord] = []
    result = target
    for record in registry:
        if record.path == target.path:
            manual_aliases = _dedupe(record.manual_aliases + [alias])
            result = ProjectRecord(
                name=record.name,
                slug=record.slug,
                path=record.path,
                root=record.root,
                aliases=_dedupe(generate_aliases(record.name) + manual_aliases),
                manual_aliases=manual_aliases,
                markers=record.markers,
                last_scanned=record.last_scanned,
                repo_id=record.repo_id,
                languages=record.languages,
                primary_language=record.primary_language,
                owner_scope=record.owner_scope,
                relation=record.relation,
                classification_source=record.classification_source,
                vcs=record.vcs,
                layout=record.layout,
                capabilities=record.capabilities,
            )
            updated.append(result)
        else:
            updated.append(record)
    save_registry(paths, updated)
    return result


def remove_alias(paths: AtlasPaths, reference: str, alias: str) -> ProjectRecord:
    registry = scan_registry(paths)
    target = resolve_project_ref(paths, reference, auto_scan=False)
    updated: list[ProjectRecord] = []
    result = target
    for record in registry:
        if record.path == target.path:
            manual_aliases = [
                item for item in record.manual_aliases if item.lower() != alias.lower()
            ]
            result = ProjectRecord(
                name=record.name,
                slug=record.slug,
                path=record.path,
                root=record.root,
                aliases=_dedupe(generate_aliases(record.name) + manual_aliases),
                manual_aliases=manual_aliases,
                markers=record.markers,
                last_scanned=record.last_scanned,
                repo_id=record.repo_id,
                languages=record.languages,
                primary_language=record.primary_language,
                owner_scope=record.owner_scope,
                relation=record.relation,
                classification_source=record.classification_source,
                vcs=record.vcs,
                layout=record.layout,
                capabilities=record.capabilities,
            )
            updated.append(result)
        else:
            updated.append(record)
    save_registry(paths, updated)
    return result
