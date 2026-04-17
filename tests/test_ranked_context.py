from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from atlas_once.atlas import main
from atlas_once.config import get_paths
from atlas_once.ranked_context import (
    collect_ranked_bundle,
    prepare_ranked_manifest,
    render_prepared_ranked_bundle,
)
from atlas_once.registry import ProjectRecord


def _write(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")


def _write_ranked_config(atlas_env: Path, payload: dict[str, object]) -> Path:
    config_path = atlas_env / "config" / "atlas_once" / "ranked_contexts.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return config_path


def _make_mix_project(
    root: Path, *, readme: bool = True, files: dict[str, str] | None = None
) -> None:
    _write(root / "mix.exs", "defmodule Demo.MixProject do\nend\n")
    if readme:
        _write(root / "README.md", f"# {root.name}\n")
    for rel_path, contents in (files or {}).items():
        _write(root / rel_path, contents)


def _default_ranked_payload(
    dexterity_root: Path,
    *,
    groups: dict[str, object],
    repos: dict[str, object] | None = None,
    strategies: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "version": 3,
        "defaults": {
            "registry": {"self_owners": ["nshkrdotcom"]},
            "runtime": {"dexterity_root": str(dexterity_root)},
            "strategies": strategies
            or {"elixir_ranked_v1": {"include_readme": True, "top_files": 2}},
        },
        "repos": repos or {},
        "groups": groups,
    }


def _actual_project_root_from_shadow(shadow_root: Path) -> Path:
    return (shadow_root / "mix.exs").resolve().parent


def test_ranked_context_requires_v3_config(atlas_env: Path) -> None:
    dexterity_root = atlas_env / "dexterity"
    dexterity_root.mkdir()

    _write_ranked_config(
        atlas_env,
        {
            "version": 2,
            "defaults": {
                "registry": {"self_owners": ["nshkrdotcom"]},
                "runtime": {"dexterity_root": str(dexterity_root)},
                "strategies": {"elixir_ranked_v1": {"top_files": 1}},
            },
            "repos": {},
            "groups": {"owned-elixir-all": {"selectors": [{"owner_scope": "self"}]}},
        },
    )

    with pytest.raises(SystemExit, match="version must be 3"):
        prepare_ranked_manifest(get_paths(), "owned-elixir-all")


def test_prepare_ranked_manifest_scopes_selectors_filters_noisy_projects_and_uses_shadow_workspaces(
    atlas_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ATLAS_ONCE_SELF_OWNERS", "nshkrdotcom")
    dexterity_root = atlas_env / "dexterity"
    dexterity_root.mkdir()

    repo_alpha = atlas_env / "code" / "repo_alpha"
    _make_mix_project(
        repo_alpha,
        files={"lib/root.ex": "defmodule Root do\nend\n"},
    )
    _make_mix_project(
        repo_alpha / "core" / "engine",
        files={"lib/engine.ex": "defmodule Engine do\nend\n"},
    )
    _make_mix_project(
        repo_alpha / "_legacy" / "old_engine",
        files={"lib/old_engine.ex": "defmodule OldEngine do\nend\n"},
    )
    _make_mix_project(
        repo_alpha / "test" / "fixtures" / "fake_engine",
        files={"lib/fake_engine.ex": "defmodule FakeEngine do\nend\n"},
    )
    _make_mix_project(
        repo_alpha / "examples" / "playground",
        files={"lib/playground.ex": "defmodule Playground do\nend\n"},
    )

    other_root = atlas_env / "other"
    repo_beta = other_root / "repo_beta"
    _make_mix_project(
        repo_beta,
        files={"lib/beta.ex": "defmodule Beta do\nend\n"},
    )

    subprocess.run(["git", "init", "-q", str(repo_alpha)], check=True)
    subprocess.run(
        ["git", "-C", str(repo_alpha), "remote", "add", "origin", "n:nshkrdotcom/repo_alpha.git"],
        check=True,
    )
    subprocess.run(["git", "init", "-q", str(repo_beta)], check=True)
    subprocess.run(
        ["git", "-C", str(repo_beta), "remote", "add", "origin", "n:nshkrdotcom/repo_beta.git"],
        check=True,
    )

    _write_ranked_config(
        atlas_env,
        _default_ranked_payload(
            dexterity_root,
            groups={
                "owned-gn-primary-elixir": {
                    "selectors": [
                        {
                            "owner_scope": "self",
                            "primary_language": "elixir",
                            "relation": "primary",
                            "roots": [str(atlas_env / "code")],
                            "variant": "default",
                        }
                    ]
                }
            },
        ),
    )

    assert main(["registry", "root-add", str(other_root)]) == 0
    assert main(["registry", "scan"]) == 0

    dexter_roots: list[Path] = []
    actual_projects: list[Path] = []

    def fake_run(
        cmd: list[str],
        *,
        cwd: str | None = None,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, capture_output, text, check, env

        if cmd[:2] == ["mix", "dexterity.index"]:
            shadow_root = Path(cmd[cmd.index("--repo-root") + 1])
            dexter_roots.append(shadow_root)
            actual_projects.append(_actual_project_root_from_shadow(shadow_root))
            shadow_root.mkdir(parents=True, exist_ok=True)
            (shadow_root / ".dexter.db").write_text("shadow db\n", encoding="utf-8")
            dexterity_store = shadow_root / ".dexterity"
            dexterity_store.mkdir(parents=True, exist_ok=True)
            (dexterity_store / "dexterity.db").write_text("shadow store\n", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, "ok\n", "")

        if cmd[:2] == ["mix", "dexterity.query"]:
            shadow_root = Path(cmd[cmd.index("--repo-root") + 1])
            actual_root = _actual_project_root_from_shadow(shadow_root)
            payload_by_project = {
                str(repo_alpha): [["lib/root.ex", 0.9]],
                str(repo_alpha / "core" / "engine"): [["lib/engine.ex", 0.8]],
            }
            payload = {
                "ok": True,
                "command": "ranked_files",
                "result": payload_by_project[str(actual_root)],
            }
            return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")

        raise AssertionError(f"unexpected dexterity command: {cmd}")

    monkeypatch.setattr("atlas_once.ranked_context.subprocess.run", fake_run)

    prepared = prepare_ranked_manifest(get_paths(), "owned-gn-primary-elixir")
    bundle = render_prepared_ranked_bundle(get_paths(), "owned-gn-primary-elixir")

    assert prepared.repo_count == 1
    assert prepared.project_count == 2
    assert "# FILE: ./repo_alpha/README.md" in bundle.text
    assert "# FILE: ./repo_alpha/lib/root.ex" in bundle.text
    assert "# FILE: ./repo_alpha/core/engine/lib/engine.ex" in bundle.text
    assert "repo_beta" not in bundle.text
    assert "_legacy" not in bundle.text
    assert "test/fixtures" not in bundle.text
    assert "examples/playground" not in bundle.text

    assert actual_projects == [repo_alpha, repo_alpha / "core" / "engine"]
    for shadow_root in dexter_roots:
        assert shadow_root.is_relative_to(get_paths().state_home)
        assert (shadow_root / ".dexter.db").is_file()
        assert (shadow_root / ".dexterity" / "dexterity.db").is_file()

    assert not (repo_alpha / ".dexter.db").exists()
    assert not (repo_alpha / ".dexterity").exists()


def test_prepare_manifest_applies_overrides_and_status_exposes_selection_metadata(
    atlas_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    dexterity_root = atlas_env / "dexterity"
    dexterity_root.mkdir()

    repo = atlas_env / "code" / "jido_integration"
    _make_mix_project(
        repo,
        files={
            "lib/root_a.ex": "defmodule RootA do\nend\n",
            "lib/root_b.ex": "defmodule RootB do\nend\n",
        },
    )
    _make_mix_project(
        repo / "apps" / "foo",
        files={"lib/foo.ex": "defmodule Foo do\nend\n"},
    )
    _make_mix_project(
        repo / "apps" / "bar",
        files={
            "lib/bar_one.ex": "defmodule BarOne do\nend\n",
            "lib/bar_two.ex": "defmodule BarTwo do\nend\n",
        },
    )

    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", "n:nshkrdotcom/jido_integration.git"],
        check=True,
    )

    _write_ranked_config(
        atlas_env,
        _default_ranked_payload(
            dexterity_root,
            repos={
                "jido_integration": {
                    "ref": "jido_integration",
                    "variants": {
                        "ops-lite": {
                            "top_files": 2,
                            "projects": {
                                "apps/foo": {"exclude": True},
                                "apps/bar": {"top_files": 1},
                            },
                        }
                    },
                }
            },
            groups={"ops-lite": {"items": [{"ref": "jido_integration", "variant": "ops-lite"}]}},
        ),
    )

    assert main(["registry", "scan"]) == 0
    capsys.readouterr()

    def fake_run(
        cmd: list[str],
        *,
        cwd: str | None = None,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, capture_output, text, check, env

        if cmd[:2] == ["mix", "dexterity.index"]:
            return subprocess.CompletedProcess(cmd, 0, "ok\n", "")

        if cmd[:2] == ["mix", "dexterity.query"]:
            shadow_root = Path(cmd[cmd.index("--repo-root") + 1])
            actual_root = _actual_project_root_from_shadow(shadow_root)
            payload_by_project = {
                str(repo): [["lib/root_b.ex", 0.9], ["lib/root_a.ex", 0.8]],
                str(repo / "apps" / "bar"): [["lib/bar_two.ex", 0.9], ["lib/bar_one.ex", 0.8]],
            }
            payload = {
                "ok": True,
                "command": "ranked_files",
                "result": payload_by_project[str(actual_root)],
            }
            return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")

        raise AssertionError(f"unexpected dexterity command: {cmd}")

    monkeypatch.setattr("atlas_once.ranked_context.subprocess.run", fake_run)

    assert main(["--json", "context", "ranked", "prepare", "ops-lite"]) == 0
    prepare_payload = json.loads(capsys.readouterr().out)
    manifest = prepare_payload["data"]["prepared_manifest"]

    assert manifest["repo_count"] == 1
    assert manifest["project_count"] == 2
    repo_summary = manifest["repos"][0]
    assert repo_summary["repo_label"] == "jido_integration"
    project_summaries = {item["project_rel_path"]: item for item in repo_summary["projects"]}
    assert project_summaries["."]["selected_count"] == 2
    assert project_summaries["apps/foo"]["excluded"] is True
    assert project_summaries["apps/bar"]["selected_count"] == 1

    assert main(["--json", "context", "ranked", "status", "ops-lite"]) == 0
    status_payload = json.loads(capsys.readouterr().out)
    files = status_payload["data"]["prepared_manifest"]["files"]
    assert any(item["output_path"] == "./jido_integration/lib/root_b.ex" for item in files)
    assert any(
        item["output_path"] == "./jido_integration/apps/bar/lib/bar_two.ex"
        for item in files
    )
    assert not any("apps/foo" in item["output_path"] for item in files)


def test_prepare_manifest_warns_and_records_unmatched_project_overrides(
    atlas_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    dexterity_root = atlas_env / "dexterity"
    dexterity_root.mkdir()

    repo = atlas_env / "code" / "mezzanine"
    _make_mix_project(
        repo,
        files={"lib/root.ex": "defmodule Mezzanine.Root do\nend\n"},
    )
    _make_mix_project(
        repo / "core" / "runtime_engine",
        files={"lib/runtime_engine.ex": "defmodule Mezzanine.RuntimeEngine do\nend\n"},
    )

    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", "n:nshkrdotcom/mezzanine.git"],
        check=True,
    )

    _write_ranked_config(
        atlas_env,
        _default_ranked_payload(
            dexterity_root,
            repos={
                "mezzanine": {
                    "ref": "mezzanine",
                    "variants": {
                        "gn-ten": {
                            "top_files": 2,
                            "projects": {
                                "core/runtime_engine": {"top_files": 1},
                                "core/ops_assurance": {"top_files": 1},
                                "core/ops_audit": {"top_files": 1},
                            },
                        }
                    },
                }
            },
            groups={"gn-ten": {"items": [{"ref": "mezzanine", "variant": "gn-ten"}]}},
        ),
    )

    assert main(["registry", "scan"]) == 0
    capsys.readouterr()

    def fake_run(
        cmd: list[str],
        *,
        cwd: str | None = None,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, capture_output, text, check, env

        if cmd[:2] == ["mix", "dexterity.index"]:
            return subprocess.CompletedProcess(cmd, 0, "ok\n", "")

        if cmd[:2] == ["mix", "dexterity.query"]:
            shadow_root = Path(cmd[cmd.index("--repo-root") + 1])
            actual_root = _actual_project_root_from_shadow(shadow_root)
            payload_by_project = {
                str(repo): [["lib/root.ex", 0.9]],
                str(repo / "core" / "runtime_engine"): [["lib/runtime_engine.ex", 0.8]],
            }
            payload = {
                "ok": True,
                "command": "ranked_files",
                "result": payload_by_project[str(actual_root)],
            }
            return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")

        raise AssertionError(f"unexpected dexterity command: {cmd}")

    monkeypatch.setattr("atlas_once.ranked_context.subprocess.run", fake_run)

    assert main(["context", "ranked", "prepare", "gn-ten"]) == 0
    prepare_capture = capsys.readouterr()
    assert "prepared gn-ten:" in prepare_capture.out
    assert "reason=unknown-project-override" in prepare_capture.err
    assert "override=core/ops_assurance" in prepare_capture.err
    assert "override=core/ops_audit" in prepare_capture.err

    assert main(["--json", "context", "ranked", "status", "gn-ten"]) == 0
    status_payload = json.loads(capsys.readouterr().out)
    repo_summary = status_payload["data"]["prepared_manifest"]["repos"][0]
    assert repo_summary["unmatched_project_overrides"] == [
        "core/ops_assurance",
        "core/ops_audit",
    ]
    assert repo_summary["projects"][0]["project_rel_path"] == "."
    assert repo_summary["projects"][1]["project_rel_path"] == "core/runtime_engine"


def test_atlas_context_ranked_prepare_then_render_uses_current_contents_without_shelling_out_again(
    atlas_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    dexterity_root = atlas_env / "dexterity"
    dexterity_root.mkdir()

    repo = atlas_env / "code" / "repo_alpha"
    _make_mix_project(
        repo,
        files={"lib/root.ex": "defmodule Root do\nend\n"},
    )

    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", "n:nshkrdotcom/repo_alpha.git"],
        check=True,
    )

    _write_ranked_config(
        atlas_env,
        _default_ranked_payload(
            dexterity_root,
            groups={"owned-elixir": {"items": [{"ref": "repo_alpha", "variant": "default"}]}},
            strategies={"elixir_ranked_v1": {"include_readme": True, "top_files": 1}},
        ),
    )

    assert main(["registry", "scan"]) == 0
    capsys.readouterr()

    def fake_run(
        cmd: list[str],
        *,
        cwd: str | None = None,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, capture_output, text, check, env

        if cmd[:2] == ["mix", "dexterity.index"]:
            return subprocess.CompletedProcess(cmd, 0, "ok\n", "")

        if cmd[:2] == ["mix", "dexterity.query"]:
            payload = {"ok": True, "command": "ranked_files", "result": [["lib/root.ex", 0.9]]}
            return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")

        raise AssertionError(f"unexpected dexterity command: {cmd}")

    monkeypatch.setattr("atlas_once.ranked_context.subprocess.run", fake_run)

    assert main(["--json", "context", "ranked", "prepare", "owned-elixir"]) == 0
    prepare_payload = json.loads(capsys.readouterr().out)
    prepared_manifest_path = Path(prepare_payload["data"]["prepared_manifest"]["manifest_path"])
    assert prepared_manifest_path.is_file()

    (repo / "lib" / "root.ex").write_text("defmodule Root do\n  :updated\nend\n", encoding="utf-8")

    def fail_run(
        cmd: list[str],
        *,
        cwd: str | None = None,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, capture_output, text, check, env
        raise AssertionError(f"render should not shell out: {cmd}")

    monkeypatch.setattr("atlas_once.ranked_context.subprocess.run", fail_run)

    assert main(["--json", "context", "ranked", "owned-elixir"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert payload["command"] == "context.ranked"
    assert payload["data"]["prepared_manifest"]["manifest_path"] == str(prepared_manifest_path)
    rendered = Path(payload["data"]["manifest"]["bundle_path"]).read_text(encoding="utf-8")
    assert ":updated" in rendered


def test_collect_ranked_bundle_falls_back_to_lib_files_when_ranked_query_is_empty(
    atlas_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dexterity_root = atlas_env / "dexterity"
    dexterity_root.mkdir()

    repo = atlas_env / "code" / "tiny_repo"
    _make_mix_project(
        repo,
        files={
            "lib/a.ex": "defmodule Tiny.A do\nend\n",
            "lib/b.ex": "defmodule Tiny.B do\nend\n",
        },
    )

    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", "n:nshkrdotcom/tiny_repo.git"],
        check=True,
    )

    _write_ranked_config(
        atlas_env,
        _default_ranked_payload(
            dexterity_root,
            groups={"tiny": {"items": [{"ref": "tiny_repo", "variant": "default"}]}},
            strategies={"elixir_ranked_v1": {"include_readme": True, "top_files": 1}},
        ),
    )

    assert main(["registry", "scan"]) == 0

    def fake_run(
        cmd: list[str],
        *,
        cwd: str | None = None,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, capture_output, text, check, env

        if cmd[:2] == ["mix", "dexterity.index"]:
            return subprocess.CompletedProcess(cmd, 0, "ok\n", "")

        if cmd[:2] == ["mix", "dexterity.query"]:
            payload = {"ok": True, "command": "ranked_files", "result": []}
            return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")

        raise AssertionError(f"unexpected dexterity command: {cmd}")

    monkeypatch.setattr("atlas_once.ranked_context.subprocess.run", fake_run)

    bundle = collect_ranked_bundle(get_paths(), "tiny")

    assert "# FILE: ./tiny_repo/README.md" in bundle.text
    assert "# FILE: ./tiny_repo/lib/a.ex" in bundle.text
    assert "# FILE: ./tiny_repo/lib/b.ex" not in bundle.text


def test_prepare_manifest_budget_caps_elixir_selection_by_bytes_and_reports_budget_metadata(
    atlas_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    dexterity_root = atlas_env / "dexterity"
    dexterity_root.mkdir()

    repo = atlas_env / "code" / "budget_repo"
    _make_mix_project(
        repo,
        readme=False,
        files={
            "lib/a.ex": "defmodule BudgetRepo.A do\n  def run, do: :alpha\nend\n",
            "lib/b.ex": "defmodule BudgetRepo.B do\n  def run, do: :bravo\nend\n",
        },
    )

    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", "n:nshkrdotcom/budget_repo.git"],
        check=True,
    )

    first_file = repo / "lib" / "a.ex"
    second_file = repo / "lib" / "b.ex"
    byte_budget = first_file.stat().st_size + 4

    _write_ranked_config(
        atlas_env,
        _default_ranked_payload(
            dexterity_root,
            groups={"budgeted": {"items": [{"ref": "budget_repo", "variant": "budgeted"}]}},
            repos={
                "budget_repo": {
                    "ref": "budget_repo",
                    "variants": {
                        "budgeted": {
                            "include_readme": False,
                            "top_files": 2,
                            "max_bytes": byte_budget,
                        }
                    },
                }
            },
        ),
    )

    assert main(["registry", "scan"]) == 0
    capsys.readouterr()

    def fake_run(
        cmd: list[str],
        *,
        cwd: str | None = None,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, capture_output, text, check, env

        if cmd[:2] == ["mix", "dexterity.index"]:
            return subprocess.CompletedProcess(cmd, 0, "ok\n", "")

        if cmd[:2] == ["mix", "dexterity.query"]:
            payload = {
                "ok": True,
                "command": "ranked_files",
                "result": [["lib/a.ex", 0.9], ["lib/b.ex", 0.8]],
            }
            return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")

        raise AssertionError(f"unexpected dexterity command: {cmd}")

    monkeypatch.setattr("atlas_once.ranked_context.subprocess.run", fake_run)

    assert main(["--json", "context", "ranked", "prepare", "budgeted"]) == 0
    payload = json.loads(capsys.readouterr().out)
    manifest = payload["data"]["prepared_manifest"]
    repo_summary = manifest["repos"][0]
    root_summary = repo_summary["projects"][0]

    assert manifest["selection_mode"] == "budget"
    assert manifest["consumed_bytes"] == first_file.stat().st_size
    assert manifest["budget"]["max_bytes"] == byte_budget
    assert root_summary["selected_count"] == 1
    assert root_summary["selected_bytes"] == first_file.stat().st_size
    assert root_summary["selection_mode"] == "budget"
    paths = [item["output_path"] for item in manifest["files"]]
    assert "./budget_repo/lib/a.ex" in paths
    assert "./budget_repo/lib/b.ex" not in paths
    assert manifest["consumed_bytes"] < first_file.stat().st_size + second_file.stat().st_size


def test_prepare_manifest_repo_budget_prefers_lower_project_priority_tier(
    atlas_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dexterity_root = atlas_env / "dexterity"
    dexterity_root.mkdir()

    repo = atlas_env / "code" / "priority_repo"
    _make_mix_project(repo, readme=False, files={"lib/root.ex": "defmodule PriorityRepo do\nend\n"})
    _make_mix_project(
        repo / "apps" / "alpha",
        readme=False,
        files={"lib/alpha.ex": "defmodule PriorityRepo.Alpha do\n  def pick, do: :alpha\nend\n"},
    )
    _make_mix_project(
        repo / "apps" / "beta",
        readme=False,
        files={"lib/beta.ex": "defmodule PriorityRepo.Beta do\n  def pick, do: :beta\nend\n"},
    )

    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", "n:nshkrdotcom/priority_repo.git"],
        check=True,
    )

    beta_file = repo / "apps" / "beta" / "lib" / "beta.ex"

    _write_ranked_config(
        atlas_env,
        _default_ranked_payload(
            dexterity_root,
            groups={"priority": {"items": [{"ref": "priority_repo", "variant": "priority"}]}},
            repos={
                "priority_repo": {
                    "ref": "priority_repo",
                    "variants": {
                        "priority": {
                            "include_readme": False,
                            "top_files": 1,
                            "max_bytes": beta_file.stat().st_size + 2,
                            "projects": {
                                ".": {"exclude": True},
                                "apps/alpha": {"priority_tier": 3},
                                "apps/beta": {"priority_tier": 1},
                            },
                        }
                    },
                }
            },
        ),
    )

    assert main(["registry", "scan"]) == 0

    def fake_run(
        cmd: list[str],
        *,
        cwd: str | None = None,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, capture_output, text, check, env

        if cmd[:2] == ["mix", "dexterity.index"]:
            return subprocess.CompletedProcess(cmd, 0, "ok\n", "")

        if cmd[:2] == ["mix", "dexterity.query"]:
            shadow_root = Path(cmd[cmd.index("--repo-root") + 1])
            actual_root = _actual_project_root_from_shadow(shadow_root)
            payload_by_project = {
                str(repo / "apps" / "alpha"): [["lib/alpha.ex", 0.9]],
                str(repo / "apps" / "beta"): [["lib/beta.ex", 0.9]],
            }
            payload = {
                "ok": True,
                "command": "ranked_files",
                "result": payload_by_project[str(actual_root)],
            }
            return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")

        raise AssertionError(f"unexpected dexterity command: {cmd}")

    monkeypatch.setattr("atlas_once.ranked_context.subprocess.run", fake_run)

    prepared = prepare_ranked_manifest(get_paths(), "priority")

    selected_paths = [item.output_rel for item in prepared.files]
    assert "priority_repo/apps/beta/lib/beta.ex" in selected_paths
    assert "priority_repo/apps/alpha/lib/alpha.ex" not in selected_paths
    repo_summary = prepared.repos[0]
    project_summaries = {item.project_rel_path: item for item in repo_summary.projects}
    assert project_summaries["apps/beta"].priority_tier == 1
    assert project_summaries["apps/alpha"].priority_tier == 3
    assert project_summaries["apps/beta"].selected_count == 1
    assert project_summaries["apps/alpha"].selected_count == 0
    assert project_summaries["apps/alpha"].exclusion_reason == "repo_budget_exhausted"


def test_collect_ranked_bundle_uses_token_budget_for_non_elixir_sources(atlas_env: Path) -> None:
    dexterity_root = atlas_env / "dexterity"
    dexterity_root.mkdir()

    repo = atlas_env / "code" / "python_repo"
    _write(repo / "pyproject.toml", '[project]\nname = "python_repo"\nversion = "0.1.0"\n')
    _write(repo / "src" / "python_repo" / "a.py", "print('alpha alpha alpha alpha alpha')\n")
    _write(repo / "src" / "python_repo" / "b.py", "print('bravo bravo bravo bravo bravo bravo')\n")

    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", "n:nshkrdotcom/python_repo.git"],
        check=True,
    )

    _write_ranked_config(
        atlas_env,
        _default_ranked_payload(
            dexterity_root,
            groups={"tiny-python": {"items": [{"ref": "python_repo", "variant": "tiny"}]}},
            repos={
                "python_repo": {
                    "ref": "python_repo",
                    "variants": {
                        "tiny": {
                            "strategy": "python_default_v1",
                            "include_readme": False,
                            "top_files": 2,
                            "max_tokens": 10,
                        }
                    },
                }
            },
            strategies={"python_default_v1": {"include_readme": False, "top_files": 2}},
        ),
    )

    assert main(["registry", "scan"]) == 0

    prepared = prepare_ranked_manifest(get_paths(), "tiny-python")

    assert len(prepared.files) == 1
    assert prepared.files[0].output_rel == "python_repo/src/python_repo/a.py"
    repo_summary = prepared.repos[0]
    assert repo_summary.selection_mode == "budget"
    assert repo_summary.selected_tokens_estimate > 0
    assert repo_summary.selected_tokens_estimate <= 10


def test_collect_ranked_bundle_applies_exclude_globs_before_budgeting(
    atlas_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dexterity_root = atlas_env / "dexterity"
    dexterity_root.mkdir()

    repo = atlas_env / "code" / "filter_repo"
    _make_mix_project(
        repo,
        readme=False,
        files={
            "lib/fixtures.ex": (
                "defmodule FilterRepo.Fixtures do\n"
                "  def giant, do: :fixture_fixture_fixture\n"
                "end\n"
            ),
            "lib/runtime.ex": "defmodule FilterRepo.Runtime do\n  def live, do: :runtime\nend\n",
        },
    )

    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", "n:nshkrdotcom/filter_repo.git"],
        check=True,
    )

    _write_ranked_config(
        atlas_env,
        _default_ranked_payload(
            dexterity_root,
            groups={"filtered": {"items": [{"ref": "filter_repo", "variant": "filtered"}]}},
            repos={
                "filter_repo": {
                    "ref": "filter_repo",
                    "variants": {
                        "filtered": {
                            "include_readme": False,
                            "top_files": 2,
                            "exclude_globs": ["lib/*fixtures*.ex"],
                        }
                    },
                }
            },
        ),
    )

    assert main(["registry", "scan"]) == 0

    def fake_run(
        cmd: list[str],
        *,
        cwd: str | None = None,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, capture_output, text, check, env

        if cmd[:2] == ["mix", "dexterity.index"]:
            return subprocess.CompletedProcess(cmd, 0, "ok\n", "")

        if cmd[:2] == ["mix", "dexterity.query"]:
            payload = {
                "ok": True,
                "command": "ranked_files",
                "result": [["lib/fixtures.ex", 0.95], ["lib/runtime.ex", 0.90]],
            }
            return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")

        raise AssertionError(f"unexpected dexterity command: {cmd}")

    monkeypatch.setattr("atlas_once.ranked_context.subprocess.run", fake_run)

    bundle = collect_ranked_bundle(get_paths(), "filtered")

    assert "# FILE: ./filter_repo/lib/runtime.ex" in bundle.text
    assert "# FILE: ./filter_repo/lib/fixtures.ex" not in bundle.text


def test_path_scoped_elixir_repo_with_project_overrides_infers_elixir_strategy_without_registry(
    atlas_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dexterity_root = atlas_env / "dexterity"
    dexterity_root.mkdir()

    repo = atlas_env / "code" / "stack_lab"
    _make_mix_project(
        repo,
        readme=False,
        files={"lib/root.ex": "defmodule StackLab do\nend\n"},
    )
    _make_mix_project(
        repo / "support" / "lab_core",
        readme=False,
        files={"lib/lab_core.ex": "defmodule StackLab.LabCore do\nend\n"},
    )
    _make_mix_project(
        repo / "support" / "other_harness",
        readme=False,
        files={"lib/other_harness.ex": "defmodule StackLab.OtherHarness do\nend\n"},
    )

    _write_ranked_config(
        atlas_env,
        _default_ranked_payload(
            dexterity_root,
            groups={"path-stack": {"items": [{"path": str(repo), "variant": "focus"}]}},
            repos={
                "stack_lab": {
                    "path": str(repo),
                    "variants": {
                        "focus": {
                            "include_readme": False,
                            "top_files": 2,
                            "project_discovery": {
                                "include_path_prefixes": ["support/lab_core"],
                            },
                            "projects": {
                                ".": {"exclude": True},
                                "support/other_harness": {"exclude": True},
                            },
                        }
                    },
                }
            },
        ),
    )

    def fake_run(
        cmd: list[str],
        *,
        cwd: str | None = None,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, capture_output, text, check, env

        if cmd[:2] == ["mix", "dexterity.index"]:
            return subprocess.CompletedProcess(cmd, 0, "ok\n", "")

        if cmd[:2] == ["mix", "dexterity.query"]:
            payload = {
                "ok": True,
                "command": "ranked_files",
                "result": [["lib/lab_core.ex", 0.9]],
            }
            return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")

        raise AssertionError(f"unexpected dexterity command: {cmd}")

    monkeypatch.setattr("atlas_once.ranked_context.subprocess.run", fake_run)

    prepared = prepare_ranked_manifest(get_paths(), "path-stack")

    assert prepared.repos[0].strategy == "elixir_ranked_v1"
    selected_paths = [item.output_rel for item in prepared.files]
    assert "stack_lab/support/lab_core/lib/lab_core.ex" in selected_paths
    assert "stack_lab/support/other_harness/lib/other_harness.ex" not in selected_paths


def test_stale_registry_record_for_stack_lab_still_infers_elixir_strategy_from_repo_root(
    atlas_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dexterity_root = atlas_env / "dexterity"
    dexterity_root.mkdir()

    repo = atlas_env / "code" / "stack_lab"
    _make_mix_project(
        repo,
        readme=False,
        files={"lib/root.ex": "defmodule StackLab do\nend\n"},
    )
    _make_mix_project(
        repo / "support" / "lab_core",
        readme=False,
        files={"lib/lab_core.ex": "defmodule StackLab.LabCore do\nend\n"},
    )

    _write_ranked_config(
        atlas_env,
        _default_ranked_payload(
            dexterity_root,
            groups={"stale-stack": {"items": [{"ref": "stack_lab", "variant": "focus"}]}},
            repos={
                "stack_lab": {
                    "ref": "stack_lab",
                    "variants": {
                        "focus": {
                            "include_readme": False,
                            "top_files": 2,
                            "project_discovery": {
                                "include_path_prefixes": ["support/lab_core"],
                            },
                            "projects": {
                                ".": {"exclude": True},
                            },
                        }
                    },
                }
            },
        ),
    )

    stale_record = ProjectRecord(
        name="stack_lab",
        slug="stack-lab",
        path=str(repo.resolve()),
        root=str(repo.resolve().parent),
        aliases=["stack_lab", "stack-lab", "stacklab"],
        manual_aliases=[],
        markers=[],
        last_scanned="stale",
        repo_id="local:stack_lab",
        languages=[],
        primary_language="",
        owner_scope="self",
        relation="primary",
        classification_source="stale_fixture",
        vcs={},
        layout={"mix_projects": []},
        capabilities={"generic_default_v1": True},
    )

    monkeypatch.setattr("atlas_once.ranked_context.load_registry", lambda paths: [stale_record])
    monkeypatch.setattr(
        "atlas_once.ranked_context.resolve_project_ref",
        lambda paths, reference, auto_scan=False: stale_record,
    )

    def fake_run(
        cmd: list[str],
        *,
        cwd: str | None = None,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, capture_output, text, check, env

        if cmd[:2] == ["mix", "dexterity.index"]:
            return subprocess.CompletedProcess(cmd, 0, "ok\n", "")

        if cmd[:2] == ["mix", "dexterity.query"]:
            payload = {
                "ok": True,
                "command": "ranked_files",
                "result": [["lib/lab_core.ex", 0.9]],
            }
            return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")

        raise AssertionError(f"unexpected dexterity command: {cmd}")

    monkeypatch.setattr("atlas_once.ranked_context.subprocess.run", fake_run)

    prepared = prepare_ranked_manifest(get_paths(), "stale-stack")

    assert prepared.repos[0].strategy == "elixir_ranked_v1"
    assert any(
        item.output_rel == "stack_lab/support/lab_core/lib/lab_core.ex"
        for item in prepared.files
    )


def test_config_ranked_install_seeds_v3_root_scoped_template(
    atlas_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ranked_path = atlas_home / ".config" / "atlas_once" / "ranked_contexts.json"

    assert main(["--json", "config", "ranked", "install", "--profile", "nshkrdotcom"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["ranked_contexts"]["status"] == "installed"

    config = json.loads(ranked_path.read_text(encoding="utf-8"))
    assert config["version"] == 3
    assert config["defaults"]["runtime"]["dexterity_root"] == "~/p/g/n/dexterity"
    assert config["defaults"]["strategies"]["elixir_ranked_v1"]["max_bytes"] == 60000
    assert config["defaults"]["strategies"]["elixir_ranked_v1"]["max_tokens"] == 15000
    assert config["groups"]["owned-elixir-all"]["selectors"] == [
        {
            "owner_scope": "self",
            "primary_language": "elixir",
            "relation": "primary",
            "roots": ["~/p/g/n"],
            "variant": "default",
        }
    ]
    assert [item["ref"] for item in config["groups"]["gn-ten"]["items"]] == [
        "app_kit",
        "extravaganza",
        "mezzanine",
        "outer_brain",
        "citadel",
        "jido_integration",
        "execution_plane",
        "ground_plane",
        "stack_lab",
        "AITrace",
    ]

    repo_definition = config["repos"]["jido_integration"]
    assert repo_definition["ref"] == "jido_integration"
    assert repo_definition["variants"]["ops-lite"]["projects"]["apps/devops_incident_response"] == {
        "top_files": 4
    }
    assert repo_definition["variants"]["gn-ten"]["max_bytes"] == 120000
    assert (
        repo_definition["variants"]["gn-ten"]["projects"]["connectors/github"][
            "priority_tier"
        ]
        == 1
    )

    assert main(["--json", "context", "ranked", "status", "owned-elixir-all"]) == 8
    error_payload = json.loads(capsys.readouterr().out)
    assert "atlas context ranked prepare owned-elixir-all" in error_payload["errors"][0]["message"]
