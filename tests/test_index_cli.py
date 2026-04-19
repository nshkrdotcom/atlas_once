from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from atlas_once.atlas import _index_main, main
from atlas_once.config import get_paths
from atlas_once.index_watcher import IndexWatcherState, save_state


def _write_ranked_runtime(atlas_env: Path) -> None:
    config_path = atlas_env / "config" / "atlas_once" / "ranked_contexts.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "version": 3,
                "defaults": {
                    "registry": {"self_owners": []},
                    "runtime": {"dexterity_root": str(atlas_env / "dexterity")},
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


def _make_repo(atlas_env: Path) -> Path:
    repo = atlas_env / "code" / "demo"
    (repo / ".git").mkdir(parents=True)
    (repo / "lib").mkdir()
    (repo / "mix.exs").write_text("defmodule Demo.MixProject do\nend\n", encoding="utf-8")
    (repo / "lib" / "demo.ex").write_text("defmodule Demo do\nend\n", encoding="utf-8")
    return repo


def test_index_status_watch_refresh_and_stop_json(
    atlas_env: Path,
    capsys,
    monkeypatch,
) -> None:
    _write_ranked_runtime(atlas_env)
    _make_repo(atlas_env)

    assert main(["--json", "registry", "scan"]) == 0
    capsys.readouterr()

    calls: list[Path] = []

    def fake_run_index(
        project_root: Path,
        *,
        dexterity_root: Path,
        shadow_root: Path,
        dexter_bin: str = "dexter",
    ) -> subprocess.CompletedProcess[str]:
        del dexterity_root, shadow_root, dexter_bin
        calls.append(project_root)
        return subprocess.CompletedProcess(["mix", "dexterity.index"], 0, "ok\n", "")

    monkeypatch.setattr("atlas_once.index_watcher.run_index", fake_run_index)

    assert main(["--json", "index", "status"]) == 0
    status_payload = json.loads(capsys.readouterr().out)
    assert status_payload["command"] == "index.status"
    assert status_payload["data"]["daemon"]["running"] is False
    assert status_payload["data"]["summary"]["projects_total"] == 1

    assert main(["--json", "index", "watch", "--once", "--debounce-ms", "0"]) == 0
    watch_payload = json.loads(capsys.readouterr().out)
    assert watch_payload["command"] == "index.watch"
    assert watch_payload["data"]["watcher"]["once"] is True
    assert len(calls) == 1

    assert main(["--json", "index", "refresh", "--project", "demo"]) == 0
    refresh_payload = json.loads(capsys.readouterr().out)
    assert refresh_payload["command"] == "index.refresh"
    assert refresh_payload["data"]["status"]["summary"]["projects_total"] == 1
    assert len(calls) == 2

    assert main(["--json", "index", "stop", "--force"]) == 0
    stop_payload = json.loads(capsys.readouterr().out)
    assert stop_payload["command"] == "index.stop"
    assert stop_payload["data"]["force"] is True


def test_index_start_launches_background_watcher(
    atlas_env: Path,
    capsys,
    monkeypatch,
) -> None:
    _write_ranked_runtime(atlas_env)
    _make_repo(atlas_env)

    assert main(["--json", "registry", "scan"]) == 0
    capsys.readouterr()

    launched: list[list[str]] = []

    class FakeProcess:
        pid = 4321

        def poll(self) -> int | None:
            return None

    def fake_popen(command: list[str], **_: Any) -> FakeProcess:
        launched.append(command)
        save_state(get_paths(), IndexWatcherState(running=True, pid=4321))
        return FakeProcess()

    monkeypatch.setattr("atlas_once.atlas.subprocess.Popen", fake_popen)
    monkeypatch.setattr("atlas_once.index_watcher._is_alive", lambda pid: pid == 4321)

    assert main(["--json", "index", "start"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["command"] == "index.start"
    assert payload["data"]["watcher"]["running"] is True
    assert payload["data"]["watcher"]["pid"] == 4321
    assert payload["data"]["watcher"]["already_running"] is False
    assert payload["data"]["targets"][0]["project_ref"] == "demo"
    assert launched
    assert launched[0][1:4] == ["index", "watch", "--daemon"]


def test_index_help_includes_new_subcommands(atlas_env: Path, capsys) -> None:
    del atlas_env
    with pytest.raises(SystemExit) as exc_info:
        _index_main(["--help"], False)
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "start" in out
    assert "watch" in out
    assert "status" in out
    assert "refresh" in out
    assert "stop" in out
