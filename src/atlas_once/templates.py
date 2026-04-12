from __future__ import annotations

from datetime import datetime


def daily_note_template(day: str) -> str:
    return (
        f"# {day}\n\n"
        "## Today\n\n"
        "## Focus\n\n"
        "## Repos\n\n"
        "## Notes\n\n"
        "## Next\n"
    )


def session_template(project: str, slug: str, now: datetime) -> str:
    title = f"{project} session" if project else slug.replace("-", " ")
    return (
        f"# {title}\n"
        f"Date: {now:%Y-%m-%d}\n"
        f"Time: {now:%H:%M}\n"
        f"Project: {project or 'unspecified'}\n"
        "Tags:\n\n"
        "## Done\n\n"
        "## Current State\n\n"
        "## Blockers\n\n"
        "## Next Actions\n\n"
        "## Files\n\n"
        "## Related Repos\n"
    )
