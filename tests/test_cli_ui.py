from __future__ import annotations

from atlas_once.atlas import _git_status_text
from atlas_once.cli_ui import Cell, Column, render_table, strip_ansi


def test_render_table_aligns_columns_with_colored_cells() -> None:
    text = render_table(
        [
            {"repo": "short", "state": Cell("dirty", "yellow"), "ab": "3/4"},
            {"repo": "much-longer", "state": Cell("clean", "green"), "ab": "0/0"},
        ],
        [
            Column("repo", "REPO"),
            Column("state", "STATE"),
            Column("ab", "A/B", align="right"),
        ],
        color=True,
    )

    assert "\x1b[" in text
    plain_lines = strip_ansi(text).splitlines()
    assert "\t" not in strip_ansi(text)
    assert plain_lines[1].index("dirty") == plain_lines[2].index("clean")
    assert plain_lines[1].index("3/4") == plain_lines[2].index("0/0")


def test_git_status_text_uses_compact_aligned_columns(monkeypatch) -> None:
    monkeypatch.delenv("CLICOLOR_FORCE", raising=False)
    data = {
        "repo_count": 2,
        "dirty_count": 1,
        "unpushed_count": 1,
        "stale_count": 0,
        "source": "cache",
        "repos": [
            {
                "repo_ref": "short",
                "working_dirty": True,
                "index_dirty": False,
                "untracked_count": 0,
                "conflicted": False,
                "ahead": 3,
                "behind": 4,
                "branch": "feature",
                "path": "/tmp/short",
            },
            {
                "repo_ref": "much-longer",
                "working_dirty": False,
                "index_dirty": False,
                "untracked_count": 0,
                "conflicted": False,
                "ahead": 0,
                "behind": 0,
                "branch": "main",
                "path": "/tmp/much-longer",
            },
        ],
    }

    text = _git_status_text(data)
    lines = text.splitlines()

    assert "\t" not in text
    assert "ahead=" not in text
    assert "behind=" not in text
    assert "REPO" in text
    assert "A/B" in text
    assert "3/4" in text
    assert "0/0" in text
    assert lines[3].index("dirty") == lines[4].index("clean")
    assert lines[3].index("3/4") == lines[4].index("0/0")


def test_git_status_text_colorizes_when_forced(monkeypatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("CLICOLOR_FORCE", "1")
    data = {
        "repo_count": 1,
        "dirty_count": 1,
        "unpushed_count": 0,
        "stale_count": 0,
        "source": "cache",
        "repos": [
            {
                "repo_ref": "atlas-once",
                "working_dirty": True,
                "index_dirty": False,
                "untracked_count": 0,
                "conflicted": False,
                "ahead": 0,
                "behind": 0,
                "branch": "main",
                "path": "/tmp/atlas_once",
            }
        ],
    }

    assert "\x1b[" in _git_status_text(data)
