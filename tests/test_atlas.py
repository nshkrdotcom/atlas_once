from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest

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


def test_json_status_resolve_and_event_log(atlas_env: Path, capsys) -> None:
    project = atlas_env / "code" / "jido_symphony_prime"
    project.mkdir()
    (project / ".git").mkdir()

    assert main(["--json", "registry", "scan"]) == 0
    scan_payload = json.loads(capsys.readouterr().out)
    assert scan_payload["ok"] is True
    assert scan_payload["command"] == "registry.scan"
    assert scan_payload["data"]["project_count"] == 1

    assert main(["--json", "resolve", "jsp"]) == 0
    resolve_payload = json.loads(capsys.readouterr().out)
    assert resolve_payload["ok"] is True
    assert resolve_payload["data"]["project"]["path"] == str(project)

    assert main(["--json", "status"]) == 0
    status_payload = json.loads(capsys.readouterr().out)
    assert status_payload["ok"] is True
    assert status_payload["data"]["registry"]["project_count"] == 1
    assert status_payload["data"]["inbox"]["open_count"] == 0

    events_path = atlas_env / "config" / "atlas_once" / "events.jsonl"
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    assert [event["command"] for event in events][-3:] == [
        "registry.scan",
        "resolve",
        "status",
    ]


def test_json_resolve_error_uses_stable_exit_code(atlas_env: Path, capsys) -> None:
    assert main(["--json", "resolve", "missing-project"]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["exit_code"] == 3
    assert payload["errors"][0]["kind"] == "unknown_project"


def test_capture_note_context_and_next_json_contract(
    atlas_env: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = atlas_env / "code" / "jido_domain"
    (repo / "lib").mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / "mix.exs").write_text("defmodule Demo.MixProject do\nend\n", encoding="utf-8")
    (repo / "lib" / "demo.ex").write_text("defmodule Demo do\nend\n", encoding="utf-8")

    assert main(["--json", "registry", "scan"]) == 0
    capsys.readouterr()

    monkeypatch.setattr("sys.stdin", StringIO("Prefer workspace root for bundles."))
    assert (
        main(["--json", "capture", "--stdin", "--project", "jido_domain", "--kind", "decision"])
        == 0
    )
    capture_payload = json.loads(capsys.readouterr().out)
    entry_id = capture_payload["data"]["entry"]["entry_id"]
    assert capture_payload["data"]["entry"]["project"] == "jido_domain"

    assert main(["--json", "next"]) == 0
    next_payload = json.loads(capsys.readouterr().out)
    assert next_payload["data"]["action"] == "promote_auto"
    assert next_payload["data"]["command"] == "atlas promote auto"

    monkeypatch.setattr("sys.stdin", StringIO("Links to [[beta]]."))
    assert (
        main(
            [
                "--json",
                "note",
                "new",
                "Alpha",
                "--project",
                "jido_domain",
                "--tag",
                "memory",
                "--body-stdin",
            ]
        )
        == 0
    )
    alpha_payload = json.loads(capsys.readouterr().out)
    alpha_path = Path(alpha_payload["data"]["path"])
    assert alpha_path.is_file()

    assert (
        main(
            [
                "--json",
                "note",
                "new",
                "Beta",
                "--project",
                "jido_domain",
                "--tag",
                "memory",
                "--body",
                "Beta body.",
            ]
        )
        == 0
    )
    beta_payload = json.loads(capsys.readouterr().out)
    relationships = json.loads(
        (atlas_env / "config" / "atlas_once" / "indexes" / "relationships.json").read_text(
            encoding="utf-8"
        )
    )
    assert relationships["meta"]["mode"] == "incremental"
    assert relationships["meta"]["parsed_notes"] == 1
    assert beta_payload["data"]["sync"]["mode"] == "incremental"

    assert main(["--json", "context", "repo", "jido_domain"]) == 0
    context_payload = json.loads(capsys.readouterr().out)
    manifest = context_payload["data"]["manifest"]
    assert manifest["kind"] == "repo"
    assert Path(manifest["bundle_path"]).is_file()
    assert any(path.endswith("mix.exs") for path in manifest["included_files"])
    assert manifest["approx_tokens"] >= 1

    assert main(["--json", "promote", "entry", entry_id]) == 0
    promote_payload = json.loads(capsys.readouterr().out)
    assert Path(promote_payload["data"]["target"]).is_file()


def test_registry_scan_changed_only_reuses_unchanged_roots(atlas_env: Path, capsys) -> None:
    primary = atlas_env / "code" / "switchyard"
    primary.mkdir()
    (primary / ".git").mkdir()

    second_root = atlas_env / "north"
    second_root.mkdir()
    other = second_root / "citadel"
    other.mkdir()
    (other / ".git").mkdir()

    assert main(["registry", "root-add", str(second_root)]) == 0
    capsys.readouterr()
    assert main(["--json", "registry", "scan"]) == 0
    capsys.readouterr()
    assert main(["--json", "registry", "scan", "--changed-only"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["project_count"] == 2
    assert sorted(payload["data"]["reused_roots"]) == sorted(
        [str(atlas_env / "code"), str(second_root)]
    )


def test_context_stack_remember_is_json_clean(atlas_env: Path, capsys) -> None:
    repo = atlas_env / "code" / "switchyard"
    (repo / "lib").mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / "mix.exs").write_text("defmodule Demo.MixProject do\nend\n", encoding="utf-8")
    (repo / "lib" / "demo.ex").write_text("defmodule Demo do\nend\n", encoding="utf-8")

    assert main(["--json", "context", "stack", "--remember", str(repo)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["data"]["remembered_preset_id"] == 1
    assert Path(payload["data"]["manifest"]["bundle_path"]).is_file()


def test_generic_defaults_are_not_personal(atlas_home: Path, capsys) -> None:
    assert main(["--json", "config", "show"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["data"]["settings"]["data_home"] == str(atlas_home / "atlas_once")
    assert payload["data"]["settings"]["code_root"] is None
    assert payload["data"]["settings"]["project_roots"] == []
    assert payload["data"]["profile"] is None
    assert payload["data"]["paths"]["config_home"] == str(atlas_home / ".config" / "atlas_once")
    assert payload["data"]["paths"]["state_home"] == str(atlas_home / ".atlas_once")


def test_install_defaults_to_nshkrdotcom_profile(atlas_home: Path, capsys) -> None:
    assert main(["--json", "install"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["data"]["profile"]["name"] == "nshkrdotcom"
    assert payload["data"]["settings"]["data_home"] == str(atlas_home / "jb")
    assert payload["data"]["settings"]["code_root"] == str(atlas_home / "p" / "g" / "n")
    assert payload["data"]["settings"]["project_roots"] == [
        str(atlas_home / "p" / "g" / "n"),
        str(atlas_home / "p" / "g" / "North-Shore-AI"),
    ]
    assert (atlas_home / ".config" / "atlas_once" / "profile.json").is_file()


def test_profile_switch_and_shell_install_are_generic(atlas_home: Path, capsys) -> None:
    assert main(["--json", "config", "profile", "list"]) == 0
    profiles_payload = json.loads(capsys.readouterr().out)
    names = [item["name"] for item in profiles_payload["data"]["profiles"]]
    assert "default" in names
    assert "nshkrdotcom" in names

    assert main(["--json", "config", "profile", "use", "default"]) == 0
    use_payload = json.loads(capsys.readouterr().out)
    assert use_payload["data"]["profile"]["name"] == "default"
    assert use_payload["data"]["settings"]["data_home"] == str(atlas_home / "atlas_once")

    assert main(["--json", "config", "set", "code_root", str(atlas_home / "code")]) == 0
    set_payload = json.loads(capsys.readouterr().out)
    assert set_payload["data"]["profile"]["customized"] is True
    assert set_payload["data"]["settings"]["code_root"] == str(atlas_home / "code")

    assert main(["config", "shell", "show"]) == 0
    shell_text = capsys.readouterr().out
    assert 'docday "$@"' in shell_text
    assert "~/p/g/n/atlas_once" not in shell_text

    target = atlas_home / ".bashrc"
    target.write_text("# bashrc\n", encoding="utf-8")
    assert main(["--json", "config", "shell", "install", "--target", str(target)]) == 0
    install_payload = json.loads(capsys.readouterr().out)
    snippet_path = Path(install_payload["data"]["snippet_path"])
    assert snippet_path.is_file()
    assert "atlas_once.sh" in snippet_path.name
    assert "~/p/g/n/atlas_once" not in snippet_path.read_text(encoding="utf-8")
    bashrc = target.read_text(encoding="utf-8")
    assert "atlas_once.sh" in bashrc
