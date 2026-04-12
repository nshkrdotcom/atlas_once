from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

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


@dataclass(frozen=True)
class RegistryScanResult:
    projects: list[ProjectRecord]
    scanned_roots: list[str]
    reused_roots: list[str]


def manual_project(reference: str) -> ProjectRecord:
    path = Path(reference).expanduser()
    resolved_name = path.name or reference
    return ProjectRecord(
        name=resolved_name,
        slug=slug_for_name(resolved_name),
        path=str(path.resolve()) if path.exists() else "",
        root=str(path.resolve().parent) if path.exists() else "",
        aliases=generate_aliases(resolved_name),
        manual_aliases=[],
        markers=[],
        last_scanned="manual",
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
    if not paths.registry_path.is_file():
        return []
    payload = json.loads(paths.registry_path.read_text(encoding="utf-8"))
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
    paths.registry_path.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(project) for project in sorted(projects, key=lambda item: item.name.lower())]
    paths.registry_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
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
            manual_aliases = previous.manual_aliases if previous is not None else []
            aliases = _dedupe(generate_aliases(child.name) + manual_aliases)
            projects.append(
                ProjectRecord(
                    name=child.name,
                    slug=slug_for_name(child.name),
                    path=str(child.resolve()),
                    root=str(root),
                    aliases=aliases,
                    manual_aliases=_dedupe(manual_aliases),
                    markers=markers,
                    last_scanned=scanned_at,
                )
            )

    save_registry(paths, projects)
    save_registry_meta(paths, next_meta)
    return RegistryScanResult(
        projects=projects, scanned_roots=scanned_roots, reused_roots=reused_roots
    )


def resolve_project_ref(
    paths: AtlasPaths, reference: str, auto_scan: bool = False
) -> ProjectRecord:
    candidate_path = Path(reference).expanduser()
    if candidate_path.exists():
        resolved = candidate_path.resolve()
        return ProjectRecord(
            name=resolved.name,
            slug=slug_for_name(resolved.name),
            path=str(resolved),
            root=str(resolved.parent),
            aliases=generate_aliases(resolved.name),
            manual_aliases=[],
            markers=detect_project_markers(resolved),
            last_scanned="manual",
        )

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

    raise SystemExit(f"Unknown project reference: {reference}")


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
        project_roots=sorted(roots),
        auto_sync_relationships=settings.auto_sync_relationships,
        review_window_days=settings.review_window_days,
    )
    save_settings(paths, updated)
    return updated


def remove_root(paths: AtlasPaths, root_text: str) -> AtlasSettings:
    settings = ensure_state(paths)
    root = str(Path(root_text).expanduser().resolve())
    updated = AtlasSettings(
        project_roots=[item for item in settings.project_roots if item != root],
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
            )
            updated.append(result)
        else:
            updated.append(record)
    save_registry(paths, updated)
    return result
