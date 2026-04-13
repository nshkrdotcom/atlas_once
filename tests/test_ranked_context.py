from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from atlas_once.atlas import main
from atlas_once.config import get_paths
from atlas_once.ranked_context import (
    collect_ranked_bundle,
    load_ranked_configs,
    prepare_ranked_manifest,
    render_prepared_ranked_bundle,
)


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


def test_ranked_context_config_rejects_project_whitelist_field(atlas_env: Path) -> None:
    dexterity_root = atlas_env / "dexterity"
    dexterity_root.mkdir()

    _write_ranked_config(
        atlas_env,
        {
            "version": 1,
            "defaults": {"dexterity_root": str(dexterity_root), "top_files": 10},
            "configs": {
                "ops-default": {
                    "repos": [
                        {
                            "path": str(atlas_env / "code" / "repo_alpha"),
                            "projects": {"apps/foo": {"include": True}},
                        }
                    ]
                }
            },
        },
    )

    with pytest.raises(SystemExit, match="include"):
        load_ranked_configs(get_paths())


def test_prepare_ranked_manifest_applies_repo_grouping_blacklist_and_graylist(
    atlas_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dexterity_root = atlas_env / "dexterity"
    dexterity_root.mkdir()

    repo_alpha = atlas_env / "code" / "repo_alpha"
    _make_mix_project(
        repo_alpha,
        files={
            "lib/root_a.ex": "defmodule RootA do\nend\n",
            "lib/root_b.ex": "defmodule RootB do\nend\n",
        },
    )
    _make_mix_project(
        repo_alpha / "apps" / "foo",
        files={"lib/foo.ex": "defmodule Foo do\nend\n"},
    )
    _make_mix_project(
        repo_alpha / "apps" / "bar",
        files={
            "lib/bar_one.ex": "defmodule BarOne do\nend\n",
            "lib/bar_two.ex": "defmodule BarTwo do\nend\n",
            "lib/bar_three.ex": "defmodule BarThree do\nend\n",
        },
    )

    repo_beta = atlas_env / "code" / "repo_beta"
    _make_mix_project(
        repo_beta,
        files={"lib/beta.ex": "defmodule Beta do\nend\n"},
    )

    _write_ranked_config(
        atlas_env,
        {
            "version": 1,
            "defaults": {
                "dexterity_root": str(dexterity_root),
                "top_files": 2,
                "include_readme": True,
            },
            "configs": {
                "ops-default": {
                    "repos": [
                        {
                            "path": str(repo_alpha),
                            "projects": {
                                "apps/foo": {"exclude": True},
                                "apps/bar": {"top_percent": 0.5},
                            },
                        },
                        {"path": str(repo_beta)},
                    ]
                }
            },
        },
    )

    calls: list[tuple[tuple[str, ...], str]] = []

    def fake_run(
        cmd: list[str],
        *,
        cwd: str | None = None,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del capture_output, text, check, env
        calls.append((tuple(cmd), str(cwd)))

        if cmd[:2] == ["mix", "dexterity.index"]:
            return subprocess.CompletedProcess(cmd, 0, "ok\n", "")

        if cmd[:2] == ["mix", "dexterity.query"]:
            repo_root = Path(cmd[cmd.index("--repo-root") + 1])
            limit = int(cmd[cmd.index("--limit") + 1])

            ranked_by_root = {
                str(repo_alpha): [
                    ["lib/root_b.ex", 0.9],
                    ["lib/root_a.ex", 0.8],
                ],
                str(repo_alpha / "apps" / "bar"): [
                    ["lib/bar_three.ex", 0.9],
                    ["lib/bar_one.ex", 0.8],
                    ["lib/bar_two.ex", 0.7],
                ],
                str(repo_beta): [["lib/beta.ex", 0.95]],
            }

            payload = {
                "ok": True,
                "command": "ranked_files",
                "result": ranked_by_root[str(repo_root)][:limit],
            }
            return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")

        raise AssertionError(f"unexpected dexterity command: {cmd}")

    monkeypatch.setattr("atlas_once.ranked_context.subprocess.run", fake_run)

    prepared = prepare_ranked_manifest(get_paths(), "ops-default")
    bundle = render_prepared_ranked_bundle(get_paths(), "ops-default")

    assert "# FILE: ./repo_alpha/README.md" in bundle.text
    assert "# FILE: ./repo_alpha/lib/root_b.ex" in bundle.text
    assert "# FILE: ./repo_alpha/lib/root_a.ex" in bundle.text
    assert "# FILE: ./repo_alpha/apps/bar/README.md" in bundle.text
    assert "# FILE: ./repo_alpha/apps/bar/lib/bar_three.ex" in bundle.text
    assert "# FILE: ./repo_alpha/apps/bar/lib/bar_one.ex" in bundle.text
    assert "# FILE: ./repo_beta/README.md" in bundle.text
    assert "# FILE: ./repo_beta/lib/beta.ex" in bundle.text

    assert "apps/foo" not in bundle.text
    assert "===== " not in bundle.text

    queried_roots = [
        cmd[cmd.index("--repo-root") + 1] for cmd, _cwd in calls if "dexterity.query" in cmd
    ]
    assert queried_roots == [str(repo_alpha), str(repo_alpha / "apps" / "bar"), str(repo_beta)]
    assert prepared.repo_count == 2
    assert prepared.project_count == 3

    bar_query = next(
        cmd
        for cmd, _cwd in calls
        if "dexterity.query" in cmd
        and "--repo-root" in cmd
        and str(repo_alpha / "apps" / "bar") in cmd
    )
    assert bar_query[bar_query.index("--limit") + 1] == "2"


def test_atlas_context_ranked_prepare_then_render_uses_current_contents_and_returns_manifest_json(
    atlas_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    dexterity_root = atlas_env / "dexterity"
    dexterity_root.mkdir()

    repo = atlas_env / "code" / "jido_integration"
    _make_mix_project(
        repo,
        files={
            "lib/root.ex": "defmodule Root do\nend\n",
            "lib/extra.ex": "defmodule Extra do\nend\n",
        },
    )

    _write_ranked_config(
        atlas_env,
        {
            "version": 1,
            "defaults": {
                "dexterity_root": str(dexterity_root),
                "top_files": 1,
                "include_readme": True,
            },
            "configs": {"ops-default": {"repos": [{"ref": "jido_integration"}]}},
        },
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

    assert main(["--json", "context", "ranked", "prepare", "ops-default"]) == 0
    prepare_payload = json.loads(capsys.readouterr().out)
    assert prepare_payload["ok"] is True
    assert prepare_payload["command"] == "context.ranked.prepare"
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

    assert main(["--json", "context", "ranked", "ops-default"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert payload["command"] == "context.ranked"
    assert payload["data"]["config"] == "ops-default"
    assert payload["data"]["prepared_manifest"]["manifest_path"] == str(prepared_manifest_path)
    assert payload["data"]["manifest"]["kind"] == "ranked"
    assert Path(payload["data"]["manifest"]["bundle_path"]).is_file()
    assert any(path.endswith("README.md") for path in payload["data"]["manifest"]["included_files"])
    assert any(
        path.endswith("lib/root.ex") for path in payload["data"]["manifest"]["included_files"]
    )
    rendered = Path(payload["data"]["manifest"]["bundle_path"]).read_text(encoding="utf-8")
    assert ":updated" in rendered


def test_context_ranked_prepare_emits_progress_and_status_exposes_file_list(
    atlas_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    dexterity_root = atlas_env / "dexterity"
    dexterity_root.mkdir()

    repo = atlas_env / "code" / "repo_alpha"
    _make_mix_project(
        repo,
        files={"lib/root.ex": "defmodule Root do\nend\n"},
    )
    _make_mix_project(
        repo / "apps" / "foo",
        files={"lib/foo.ex": "defmodule Foo do\nend\n"},
    )

    _write_ranked_config(
        atlas_env,
        {
            "version": 1,
            "defaults": {"dexterity_root": str(dexterity_root), "top_files": 1},
            "configs": {"ops-default": {"repos": [{"path": str(repo)}]}},
        },
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
            repo_root = Path(cmd[cmd.index("--repo-root") + 1])
            if repo_root == repo:
                payload = {"ok": True, "command": "ranked_files", "result": [["lib/root.ex", 0.9]]}
            else:
                payload = {"ok": True, "command": "ranked_files", "result": [["lib/foo.ex", 0.8]]}
            return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")
        raise AssertionError(f"unexpected dexterity command: {cmd}")

    monkeypatch.setattr("atlas_once.ranked_context.subprocess.run", fake_run)

    assert main(["--json", "context", "ranked", "prepare", "ops-default"]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert "[repo 1/1] repo_alpha" in captured.err
    assert "step=index" in captured.err
    assert "step=query" in captured.err
    assert "step=selected count=1" in captured.err

    assert main(["--json", "context", "ranked", "status", "ops-default"]) == 0
    status_payload = json.loads(capsys.readouterr().out)
    files = status_payload["data"]["prepared_manifest"]["files"]
    assert files[0]["output_path"] == "./repo_alpha/README.md"
    assert any(item["output_path"] == "./repo_alpha/lib/root.ex" for item in files)
    assert any(item["output_path"] == "./repo_alpha/apps/foo/lib/foo.ex" for item in files)


def test_missing_ranked_config_error_points_to_install_flow(
    atlas_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--json", "context", "ranked", "ops-default"]) == 8
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is False
    assert "atlas install" in payload["errors"][0]["message"]
    assert "atlas config ranked install" in payload["errors"][0]["message"]


def test_context_ranked_render_requires_prepared_manifest(
    atlas_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dexterity_root = atlas_env / "dexterity"
    dexterity_root.mkdir()

    repo = atlas_env / "code" / "repo_alpha"
    _make_mix_project(repo, files={"lib/root.ex": "defmodule Root do\nend\n"})

    _write_ranked_config(
        atlas_env,
        {
            "version": 1,
            "defaults": {"dexterity_root": str(dexterity_root), "top_files": 1},
            "configs": {"ops-default": {"repos": [{"path": str(repo)}]}},
        },
    )

    assert main(["--json", "context", "ranked", "ops-default"]) == 8
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "atlas context ranked prepare ops-default" in payload["errors"][0]["message"]


def test_context_ranked_render_rejects_stale_prepared_manifest(
    atlas_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    dexterity_root = atlas_env / "dexterity"
    dexterity_root.mkdir()

    repo = atlas_env / "code" / "repo_alpha"
    _make_mix_project(repo, files={"lib/root.ex": "defmodule Root do\nend\n"})

    config_path = _write_ranked_config(
        atlas_env,
        {
            "version": 1,
            "defaults": {"dexterity_root": str(dexterity_root), "top_files": 1},
            "configs": {"ops-default": {"repos": [{"path": str(repo)}]}},
        },
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
            payload = {"ok": True, "command": "ranked_files", "result": [["lib/root.ex", 0.9]]}
            return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")
        raise AssertionError(f"unexpected dexterity command: {cmd}")

    monkeypatch.setattr("atlas_once.ranked_context.subprocess.run", fake_run)

    assert main(["--json", "context", "ranked", "prepare", "ops-default"]) == 0
    capsys.readouterr()

    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "defaults": {"dexterity_root": str(dexterity_root), "top_files": 2},
                "configs": {"ops-default": {"repos": [{"path": str(repo)}]}},
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    assert main(["--json", "context", "ranked", "ops-default"]) == 8
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "Prepared ranked context stale" in payload["errors"][0]["message"]


def test_config_ranked_commands_seed_show_and_preserve_customized_file(
    atlas_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ranked_path = atlas_home / ".config" / "atlas_once" / "ranked_contexts.json"

    assert main(["--json", "config", "ranked", "install", "--profile", "default"]) == 0
    install_payload = json.loads(capsys.readouterr().out)
    assert install_payload["data"]["ranked_contexts"]["status"] == "installed"
    assert ranked_path.is_file()

    assert main(["config", "ranked", "path"]) == 0
    assert capsys.readouterr().out.strip() == str(ranked_path)

    assert main(["--json", "config", "ranked", "show"]) == 0
    show_payload = json.loads(capsys.readouterr().out)
    assert show_payload["data"]["path"] == str(ranked_path)
    assert show_payload["data"]["config"]["version"] == 2
    assert show_payload["data"]["config"]["groups"] == {}
    assert show_payload["data"]["config"]["repos"] == {}

    assert main(["--json", "config", "profile", "use", "nshkrdotcom"]) == 0
    use_payload = json.loads(capsys.readouterr().out)
    assert use_payload["data"]["ranked_contexts"]["status"] == "updated"
    seeded_payload = json.loads(ranked_path.read_text(encoding="utf-8"))
    assert "ops-default" in seeded_payload["groups"]

    ranked_path.write_text(
        json.dumps(
            {
                "version": 2,
                "defaults": {
                    "runtime": {"dexterity_root": "~/custom/dexterity"},
                    "strategies": {"elixir_ranked_v1": {"top_files": 3}},
                },
                "repos": {},
                "groups": {"mine": {"items": [{"path": "~/work/repo", "variant": "default"}]}},
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    assert main(["--json", "config", "profile", "use", "default"]) == 0
    preserve_payload = json.loads(capsys.readouterr().out)
    assert preserve_payload["data"]["ranked_contexts"]["status"] == "preserved_custom"
    preserved_payload = json.loads(ranked_path.read_text(encoding="utf-8"))
    assert preserved_payload["groups"] == {
        "mine": {"items": [{"path": "~/work/repo", "variant": "default"}]}
    }

    assert (
        main(["--json", "config", "ranked", "install", "--profile", "nshkrdotcom", "--force"]) == 0
    )
    force_payload = json.loads(capsys.readouterr().out)
    assert force_payload["data"]["ranked_contexts"]["status"] == "updated"
    refreshed_payload = json.loads(ranked_path.read_text(encoding="utf-8"))
    assert "ops-default" in refreshed_payload["groups"]


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

    _write_ranked_config(
        atlas_env,
        {
            "version": 1,
            "defaults": {
                "dexterity_root": str(dexterity_root),
                "top_files": 1,
                "include_readme": True,
            },
            "configs": {"ops-default": {"repos": [{"path": str(repo)}]}},
        },
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
            payload = {"ok": True, "command": "ranked_files", "result": []}
            return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")

        raise AssertionError(f"unexpected dexterity command: {cmd}")

    monkeypatch.setattr("atlas_once.ranked_context.subprocess.run", fake_run)

    bundle = collect_ranked_bundle(get_paths(), "ops-default")

    assert "# FILE: ./tiny_repo/README.md" in bundle.text
    assert "# FILE: ./tiny_repo/lib/a.ex" in bundle.text
    assert "# FILE: ./tiny_repo/lib/b.ex" not in bundle.text
