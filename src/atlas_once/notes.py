from __future__ import annotations

import os
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
NOTE_SUFFIXES = {".md", ".markdown", ".mdown", ".mkd", ".mkdn", ".mdx"}


@dataclass(frozen=True)
class NoteNode:
    path: str
    title: str
    project: str | None
    tags: list[str]
    explicit_refs: list[str]
    resolved_links: list[str]


@dataclass(frozen=True)
class NoteGraphSyncResult:
    changed_notes: int
    note_count: int
    parsed_notes: int
    mode: str


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
            if path.is_file() and path.suffix.lower() in NOTE_SUFFIXES
        )
    return sorted(notes)


def strip_generated_sections(content: str) -> str:
    return GENERATED_BLOCK.sub("", content).rstrip() + "\n"


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
    origin: Path,
    raw_ref: str,
    by_name: dict[str, list[Path]],
    by_exact: dict[str, Path],
) -> Path | None:
    candidate = raw_ref.strip()
    if not candidate:
        return None

    direct = Path(candidate).expanduser()
    if direct.is_absolute():
        return by_exact.get(str(direct.resolve()))

    if "/" in candidate or candidate.endswith(".md"):
        relative = (origin.parent / candidate).resolve()
        if str(relative) in by_exact:
            return by_exact[str(relative)]

    key = candidate.removesuffix(".md").lower()
    for lookup in (key, slugify(key)):
        matches = by_name.get(lookup, [])
        if len(matches) == 1:
            return matches[0]
    return None


def note_relative_link(origin: Path, target: Path) -> str:
    return Path(os.path.relpath(target, origin.parent)).as_posix()


def replace_or_append_block(content: str, start: str, end: str, body: str) -> str:
    block = f"{start}\n{body.rstrip()}\n{end}"
    pattern = re.compile(rf"{re.escape(start)}.*?{re.escape(end)}", re.DOTALL)
    if pattern.search(content):
        return pattern.sub(block, content)
    return content.rstrip() + "\n\n" + block + "\n"


def render_relationship_section(
    title: str,
    targets: list[Path],
    origin: Path,
    titles: dict[Path, str],
) -> str:
    lines = [f"## {title}", ""]
    if not targets:
        lines.append("- None yet")
    else:
        for target in targets:
            lines.append(f"- [{titles[target]}]({note_relative_link(origin, target)})")
    return "\n".join(lines)


def parse_note_node(
    path: Path,
    by_name: dict[str, list[Path]],
    by_exact: dict[str, Path],
) -> NoteNode:
    content = strip_generated_sections(read_text(path))
    meta = parse_metadata(content)
    explicit_refs = extract_refs(content)
    resolved_links = sorted(
        {
            str(target.resolve())
            for ref in explicit_refs
            if (target := resolve_ref(path, ref, by_name, by_exact)) is not None and target != path
        }
    )
    return NoteNode(
        path=str(path),
        title=extract_title(content, path),
        project=meta.project,
        tags=meta.tags,
        explicit_refs=explicit_refs,
        resolved_links=resolved_links,
    )


def load_cached_nodes(paths: AtlasPaths) -> dict[Path, NoteNode]:
    if not paths.relationships_path.is_file():
        return {}
    payload = __import__("json").loads(paths.relationships_path.read_text(encoding="utf-8"))
    note_entries = payload.get("notes", [])
    nodes: dict[Path, NoteNode] = {}
    for item in note_entries:
        path = Path(item["path"]).resolve()
        if path.exists():
            nodes[path] = NoteNode(**item)
    return nodes


def _related_candidates(node: NoteNode, nodes: dict[Path, NoteNode]) -> list[tuple[int, Path]]:
    scores: list[tuple[int, Path]] = []
    node_links = {Path(item) for item in node.resolved_links}
    for other_path, other_node in nodes.items():
        if other_path == Path(node.path):
            continue
        score = 0
        if node.project and other_node.project == node.project:
            score += 3
        score += len(set(node.tags).intersection(other_node.tags))
        other_links = {Path(item) for item in other_node.resolved_links}
        if other_path in node_links or Path(node.path) in other_links:
            score += 5
        if Path(node.path).parent == other_path.parent:
            score += 1
        if score > 0:
            scores.append((score, other_path))
    return sorted(scores, key=lambda pair: (-pair[0], pair[1].as_posix()))


def build_graph(
    paths: AtlasPaths,
    touched: list[Path] | None = None,
) -> tuple[dict[Path, NoteNode], dict[Path, set[Path]], dict[Path, list[Path]], str, int]:
    note_paths = [path.resolve() for path in list_graph_notes(paths)]
    by_name, by_exact = _candidate_maps(note_paths)

    if touched is None or not paths.relationships_path.exists():
        nodes = {path: parse_note_node(path, by_name, by_exact) for path in note_paths}
        mode = "full"
        parsed_notes = len(note_paths)
    else:
        cached = load_cached_nodes(paths)
        nodes = {path: node for path, node in cached.items() if path in set(note_paths)}
        touched_paths = {path.resolve() for path in touched}
        parsed_paths = {path for path in touched_paths if path.exists()}
        for path in parsed_paths:
            nodes[path] = parse_note_node(path, by_name, by_exact)
        mode = "incremental"
        parsed_notes = len(parsed_paths)

    backlinks: dict[Path, set[Path]] = {path: set() for path in note_paths}
    for path, node in nodes.items():
        for target in (Path(item) for item in node.resolved_links):
            if target in backlinks and target != path:
                backlinks[target].add(path)

    related: dict[Path, list[Path]] = {}
    for path, node in nodes.items():
        related[path] = [item for _, item in _related_candidates(node, nodes)[:5]]

    return nodes, backlinks, related, mode, parsed_notes


def _sync_meta(result: NoteGraphSyncResult) -> dict[str, object]:
    return {
        "mode": result.mode,
        "parsed_notes": result.parsed_notes,
        "changed_notes": result.changed_notes,
        "note_count": result.note_count,
    }


def sync_note_graph(paths: AtlasPaths, touched: list[Path] | None = None) -> NoteGraphSyncResult:
    ensure_state(paths)
    nodes, backlinks, related, mode, parsed_notes = build_graph(paths, touched=touched)
    titles = {path: node.title for path, node in nodes.items()}
    changed = 0

    write_set: set[Path]
    if touched is None:
        write_set = set(nodes)
    else:
        touched_paths = {path.resolve() for path in touched}
        write_set = set(touched_paths)
        for path in touched_paths:
            write_set.update(backlinks.get(path, set()))
            write_set.update(related.get(path, []))
            node = nodes.get(path)
            if node is not None:
                write_set.update(Path(item) for item in node.resolved_links)
                for other_path, other_node in nodes.items():
                    if other_node.project == node.project or set(other_node.tags).intersection(
                        node.tags
                    ):
                        write_set.add(other_path)

    for path in sorted(write_set, key=lambda item: item.as_posix()):
        if path not in nodes or not path.exists():
            continue
        content = read_text(path)
        backlinks_block = render_relationship_section(
            "Backlinks",
            sorted(backlinks.get(path, set()), key=lambda item: item.as_posix()),
            path,
            titles,
        )
        related_block = render_relationship_section(
            "Related",
            related.get(path, []),
            path,
            titles,
        )
        updated = replace_or_append_block(content, BACKLINKS_START, BACKLINKS_END, backlinks_block)
        updated = replace_or_append_block(updated, RELATED_START, RELATED_END, related_block)
        if updated != content:
            path.write_text(updated, encoding="utf-8")
            changed += 1

    result = NoteGraphSyncResult(
        changed_notes=changed,
        note_count=len(nodes),
        parsed_notes=parsed_notes,
        mode=mode,
    )
    atomic_json_write(
        paths.relationships_path,
        {
            "meta": _sync_meta(result),
            "notes": [
                asdict(node)
                for _, node in sorted(nodes.items(), key=lambda item: item[0].as_posix())
            ],
            "backlinks": {
                str(path): [str(item) for item in sorted(items, key=lambda value: value.as_posix())]
                for path, items in sorted(backlinks.items(), key=lambda item: item[0].as_posix())
            },
            "related": {
                str(path): [str(item) for item in related[path]]
                for path in sorted(related, key=lambda item: item.as_posix())
            },
        },
    )
    atomic_json_write(
        paths.project_index_path,
        _project_index(nodes),
    )
    atomic_json_write(paths.tag_index_path, _tag_index(nodes))
    atomic_json_write(
        paths.link_index_path,
        {
            str(path): node.resolved_links
            for path, node in sorted(nodes.items(), key=lambda item: item[0].as_posix())
        },
    )
    return result


def _project_index(nodes: dict[Path, NoteNode]) -> dict[str, dict[str, object]]:
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
        content += "## Notes\n\n"
        if body.strip():
            content += body.strip() + "\n"

    target.write_text(content, encoding="utf-8")
    sync_note_graph(paths, touched=[target])
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
