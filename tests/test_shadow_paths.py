from __future__ import annotations

from pathlib import Path

from atlas_once.shadow_workspace import ensure_shadow_project_root, shadow_root_for_project


def test_shared_shadow_rules(atlas_env: Path) -> None:
    project = atlas_env / "code" / "demo-app"
    shadow_root = atlas_env / "state" / "code" / "shadows"
    project.mkdir(parents=True)
    (project / "mix.exs").write_text("defmodule Demo.MixProject do\nend\n", encoding="utf-8")
    (project / ".dexter.db").write_text("source db must not mirror\n", encoding="utf-8")
    (project / ".dexterity").mkdir()
    (project / ".dexterity" / "dexterity.db").write_text("source store\n", encoding="utf-8")

    expected = shadow_root_for_project(project, shadow_root)
    actual = ensure_shadow_project_root(project, shadow_root)

    assert actual == expected
    assert (actual / "mix.exs").is_symlink()
    assert not (actual / ".dexter.db").exists()
    assert not (actual / ".dexterity").exists()
