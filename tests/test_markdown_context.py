from __future__ import annotations

from pathlib import Path

from atlas_once.markdown_ctx import collect_markdown_bundle


def test_markdown_bundle_recursive(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    (root / "sub").mkdir(parents=True)
    (root / "a.md").write_text("# A\n", encoding="utf-8")
    (root / "sub" / "b.md").write_text("# B", encoding="utf-8")

    bundle = collect_markdown_bundle(root, pwd_only=False)
    assert "# FILE: ./a.md" in bundle.text
    assert "# FILE: ./sub/b.md" in bundle.text


def test_markdown_bundle_pwd_only(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    (root / "sub").mkdir(parents=True)
    (root / "a.md").write_text("# A\n", encoding="utf-8")
    (root / "sub" / "b.md").write_text("# B\n", encoding="utf-8")

    bundle = collect_markdown_bundle(root, pwd_only=True)
    assert "# FILE: ./a.md" in bundle.text
    assert "sub/b.md" not in bundle.text
