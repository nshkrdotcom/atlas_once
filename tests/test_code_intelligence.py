from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from atlas_once.atlas import main


def _write_ranked_runtime(atlas_env: Path) -> None:
    config_path = atlas_env / "config" / "atlas_once" / "ranked_contexts.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "version": 3,
                "defaults": {
                    "registry": {"self_owners": []},
                    "runtime": {
                        "dexterity_root": str(atlas_env / "dexterity"),
                        "dexter_bin": str(atlas_env / "bin" / "dexter"),
                        "shadow_root": str(atlas_env / "state" / "shadows"),
                    },
                    "strategies": {},
                },
                "repos": {},
                "groups": {},
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (atlas_env / "dexterity").mkdir()
    (atlas_env / "bin").mkdir()


def _make_mix_repo(root: Path) -> None:
    (root / ".git").mkdir(parents=True)
    (root / "lib" / "demo").mkdir(parents=True)
    (root / "mix.exs").write_text("defmodule Demo.MixProject do\nend\n", encoding="utf-8")
    (root / "lib" / "demo" / "agent.ex").write_text(
        "defmodule Demo.Agent do\n  def run, do: :ok\nend\n",
        encoding="utf-8",
    )


def test_symbols_default_to_current_repo_and_shadow_index(
    atlas_env: Path,
    capsys,
    monkeypatch,
) -> None:
    _write_ranked_runtime(atlas_env)
    repo = atlas_env / "code" / "demo"
    _make_mix_repo(repo)
    monkeypatch.chdir(repo)
    calls: list[list[str]] = []

    def fake_run(
        cmd: list[str],
        *,
        cwd: str | None = None,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
        env: dict[str, str] | None = None,
        **_: Any,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, capture_output, text, check, env
        calls.append(cmd)
        assert str(repo) not in cmd
        if cmd[:2] == ["mix", "dexterity.index"]:
            shadow = Path(cmd[cmd.index("--repo-root") + 1])
            (shadow / ".dexter.db").write_text("shadow only\n", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, "index refreshed\n", "")
        assert cmd[:3] == ["mix", "dexterity.query", "symbols"]
        return subprocess.CompletedProcess(
            cmd,
            0,
            json.dumps(
                {
                    "ok": True,
                    "result": [
                        {
                            "file": f"{cmd[cmd.index('--repo-root') + 1]}/lib/demo/agent.ex",
                            "module": "Demo.Agent",
                            "function": "run",
                            "arity": 0,
                        }
                    ],
                }
            ),
            "",
        )

    monkeypatch.setattr("atlas_once.code_intelligence.subprocess.run", fake_run)

    assert main(["--json", "symbols", "Agent"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["command"] == "symbols"
    assert payload["data"]["project"]["repo_root"] == str(repo.resolve())
    expected_shadow_base = str(atlas_env / "state" / "shadows")
    assert payload["data"]["project"]["shadow_root"].startswith(expected_shadow_base)
    assert payload["data"]["result"][0]["file"] == str(repo / "lib" / "demo" / "agent.ex")
    assert not (repo / ".dexter.db").exists()
    assert not (repo / ".dexterity").exists()
    assert [cmd[1] for cmd in calls] == ["dexterity.index", "dexterity.query"]


def test_index_without_subcommand_indexes_current_mix_repo(
    atlas_env: Path,
    capsys,
    monkeypatch,
) -> None:
    _write_ranked_runtime(atlas_env)
    repo = atlas_env / "code" / "demo"
    _make_mix_repo(repo)
    monkeypatch.chdir(repo)

    def fake_run(
        cmd: list[str],
        **_: Any,
    ) -> subprocess.CompletedProcess[str]:
        assert cmd[:2] == ["mix", "dexterity.index"]
        assert str(repo) not in cmd
        return subprocess.CompletedProcess(cmd, 0, "index refreshed\n", "")

    monkeypatch.setattr("atlas_once.code_intelligence.subprocess.run", fake_run)

    assert main(["--json", "index"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["command"] == "index.here"
    assert payload["data"]["project"]["repo_root"] == str(repo.resolve())
    assert payload["data"]["tool"]["returncode"] == 0
    assert not (repo / ".dexter.db").exists()


def test_raw_dexter_lookup_uses_shadow_cwd_and_maps_paths(
    atlas_env: Path,
    capsys,
    monkeypatch,
) -> None:
    _write_ranked_runtime(atlas_env)
    repo = atlas_env / "code" / "demo"
    _make_mix_repo(repo)
    monkeypatch.chdir(repo)
    seen_cwds: list[str | None] = []

    def fake_run(
        cmd: list[str],
        *,
        cwd: str | None = None,
        **_: Any,
    ) -> subprocess.CompletedProcess[str]:
        seen_cwds.append(cwd)
        if cmd[:2] == ["mix", "dexterity.index"]:
            return subprocess.CompletedProcess(cmd, 0, "index refreshed\n", "")
        assert cmd[1:3] == ["lookup", "Demo.Agent"]
        shadow_cwd = cwd or ""
        return subprocess.CompletedProcess(
            cmd,
            0,
            f"{shadow_cwd}/lib/demo/agent.ex:1:defmodule Demo.Agent\n",
            "",
        )

    monkeypatch.setattr("atlas_once.code_intelligence.subprocess.run", fake_run)

    assert main(["--json", "dexter", "lookup", "Demo.Agent"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["command"] == "dexter.lookup"
    assert payload["data"]["stdout"].startswith(str(repo / "lib" / "demo" / "agent.ex"))
    assert seen_cwds[-1] == payload["data"]["project"]["shadow_root"]
    assert not (repo / ".dexter.db").exists()


def test_def_module_uses_raw_dexter_lookup(
    atlas_env: Path,
    capsys,
    monkeypatch,
) -> None:
    _write_ranked_runtime(atlas_env)
    repo = atlas_env / "code" / "demo"
    _make_mix_repo(repo)
    monkeypatch.chdir(repo)
    calls: list[list[str]] = []

    def fake_run(
        cmd: list[str],
        *,
        cwd: str | None = None,
        **_: Any,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[:2] == ["mix", "dexterity.index"]:
            return subprocess.CompletedProcess(cmd, 0, "index refreshed\n", "")
        assert cmd[1:3] == ["lookup", "Demo.Agent"]
        assert cwd is not None
        return subprocess.CompletedProcess(
            cmd,
            0,
            f"{cwd}/lib/demo/agent.ex:1:defmodule Demo.Agent\n",
            "",
        )

    monkeypatch.setattr("atlas_once.code_intelligence.subprocess.run", fake_run)

    assert main(["--json", "def", "Demo.Agent"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["command"] == "def"
    assert payload["data"]["stdout"].startswith(str(repo / "lib" / "demo" / "agent.ex"))
    assert [cmd[1] for cmd in calls] == ["dexterity.index", "lookup"]


def test_repo_map_does_not_pass_dexter_bin_to_mix_task(
    atlas_env: Path,
    capsys,
    monkeypatch,
) -> None:
    _write_ranked_runtime(atlas_env)
    repo = atlas_env / "code" / "demo"
    _make_mix_repo(repo)
    monkeypatch.chdir(repo)
    map_commands: list[list[str]] = []

    def fake_run(
        cmd: list[str],
        **_: Any,
    ) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["mix", "dexterity.index"]:
            return subprocess.CompletedProcess(cmd, 0, "index refreshed\n", "")
        assert cmd[:2] == ["mix", "dexterity.map"]
        map_commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "repo map\n", "")

    monkeypatch.setattr("atlas_once.code_intelligence.subprocess.run", fake_run)

    assert main(["--json", "repo-map", "--active", "lib/demo/agent.ex"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["command"] == "repo-map"
    assert payload["data"]["result"] == "repo map\n"
    assert "--dexter-bin" not in map_commands[0]
