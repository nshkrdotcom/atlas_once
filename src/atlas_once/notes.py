from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .config import AtlasPaths, ensure_state
from .registry import ProjectRecord
from .templates import daily_note_template, session_template
from .util import atomic_json_write, parse_metadata, read_text, slugify

BACKLINKS_START = "<!-- atlas:backlinks:start -->"
BACKLINKS_END = "<!-- atlas:backlinks:end -->"
RELATED_START = "<!-- atlas:related:start -->"
RELATED_END = "<!-- atlas:related:end -->"
WIKI_LINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
HEADER_LINE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
GENERATED_BLOCK = re.compile(
    rf"{re.escape(BACKLINKS_START)}.*?{re.escape(BACKLINKS_END)}|"
    rf"{re.escape(RELATED_START)}.*?{re.escape(RELATED_END)}",
    re.DOTALL,
)


@dataclass(frozen=True)
class NoteNode:
    path: str
    title: str
    project: str | None
    tags: list[str]
    explicit_refs: list[str]
    resolved_links: list[str]


def graph_roots(paths: AtlasPaths) -> list[Path]:
    return [
        paths.docs_root,
        paths.sessions_root,
        paths.projects_root,
        paths.decisions_root,
        paths.people_root,
        paths.topics_root,
    ]


def list_graph_notes(paths: AtlasPaths) -> list[Path]:
    ensure_state(paths)
    notes: list[Path] = []
    for root in graph_roots(paths):
        if not root.exists():
            continue
        notes.extend(
            path
            for path in root.rglob("*")
            if path.is_file()
            and path.suffix.lower() in {".md", ".markdown", ".mdown", ".mkd", ".mkdn", ".mdx"}
        )
    return sorted(notes)


def strip_generated_sections(content: str) -> str:
    stripped = GENERATED_BLOCK.sub("", content)
    return stripped.rstrip() + "\n"


def extract_title(content: str, path: Path) -> str:
    match = HEADER_LINE.search(content)
    if match:
        return match.group(1).strip()
    return path.stem.replace("-", " ").replace("_", " ")


def extract_refs(content: str) -> list[str]:
    refs: list[str] = []
    for match in WIKI_LINK.finditer(content):
        refs.append(match.group(1).strip())
    for match in MARKDOWN_LINK.finditer(content):
        target = match.group(1).strip()
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        refs.append(target.split("#", 1)[0].strip())
    return refs


def _candidate_maps(note_paths: list[Path]) -> tuple[dict[str, list[Path]], dict[str, Path]]:
    by_name: dict[str, list[Path]] = defaultdict(list)
    by_exact: dict[str, Path] = {}
    for path in note_paths:
        by_name[path.stem.lower()].append(path)
        by_name[slugify(path.stem)].append(path)
        by_exact[str(path.resolve())] = path
    return by_name, by_exact


def resolve_ref(
    origin: Path, raw_ref: str, by_name: dict[str, list[Path]], by_exact: dict[str, Path]
) -> Path | None:
    candidate = raw_ref.strip()
    if not candidate:
        return None

    direct = Path(candidate).expanduser()
    if direct.is_absolute():
        resolved = direct.resolve()
        return by_exact.get(str(resolved))

    if "/" in candidate or candidate.endswith(".md"):
        resolved = (origin.parent / candidate).resolve()
        if str(resolved) in by_exact:
            return by_exact[str(resolved)]

    key = candidate.removesuffix(".md").lower()
    possible = by_name.get(key, [])
    if len(possible) == 1:
        return possible[0]
    slug_key = slugify(key)
    possible = by_name.get(slug_key, [])
    if len(possible) == 1:
        return possible[0]
    return None


def note_relative_link(origin: Path, target: Path) -> str:
    return (
        Path(target).relative_to(origin.parent.resolve() / ".").as_posix()
        if False
        else Path(__import__("os").path.relpath(target, origin.parent)).as_posix()
    )


def replace_or_append_block(content: str, start: str, end: str, body: str) -> str:
    block = f"{start}\n{body.rstrip()}\n{end}"
    pattern = re.compile(rf"{re.escape(start)}.*?{re.escape(end)}", re.DOTALL)
    if pattern.search(content):
        updated = pattern.sub(block, content)
    else:
        updated = content.rstrip() + "\n\n" + block + "\n"
    return updated


def render_relationship_section(
    title: str,
    paths: list[Path],
    origin: Path,
    titles: dict[Path, str],
) -> str:
    lines = [f"## {title}", ""]
    if not paths:
        lines.append("- None yet")
    else:
        for target in paths:
            lines.append(f"- [{titles[target]}]({note_relative_link(origin, target)})")
    return "\n".join(lines)


def build_graph(
    paths: AtlasPaths,
) -> tuple[dict[Path, NoteNode], dict[Path, set[Path]], dict[Path, list[Path]]]:
    note_paths = list_graph_notes(paths)
    by_name, by_exact = _candidate_maps(note_paths)
    nodes: dict[Path, NoteNode] = {}
    backlinks: dict[Path, set[Path]] = {path: set() for path in note_paths}

    for path in note_paths:
        content = strip_generated_sections(read_text(path))
        meta = parse_metadata(content)
        resolved: list[Path] = []
        for ref in extract_refs(content):
            target = resolve_ref(path, ref, by_name, by_exact)
            if target is not None and target != path:
                resolved.append(target)
        unique_links = sorted({item.resolve() for item in resolved})
        nodes[path] = NoteNode(
            path=str(path),
            title=extract_title(content, path),
            project=meta.project,
            tags=meta.tags,
            explicit_refs=extract_refs(content),
            resolved_links=[str(item) for item in unique_links],
        )
        for target in unique_links:
            backlinks[target].add(path)

    related: dict[Path, list[Path]] = {}
    for path, node in nodes.items():
        scored: list[tuple[int, Path]] = []
        node_links = {Path(item) for item in node.resolved_links}
        for other_path, other_node in nodes.items():
            if other_path == path:
                continue
            score = 0
            if node.project and other_node.project == node.project:
                score += 3
            score += len(set(node.tags).intersection(other_node.tags))
            other_links = {Path(item) for item in other_node.resolved_links}
            if other_path in node_links or path in other_links:
                score += 5
            if path.parent == other_path.parent:
                score += 1
            if score > 0:
                scored.append((score, other_path))
        related[path] = [
            item for _, item in sorted(scored, key=lambda pair: (-pair[0], pair[1].as_posix()))[:5]
        ]

    return nodes, backlinks, related


def sync_note_graph(paths: AtlasPaths) -> int:
    ensure_state(paths)
    nodes, backlinks, related = build_graph(paths)
    titles = {path: node.title for path, node in nodes.items()}
    changed = 0

    for path in nodes:
        content = read_text(path)
        backlinks_block = render_relationship_section(
            "Backlinks",
            sorted(backlinks[path], key=lambda item: item.as_posix()),
            path,
            titles,
        )
        related_block = render_relationship_section(
            "Related",
            related[path],
            path,
            titles,
        )
        updated = replace_or_append_block(content, BACKLINKS_START, BACKLINKS_END, backlinks_block)
        updated = replace_or_append_block(updated, RELATED_START, RELATED_END, related_block)
        if updated != content:
            path.write_text(updated, encoding="utf-8")
            changed += 1

    atomic_json_write(
        paths.relationships_path,
        {
            "notes": [asdict(node) for node in nodes.values()],
            "backlinks": {
                str(path): [str(item) for item in sorted(items, key=lambda value: value.as_posix())]
                for path, items in backlinks.items()
            },
            "related": {
                str(path): [str(item) for item in related[path]]
                for path in sorted(related, key=lambda value: value.as_posix())
            },
        },
    )
    atomic_json_write(
        paths.project_index_path,
        {key: value for key, value in _project_index(paths, nodes).items()},
    )
    atomic_json_write(paths.tag_index_path, _tag_index(nodes))
    atomic_json_write(
        paths.link_index_path, {str(path): node.resolved_links for path, node in nodes.items()}
    )
    return changed


def _project_index(paths: AtlasPaths, nodes: dict[Path, NoteNode]) -> dict[str, dict[str, object]]:
    index: dict[str, dict[str, object]] = {}
    for path, node in nodes.items():
        if not node.project:
            continue
        bucket = index.setdefault(node.project, {"files": [], "repos": [], "aliases": []})
        files = bucket["files"]
        assert isinstance(files, list)
        files.append(str(path))
    return index


def _tag_index(nodes: dict[Path, NoteNode]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = defaultdict(list)
    for path, node in nodes.items():
        for tag in node.tags:
            index[tag].append(str(path))
    return dict(index)


def _metadata_header(
    title: str, now: datetime, project: str | None, tags: list[str], status: str
) -> str:
    tags_text = ", ".join(tags)
    return (
        f"# {title}\n"
        f"Date: {now:%Y-%m-%d}\n"
        f"Project: {project or ''}\n"
        f"Tags: {tags_text}\n"
        f"Status: {status}\n\n"
    )


def create_note(
    paths: AtlasPaths,
    title: str,
    kind: str = "note",
    project: ProjectRecord | None = None,
    tags: list[str] | None = None,
    body: str = "",
    date_stamp: str | None = None,
) -> Path:
    ensure_state(paths)
    now = datetime.now().astimezone()
    tags = tags or []
    project_name = project.name if project is not None else None
    project_slug = project.slug if project is not None else None
    stamp = date_stamp or now.strftime("%Y%m%d")
    stem = slugify(title)

    if kind == "decision":
        target = paths.decisions_root / f"{stamp}-{stem}.md"
    elif kind == "project":
        target = paths.projects_root / f"{project_slug or stem}.md"
    elif kind == "topic":
        target = paths.topics_root / f"{stem}.md"
    elif kind == "person":
        target = paths.people_root / f"{stem}.md"
    elif kind == "session":
        target = paths.sessions_root / f"{stamp}-{now:%H%M}-{stem}.md"
    else:
        day_dir = paths.docs_root / stamp
        if project_slug:
            day_dir = day_dir / project_slug
        target = day_dir / f"{stem}.md"

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise SystemExit(f"Note already exists: {target}")

    if kind == "session":
        content = session_template(project_name or "", title, now)
    elif kind == "daily":
        content = daily_note_template(stamp)
    else:
        content = _metadata_header(title, now, project_name, tags, "active")
        if body.strip():
            content += "## Notes\n\n" + body.strip() + "\n"
        else:
            content += "## Notes\n\n"

    target.write_text(content, encoding="utf-8")
    sync_note_graph(paths)
    return target


def ensure_entity_note(
    paths: AtlasPaths,
    kind: str,
    title: str,
    project: ProjectRecord | None = None,
    tags: list[str] | None = None,
) -> Path:
    ensure_state(paths)
    tags = tags or []
    slug = project.slug if kind == "project" and project is not None else slugify(title)
    if kind == "project":
        target = paths.projects_root / f"{slug}.md"
        canonical_title = project.name if project is not None else title
        repo_path = project.path if project is not None else ""
        aliases = ", ".join(project.aliases) if project is not None else ""
        body = (
            f"# {canonical_title}\n"
            f"Aliases: {aliases}\n"
            f"Repos: {repo_path}\n"
            f"Project: {canonical_title}\n"
            f"Tags: {', '.join(tags)}\n"
            "Status: active\n\n"
            "## Summary\n\n"
            "## Captured Notes\n\n"
        )
    elif kind == "topic":
        target = paths.topics_root / f"{slug}.md"
        body = (
            f"# {title}\n"
            f"Project: {project.name if project is not None else ''}\n"
            f"Tags: {', '.join(tags)}\n"
            "Status: active\n\n"
            "## Summary\n\n"
            "## Captured Notes\n\n"
        )
    else:
        target = paths.people_root / f"{slug}.md"
        body = (
            f"# {title}\n"
            f"Project: {project.name if project is not None else ''}\n"
            f"Tags: {', '.join(tags)}\n"
            "Status: active\n\n"
            "## Summary\n\n"
            "## Captured Notes\n\n"
        )

    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return target


def append_promotion_note(target: Path, heading: str, text: str) -> None:
    content = read_text(target)
    block = f"### {heading}\n\n{text.strip()}\n"
    if "## Captured Notes" in content:
        updated = content.rstrip() + "\n\n" + block
    else:
        updated = content.rstrip() + "\n\n## Captured Notes\n\n" + block
    target.write_text(updated, encoding="utf-8")
