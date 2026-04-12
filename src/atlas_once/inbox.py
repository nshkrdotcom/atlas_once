from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import AtlasPaths, ensure_state
from .notes import append_promotion_note, create_note, ensure_entity_note, sync_note_graph
from .registry import ProjectRecord
from .util import now_local, read_text, slugify

ENTRY_RE = re.compile(
    r"^- \[id:(?P<id>[^\]]+)\]"
    r" \[status:(?P<status>[^\]]+)\]"
    r" \[kind:(?P<kind>[^\]]+)\]"
    r"(?: \[project:(?P<project>[^\]]+)\])?"
    r"(?: \[tags:(?P<tags>[^\]]+)\])?"
    r"(?: \[target:(?P<target>[^\]]+)\])?"
    r" (?P<text>.*)$"
)


@dataclass(frozen=True)
class InboxEntry:
    source_path: str
    line_number: int
    entry_id: str
    status: str
    kind: str
    project: str | None
    tags: list[str]
    target: str | None
    text: str


def render_entry(entry: InboxEntry) -> str:
    parts = [
        f"- [id:{entry.entry_id}]",
        f"[status:{entry.status}]",
        f"[kind:{entry.kind}]",
    ]
    if entry.project:
        parts.append(f"[project:{entry.project}]")
    if entry.tags:
        parts.append(f"[tags:{','.join(entry.tags)}]")
    if entry.target:
        parts.append(f"[target:{entry.target}]")
    parts.append(entry.text)
    return " ".join(parts)


def parse_entry(line: str, source_path: Path, line_number: int) -> InboxEntry | None:
    match = ENTRY_RE.match(line.strip())
    if not match:
        return None
    tags = [item.strip() for item in (match.group("tags") or "").split(",") if item.strip()]
    return InboxEntry(
        source_path=str(source_path),
        line_number=line_number,
        entry_id=match.group("id"),
        status=match.group("status"),
        kind=match.group("kind"),
        project=match.group("project"),
        tags=tags,
        target=match.group("target"),
        text=match.group("text"),
    )


def inbox_path_for_day(paths: AtlasPaths, stamp: str) -> Path:
    return paths.inbox_root / f"{stamp}.md"


def create_entry(
    paths: AtlasPaths,
    text: str,
    project: ProjectRecord | None = None,
    tags: list[str] | None = None,
    kind: str = "note",
) -> InboxEntry:
    ensure_state(paths)
    tags = tags or []
    now = now_local()
    day = now.strftime("%Y%m%d")
    target = inbox_path_for_day(paths, day)
    entry = InboxEntry(
        source_path=str(target),
        line_number=0,
        entry_id=now.strftime("%Y%m%d-%H%M%S"),
        status="open",
        kind=kind,
        project=project.name if project is not None else None,
        tags=tags,
        target=None,
        text=text.strip(),
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(render_entry(entry) + "\n")
    return entry


def iter_entries(paths: AtlasPaths, day: str | None = None) -> list[InboxEntry]:
    ensure_state(paths)
    files: list[Path]
    if day:
        files = [inbox_path_for_day(paths, day)]
    else:
        files = sorted(path for path in paths.inbox_root.glob("*.md") if path.is_file())

    entries: list[InboxEntry] = []
    for file_path in files:
        if not file_path.exists():
            continue
        for line_number, line in enumerate(read_text(file_path).splitlines(), start=1):
            entry = parse_entry(line, file_path, line_number)
            if entry is not None:
                entries.append(entry)
    return entries


def review_inbox(paths: AtlasPaths, day: str | None = None) -> str:
    entries = [entry for entry in iter_entries(paths, day=day) if entry.status == "open"]
    if not entries:
        return "No open inbox entries."
    lines = ["Open inbox entries:", ""]
    for entry in entries:
        suggestion = infer_promotion_kind(entry) or "review"
        project = f" project={entry.project}" if entry.project else ""
        tags = f" tags={','.join(entry.tags)}" if entry.tags else ""
        lines.append(
            f"- {entry.entry_id} kind={entry.kind}{project}{tags} suggest={suggestion} {entry.text}"
        )
    return "\n".join(lines)


def review_daily(paths: AtlasPaths, day: str | None = None) -> str:
    stamp = day or now_local().strftime("%Y%m%d")
    inbox = [entry for entry in iter_entries(paths, day=stamp) if entry.status == "open"]
    today_note = paths.docs_root / stamp / "index.md"
    sessions = sorted(paths.sessions_root.glob(f"{stamp}-*.md"))
    lines = [
        f"Daily review: {stamp}",
        "",
        f"Today note: {today_note}",
        f"Open inbox entries: {len(inbox)}",
        f"Sessions: {len(sessions)}",
    ]
    auto = [entry for entry in inbox if infer_promotion_kind(entry) is not None]
    lines.append(f"Auto-promotable entries: {len(auto)}")
    if auto:
        lines.append("")
        for entry in auto:
            lines.append(f"- {entry.entry_id} -> {infer_promotion_kind(entry)}")
    return "\n".join(lines)


def infer_promotion_kind(entry: InboxEntry) -> str | None:
    if entry.kind in {"decision", "project", "topic", "person"}:
        return entry.kind
    for tag in entry.tags:
        if tag in {"decision", "project", "topic", "person"}:
            return tag
    return None


def update_entry(paths: AtlasPaths, updated: InboxEntry) -> None:
    file_path = Path(updated.source_path)
    lines = read_text(file_path).splitlines()
    lines[updated.line_number - 1] = render_entry(updated)
    file_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def promote_entry(
    paths: AtlasPaths,
    entry_id: str,
    kind: str | None = None,
    title: str | None = None,
    project: ProjectRecord | None = None,
) -> Path:
    entries = iter_entries(paths)
    for entry in entries:
        if entry.entry_id != entry_id:
            continue

        effective_kind = kind or infer_promotion_kind(entry) or "note"
        note_title = title or entry.text[:80]
        needs_sync = False
        project_record = (
            project
            if project is not None
            else (
                ProjectRecord(
                    name=entry.project,
                    slug=slugify(entry.project),
                    path="",
                    root="",
                    aliases=[slugify(entry.project)],
                    manual_aliases=[],
                    markers=[],
                    last_scanned="promote",
                )
                if entry.project
                else None
            )
        )

        if effective_kind == "project":
            if project_record is None and entry.project is None:
                project_record = ProjectRecord(
                    name=note_title,
                    slug=slugify(note_title),
                    path="",
                    root="",
                    aliases=[slugify(note_title)],
                    manual_aliases=[],
                    markers=[],
                    last_scanned="promote",
                )
            target = ensure_entity_note(paths, "project", note_title, project_record, entry.tags)
            append_promotion_note(
                target, datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"), entry.text
            )
        elif effective_kind in {"topic", "person"}:
            target = ensure_entity_note(
                paths, effective_kind, note_title, project_record, entry.tags
            )
            append_promotion_note(
                target, datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"), entry.text
            )
        else:
            target = create_note(
                paths,
                title=note_title,
                kind=effective_kind,
                project=project_record,
                tags=entry.tags,
                body=entry.text,
            )
            needs_sync = False

        updated = InboxEntry(
            source_path=entry.source_path,
            line_number=entry.line_number,
            entry_id=entry.entry_id,
            status="promoted",
            kind=entry.kind,
            project=entry.project,
            tags=entry.tags,
            target=str(target),
            text=entry.text,
        )
        update_entry(paths, updated)
        if effective_kind in {"project", "topic", "person"}:
            needs_sync = True
        if needs_sync:
            sync_note_graph(paths, touched=[target])
        return target

    raise SystemExit(f"Unknown inbox entry id: {entry_id}")


def promote_auto(paths: AtlasPaths, day: str | None = None) -> list[Path]:
    targets: list[Path] = []
    for entry in iter_entries(paths, day=day):
        if entry.status != "open":
            continue
        kind = infer_promotion_kind(entry)
        if kind is None:
            continue
        targets.append(promote_entry(paths, entry.entry_id, kind=kind))
    return targets
