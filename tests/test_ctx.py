from __future__ import annotations

from pathlib import Path

from atlas_once.markdown_ctx import main


def test_ctx_recursive(capsys, tmp_path: Path) -> None:
    root = tmp_path / "docs"
    (root / "sub").mkdir(parents=True)
    (root / "a.md").write_text("# A\n", encoding="utf-8")
    (root / "sub" / "b.md").write_text("# B", encoding="utf-8")

    assert main([str(root)]) == 0
    out = capsys.readouterr().out
    assert "# FILE: ./a.md" in out
    assert "# FILE: ./sub/b.md" in out


def test_ctx_pwd_only(capsys, tmp_path: Path) -> None:
    root = tmp_path / "docs"
    (root / "sub").mkdir(parents=True)
    (root / "a.md").write_text("# A\n", encoding="utf-8")
    (root / "sub" / "b.md").write_text("# B\n", encoding="utf-8")

    assert main(["--pwd-only", str(root)]) == 0
    out = capsys.readouterr().out
    assert "# FILE: ./a.md" in out
    assert "sub/b.md" not in out
