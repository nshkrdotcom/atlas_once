from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from atlas_once.atlas import main


def _write(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")


def _init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)


def _add_remote(path: Path, name: str, url: str) -> None:
    subprocess.run(["git", "-C", str(path), "remote", "add", name, url], check=True)


def _write_ranked_config(atlas_env: Path, payload: dict[str, object]) -> Path:
    config_path = atlas_env / "config" / "atlas_once" / "ranked_contexts.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return config_path


def _make_elixir_repo(
    root: Path,
    *,
    readme: bool = True,
    extra_files: dict[str, str] | None = None,
) -> None:
    _write(root / "mix.exs", "defmodule Demo.MixProject do\nend\n")
    if readme:
        _write(root / "README.md", f"# {root.name}\n")
    for rel_path, contents in (extra_files or {}).items():
        _write(root / rel_path, contents)


def _make_python_repo(root: Path, *, readme: bool = True, extra_files: dict[str, str]) -> None:
    _write(
        root / "pyproject.toml",
        '[project]\nname = "demo"\nversion = "0.1.0"\n',
    )
    if readme:
        _write(root / "README.md", f"# {root.name}\n")
    for rel_path, contents in extra_files.items():
        _write(root / rel_path, contents)


def test_registry_scan_collects_repo_metadata_languages_and_mix_projects(
    atlas_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ATLAS_ONCE_SELF_OWNERS", "nshkrdotcom")

    repo = atlas_env / "code" / "jido"
    _make_elixir_repo(
        repo,
        extra_files={
            "lib/jido.ex": "defmodule Jido do\nend\n",
            "apps/foo/mix.exs": "defmodule Foo.MixProject do\nend\n",
            "apps/foo/lib/foo.ex": "defmodule Foo do\nend\n",
            "dist/archive/example/mix.exs": "defmodule Ignore.MixProject do\nend\n",
        },
    )
    _init_git_repo(repo)
    _add_remote(repo, "origin", "n:nshkrdotcom/jido.git")
    _add_remote(repo, "upstream", "https://github.com/agentjido/jido.git")

    assert main(["--json", "registry", "scan"]) == 0
    payload = json.loads(capsys.readouterr().out)
    projects = payload["data"]["projects"]
    record = next(project for project in projects if project["name"] == "jido")

    assert record["owner_scope"] == "self"
    assert record["relation"] == "fork"
    assert record["classification_source"] == "local_remote_heuristic"
    assert "elixir" in record["languages"]
    assert record["primary_language"] == "elixir"
    assert record["vcs"]["origin"]["owner"] == "nshkrdotcom"
    assert record["vcs"]["upstream"]["owner"] == "agentjido"
    assert {"rel_path": ".", "role": "root"} in record["layout"]["mix_projects"]
    assert {"rel_path": "apps/foo", "role": "app"} in record["layout"]["mix_projects"]
    assert not any(
        item["rel_path"].startswith("dist/") for item in record["layout"]["mix_projects"]
    )
    assert record["capabilities"]["elixir_ranked_v1"] is True


def test_registry_list_filters_by_owner_language_and_relation(
    atlas_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ATLAS_ONCE_SELF_OWNERS", "nshkrdotcom")

    owned = atlas_env / "code" / "owned_elixir"
    _make_elixir_repo(owned, extra_files={"lib/owned.ex": "defmodule Owned do\nend\n"})
    _init_git_repo(owned)
    _add_remote(owned, "origin", "n:nshkrdotcom/owned_elixir.git")

    external = atlas_env / "code" / "external_python"
    _make_python_repo(
        external,
        extra_files={"src/external_python/main.py": "print('hi')\n"},
    )
    _init_git_repo(external)
    _add_remote(external, "origin", "https://github.com/someone/external_python.git")

    assert main(["--json", "registry", "scan"]) == 0
    capsys.readouterr()

    assert (
        main(
            [
                "--json",
                "registry",
                "list",
                "--owner",
                "self",
                "--language",
                "elixir",
                "--relation",
                "primary",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    names = [project["name"] for project in payload["data"]["projects"]]
    assert names == ["owned_elixir"]


def test_registry_resolution_prefers_registered_bare_ref_over_cwd_shadow(
    atlas_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ATLAS_ONCE_SELF_OWNERS", "nshkrdotcom")

    repo = atlas_env / "code" / "stack_lab"
    _make_elixir_repo(repo, extra_files={"lib/stack_lab.ex": "defmodule StackLab do\nend\n"})
    _init_git_repo(repo)
    _add_remote(repo, "origin", "n:nshkrdotcom/stack_lab.git")

    docs_cwd = atlas_env / "docs" / "phase5"
    _write(docs_cwd / "stack_lab" / "README.md", "# wrong shadow\n")

    assert main(["registry", "scan"]) == 0
    capsys.readouterr()

    monkeypatch.chdir(docs_cwd)
    assert main(["--json", "registry", "show", "stack_lab"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["project"]["path"] == str(repo.resolve())

    assert main(["--json", "registry", "show", "./stack_lab"]) == 0
    path_payload = json.loads(capsys.readouterr().out)
    assert path_payload["data"]["project"]["path"] == str((docs_cwd / "stack_lab").resolve())


def test_ranked_v3_supports_root_scoped_selector_groups(
    atlas_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ATLAS_ONCE_SELF_OWNERS", "nshkrdotcom")

    dexterity_root = atlas_env / "dexterity"
    dexterity_root.mkdir()

    repo = atlas_env / "code" / "repo_alpha"
    _make_elixir_repo(
        repo,
        extra_files={"lib/root.ex": "defmodule Root do\nend\n"},
    )
    _init_git_repo(repo)
    _add_remote(repo, "origin", "n:nshkrdotcom/repo_alpha.git")

    _write_ranked_config(
        atlas_env,
        {
            "version": 3,
            "defaults": {
                "registry": {"self_owners": ["nshkrdotcom"]},
                "runtime": {"dexterity_root": str(dexterity_root)},
                "strategies": {
                    "elixir_ranked_v1": {
                        "include_readme": True,
                        "top_files": 1,
                        "overscan_limit": 25,
                    }
                },
            },
            "repos": {},
            "groups": {
                "owned-elixir-all": {
                    "selectors": [
                        {
                            "owner_scope": "self",
                            "primary_language": "elixir",
                            "relation": "primary",
                            "roots": [str(atlas_env / "code")],
                            "variant": "default",
                            "exclude_forks": True,
                        }
                    ]
                }
            },
        },
    )

    calls: list[list[str]] = []

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
        calls.append(cmd)
        if cmd[:2] == ["mix", "dexterity.index"]:
            return subprocess.CompletedProcess(cmd, 0, "ok\n", "")
        if cmd[:2] == ["mix", "dexterity.query"]:
            payload = {"ok": True, "command": "ranked_files", "result": [["lib/root.ex", 0.9]]}
            return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")
        raise AssertionError(f"unexpected dexterity command: {cmd}")

    monkeypatch.setattr("atlas_once.ranked_context.subprocess.run", fake_run)

    assert main(["registry", "scan"]) == 0
    assert main(["context", "ranked", "prepare", "owned-elixir-all"]) == 0

    assert any(cmd[:2] == ["mix", "dexterity.index"] for cmd in calls)
    rendered_path = atlas_env / "config" / "atlas_once" / "cache" / "bundles"
    assert rendered_path.exists() or True


def test_ranked_v3_reuses_prepared_repo_variant_across_group_prepares(
    atlas_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ATLAS_ONCE_SELF_OWNERS", "nshkrdotcom")

    dexterity_root = atlas_env / "dexterity"
    dexterity_root.mkdir()

    repo_alpha = atlas_env / "code" / "repo_alpha"
    _make_elixir_repo(
        repo_alpha,
        extra_files={
            "lib/a.ex": "defmodule A do\nend\n",
            "lib/b.ex": "defmodule B do\nend\n",
        },
    )
    _init_git_repo(repo_alpha)
    _add_remote(repo_alpha, "origin", "n:nshkrdotcom/repo_alpha.git")

    repo_beta = atlas_env / "code" / "repo_beta"
    _make_elixir_repo(
        repo_beta,
        extra_files={"lib/c.ex": "defmodule C do\nend\n"},
    )
    _init_git_repo(repo_beta)
    _add_remote(repo_beta, "origin", "n:nshkrdotcom/repo_beta.git")

    _write_ranked_config(
        atlas_env,
        {
            "version": 3,
            "defaults": {
                "registry": {"self_owners": ["nshkrdotcom"]},
                "runtime": {"dexterity_root": str(dexterity_root)},
                "strategies": {"elixir_ranked_v1": {"include_readme": True, "top_files": 1}},
            },
            "repos": {
                "repo_alpha": {
                    "ref": "repo_alpha",
                    "variants": {"lite": {"top_files": 1}},
                }
            },
            "groups": {
                "group-one": {"items": [{"ref": "repo_alpha", "variant": "lite"}]},
                "group-two": {
                    "items": [
                        {"ref": "repo_alpha", "variant": "lite"},
                        {"ref": "repo_beta", "variant": "default"},
                    ]
                },
            },
        },
    )

    counts = {"index": 0, "query": 0}

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
            counts["index"] += 1
            return subprocess.CompletedProcess(cmd, 0, "ok\n", "")
        if cmd[:2] == ["mix", "dexterity.query"]:
            counts["query"] += 1
            shadow_root = Path(cmd[cmd.index("--repo-root") + 1])
            repo_root = (shadow_root / "mix.exs").resolve().parent
            payload = {
                "ok": True,
                "command": "ranked_files",
                "result": [["lib/a.ex", 0.9]] if repo_root == repo_alpha else [["lib/c.ex", 0.9]],
            }
            return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")
        raise AssertionError(f"unexpected dexterity command: {cmd}")

    monkeypatch.setattr("atlas_once.ranked_context.subprocess.run", fake_run)

    assert main(["registry", "scan"]) == 0
    assert main(["context", "ranked", "prepare", "group-one"]) == 0
    assert main(["context", "ranked", "prepare", "group-two"]) == 0

    assert counts == {"index": 2, "query": 2}


def test_ranked_v3_non_elixir_default_variant_renders_python_sources(
    atlas_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ATLAS_ONCE_SELF_OWNERS", "nshkrdotcom")

    repo = atlas_env / "code" / "snakepit"
    _make_python_repo(
        repo,
        extra_files={
            "src/snakepit/a.py": "def a():\n    return 'a'\n",
            "src/snakepit/b.py": "def b():\n    return 'b'\n",
            "tests/test_a.py": "def test_a():\n    assert True\n",
        },
    )
    _init_git_repo(repo)
    _add_remote(repo, "origin", "n:nshkrdotcom/snakepit.git")

    _write_ranked_config(
        atlas_env,
        {
            "version": 3,
            "defaults": {
                "registry": {"self_owners": ["nshkrdotcom"]},
                "runtime": {"dexterity_root": str(atlas_env / "dexterity")},
                "strategies": {"python_default_v1": {"include_readme": True, "top_files": 2}},
            },
            "repos": {},
            "groups": {"owned-python": {"items": [{"ref": "snakepit", "variant": "default"}]}},
        },
    )

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
        raise AssertionError(f"python default strategy should not shell out: {cmd}")

    monkeypatch.setattr("atlas_once.ranked_context.subprocess.run", fail_run)

    assert main(["registry", "scan"]) == 0
    capsys.readouterr()
    assert main(["context", "ranked", "prepare", "owned-python"]) == 0
    capsys.readouterr()
    assert main(["context", "ranked", "owned-python"]) == 0
    rendered = capsys.readouterr().out

    assert "# FILE: ./snakepit/README.md" in rendered
    assert "# FILE: ./snakepit/src/snakepit/a.py" in rendered
    assert "# FILE: ./snakepit/src/snakepit/b.py" in rendered
    assert "tests/test_a.py" not in rendered


def test_config_ranked_install_seeds_v3_repo_variant_template(
    atlas_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ranked_path = atlas_home / ".config" / "atlas_once" / "ranked_contexts.json"

    assert main(["--json", "config", "ranked", "install", "--profile", "nshkrdotcom"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["ranked_contexts"]["status"] == "installed"

    config = json.loads(ranked_path.read_text(encoding="utf-8"))
    assert config["version"] == 3
    assert "groups" in config
    assert "repos" in config
    assert "owned-elixir-all" in config["groups"]
    assert config["groups"]["owned-elixir-all"]["selectors"][0]["roots"] == ["~/p/g/n"]
