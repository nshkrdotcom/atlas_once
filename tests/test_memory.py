from __future__ import annotations

from pathlib import Path

from atlas_once.memory import (
    index_rebuild_main,
    memadd_main,
    memfind_main,
    memopen_main,
    memsnap_main,
    related_main,
    session_close_main,
    today_main,
)


def test_today_creates_index(atlas_env: Path, capsys) -> None:
    assert today_main(["--print"]) == 0
    path = Path(capsys.readouterr().out.strip())
    assert path.name == "index.md"
    assert path.read_text(encoding="utf-8").startswith("# ")


def test_memadd_and_find(atlas_env: Path, capsys) -> None:
    assert memadd_main(["--project", "switchyard", "remember", "the", "daemon"]) == 0
    inbox_path = Path(capsys.readouterr().out.strip())
    assert inbox_path.is_file()
    assert memfind_main(["switchyard"]) == 0
    assert "switchyard" in capsys.readouterr().out.lower()


def test_session_close_creates_template(atlas_env: Path, capsys) -> None:
    assert session_close_main(["--project", "switchyard", "debug", "--print"]) == 0
    path = Path(capsys.readouterr().out.strip())
    content = path.read_text(encoding="utf-8")
    assert "## Next Actions" in content
    assert "Project: switchyard" in content


def test_memsnap_writes_snapshot_and_metadata(atlas_env: Path, capsys) -> None:
    assert memsnap_main(["hello-world", "--", "bash", "-lc", "printf 'hello'"]) == 0
    snapshot_path = Path(capsys.readouterr().out.strip())
    assert snapshot_path.read_text(encoding="utf-8") == "hello"
    meta_path = snapshot_path.with_suffix(".json")
    assert meta_path.is_file()


def test_index_and_related(atlas_env: Path, capsys) -> None:
    docs = atlas_env / "jb" / "docs" / "20260411"
    docs.mkdir(parents=True)
    alpha = docs / "alpha.md"
    beta = docs / "beta.md"
    alpha.write_text("# Alpha\nProject: switchyard\nTags: daemon, routing\n", encoding="utf-8")
    beta.write_text("# Beta\nProject: switchyard\nTags: routing\n", encoding="utf-8")

    assert index_rebuild_main([]) == 0
    capsys.readouterr()
    assert related_main([str(alpha)]) == 0
    out = capsys.readouterr().out
    assert "beta.md" in out


def test_memopen_prints_most_recent_without_fzf(atlas_env: Path, capsys, monkeypatch) -> None:
    docs = atlas_env / "jb" / "docs" / "20260411"
    docs.mkdir(parents=True)
    note = docs / "note.md"
    note.write_text("# hi\n", encoding="utf-8")
    monkeypatch.delenv("EDITOR", raising=False)
    assert memopen_main(["--print"]) == 0
    assert capsys.readouterr().out.strip().endswith("note.md")
