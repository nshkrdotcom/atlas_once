from __future__ import annotations

from pathlib import Path

from atlas_once.shadow_workspace import ensure_shadow_project_root, shadow_root_for_project


def test_shared_shadow_rules(atlas_env: Path) -> None:
    project = atlas_env / "code" / "demo-app"
    shadow_root = atlas_env / "state" / "code" / "shadows"
    project.mkdir(parents=True)
    (project / "lib").mkdir()
    (project / "mix.exs").write_text("defmodule Demo.MixProject do\nend\n", encoding="utf-8")
    (project / "lib" / "demo.ex").write_text("defmodule Demo do\nend\n", encoding="utf-8")
    (project / ".dexter.db").write_text("source db must not mirror\n", encoding="utf-8")
    (project / ".dexterity").mkdir()
    (project / ".dexterity" / "dexterity.db").write_text("source store\n", encoding="utf-8")

    expected = shadow_root_for_project(project, shadow_root)
    actual = ensure_shadow_project_root(project, shadow_root)

    assert actual == expected
    assert (actual / "mix.exs").is_symlink()
    assert (actual / "lib").is_dir()
    assert not (actual / "lib").is_symlink()
    assert (actual / "lib" / "demo.ex").is_symlink()
    assert not (actual / ".dexter.db").exists()
    assert not (actual / ".dexterity").exists()


def test_shadow_keeps_source_symlink_directories_as_symlinks(atlas_env: Path) -> None:
    project = atlas_env / "code" / "demo-app"
    shared = atlas_env / "shared-lib"
    shadow_root = atlas_env / "state" / "code" / "shadows"
    shared.mkdir(parents=True)
    (shared / "linked.ex").write_text("defmodule Linked do\nend\n", encoding="utf-8")
    project.mkdir(parents=True)
    (project / "mix.exs").write_text("defmodule Demo.MixProject do\nend\n", encoding="utf-8")
    (project / "linked").symlink_to(shared, target_is_directory=True)

    actual = ensure_shadow_project_root(project, shadow_root)

    assert (actual / "linked").is_symlink()
    assert (actual / "linked").resolve() == shared.resolve()
