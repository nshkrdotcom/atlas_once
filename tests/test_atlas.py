from __future__ import annotations

import json
from pathlib import Path

from atlas_once.atlas import main


def test_atlas_dashboard_shows_primary_interface(atlas_env: Path, capsys) -> None:
    project = atlas_env / "code" / "atlas_once"
    project.mkdir()
    (project / ".git").mkdir()

    assert main([]) == 0
    out = capsys.readouterr().out
    assert "atlas: filesystem-first memory and context system" in out
    assert "atlas registry scan" in out
    assert "atlas capture" in out


def test_atlas_init_migrates_legacy_mcc_presets(atlas_env: Path, capsys) -> None:
    legacy = atlas_env / "config" / "mcc"
    legacy.mkdir(parents=True)
    (legacy / "presets.json").write_text(
        json.dumps([{"id": 1, "paths": ["/tmp/example"]}], indent=2),
        encoding="utf-8",
    )

    assert main(["init"]) == 0
    capsys.readouterr()
    migrated = atlas_env / "config" / "atlas_once" / "presets" / "mcc.json"
    assert migrated.is_file()


def test_registry_scan_across_multiple_roots_and_alias_resolution(atlas_env: Path, capsys) -> None:
    primary = atlas_env / "code" / "jido_symphony_prime"
    primary.mkdir()
    (primary / ".git").mkdir()

    other_root = atlas_env / "north"
    other_root.mkdir()
    second = other_root / "AITrace"
    second.mkdir()
    (second / ".git").mkdir()

    assert main(["registry", "root-add", str(other_root)]) == 0
    capsys.readouterr()
    assert main(["registry", "scan"]) == 0
    capsys.readouterr()
    assert main(["registry", "resolve", "jsp"]) == 0
    assert capsys.readouterr().out.strip() == str(primary)
    assert main(["registry", "alias-add", "AITrace", "trace"]) == 0
    capsys.readouterr()
    assert main(["registry", "resolve", "trace"]) == 0
    assert capsys.readouterr().out.strip() == str(second)


def test_capture_review_and_auto_promote(atlas_env: Path, capsys) -> None:
    project = atlas_env / "code" / "switchyard"
    project.mkdir()
    (project / ".git").mkdir()

    assert main(["registry", "scan"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "capture",
                "--project",
                "switchyard",
                "--kind",
                "decision",
                "--tag",
                "routing",
                "Prefer",
                "workspace",
                "root",
            ]
        )
        == 0
    )
    inbox_path = Path(capsys.readouterr().out.strip())
    assert inbox_path.is_file()

    assert main(["review", "inbox"]) == 0
    review = capsys.readouterr().out
    assert "suggest=decision" in review

    assert main(["promote", "auto"]) == 0
    promoted_path = Path(capsys.readouterr().out.strip())
    assert promoted_path.is_file()
    inbox_text = inbox_path.read_text(encoding="utf-8")
    assert "[status:promoted]" in inbox_text
    promoted_text = promoted_path.read_text(encoding="utf-8")
    assert "<!-- atlas:backlinks:start -->" in promoted_text
    assert "<!-- atlas:related:start -->" in promoted_text


def test_note_creation_updates_backlinks_and_related(atlas_env: Path, capsys) -> None:
    project = atlas_env / "code" / "atlas_once"
    project.mkdir()
    (project / ".git").mkdir()

    assert main(["registry", "scan"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "note",
                "new",
                "Alpha",
                "--project",
                "atlas_once",
                "--tag",
                "memory",
                "--body",
                "Link to [[beta]].",
            ]
        )
        == 0
    )
    alpha = Path(capsys.readouterr().out.strip())

    assert (
        main(
            [
                "note",
                "new",
                "Beta",
                "--project",
                "atlas_once",
                "--tag",
                "memory",
                "--body",
                "Beta body.",
            ]
        )
        == 0
    )
    beta = Path(capsys.readouterr().out.strip())

    alpha_text = alpha.read_text(encoding="utf-8")
    beta_text = beta.read_text(encoding="utf-8")
    assert "## Related" in alpha_text
    assert "Beta" in alpha_text
    assert "## Backlinks" in beta_text
    assert "Alpha" in beta_text


def test_context_repo_resolves_registry_reference(atlas_env: Path, capsys) -> None:
    repo = atlas_env / "code" / "jido_domain"
    (repo / "lib").mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / "mix.exs").write_text("defmodule Demo.MixProject do\nend\n", encoding="utf-8")
    (repo / "lib" / "demo.ex").write_text("defmodule Demo do\nend\n", encoding="utf-8")

    assert main(["registry", "scan"]) == 0
    capsys.readouterr()
    assert main(["context", "repo", "jido_domain"]) == 0
    out = capsys.readouterr().out
    assert "===== mix.exs =====" in out


def test_help_topics_include_agent_mode(atlas_env: Path, capsys) -> None:
    assert main(["help", "agent"]) == 0
    out = capsys.readouterr().out
    assert "atlas agent quickstart" in out
    assert "atlas context repo" in out
