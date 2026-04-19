from __future__ import annotations

import json
import signal
import subprocess
from pathlib import Path

from atlas_once.config import get_paths
from atlas_once.index_watcher import (
    IndexProjectState,
    IndexWatcherState,
    load_state,
    make_watch_target,
    refresh_projects,
    save_state,
    start_watch,
    stop_watch,
)


def _make_project(root: Path) -> None:
    (root / "lib").mkdir(parents=True)
    (root / "mix.exs").write_text("defmodule Demo.MixProject do\nend\n", encoding="utf-8")
    (root / "lib" / "demo.ex").write_text("defmodule Demo do\nend\n", encoding="utf-8")


def test_state_roundtrip(atlas_env: Path) -> None:
    paths = get_paths()
    project = atlas_env / "code" / "demo"
    _make_project(project)
    target = make_watch_target(project, project_ref="demo")
    state = IndexWatcherState(running=True, pid=123, projects={})
    state.projects[target.project_key] = IndexProjectState(
        project_key=target.project_key,
        project_ref="demo",
        project_path=str(project),
        status="stale",
        queued=True,
        queue_depth=2,
        last_file_mtime=100.0,
    )

    save_state(paths, state)
    loaded, recovered = load_state(paths)

    assert recovered is False
    assert loaded.projects[target.project_key].project_ref == "demo"
    assert loaded.projects[target.project_key].queued is True
    assert loaded.projects[target.project_key].queue_depth == 2


def test_coalesce_burst_events(atlas_env: Path, monkeypatch) -> None:
    paths = get_paths()
    project = atlas_env / "code" / "demo"
    _make_project(project)
    target = make_watch_target(project, project_ref="demo")
    calls = 0

    def fake_run_index(
        project_root: Path,
        *,
        dexterity_root: Path,
        shadow_root: Path,
        dexter_bin: str = "dexter",
    ) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        del project_root, dexterity_root, shadow_root, dexter_bin
        calls += 1
        return subprocess.CompletedProcess(["mix", "dexterity.index"], 0, "ok\n", "")

    monkeypatch.setattr("atlas_once.index_watcher.run_index", fake_run_index)

    start_watch(
        paths,
        [target],
        dexterity_root=atlas_env / "dexterity",
        dexter_bin="dexter",
        shadow_root=paths.state_home / "code" / "shadows",
        debounce_ms=0,
        poll_interval_ms=0,
        once=True,
    )

    loaded, _ = load_state(paths)
    assert calls == 1
    assert loaded.projects[target.project_key].status == "fresh"
    assert loaded.projects[target.project_key].queue_depth == 0


def test_one_in_flight_guard(atlas_env: Path, monkeypatch) -> None:
    paths = get_paths()
    project = atlas_env / "code" / "demo"
    _make_project(project)
    target = make_watch_target(project, project_ref="demo")
    state = IndexWatcherState(projects={})
    state.projects[target.project_key] = IndexProjectState(
        project_key=target.project_key,
        project_ref="demo",
        project_path=str(project),
        in_flight=True,
        queued=True,
        queue_due_at=0.0,
    )
    save_state(paths, state)

    def fail_run_index(
        project_root: Path,
        *,
        dexterity_root: Path,
        shadow_root: Path,
        dexter_bin: str = "dexter",
    ) -> subprocess.CompletedProcess[str]:
        del project_root, dexterity_root, shadow_root, dexter_bin
        raise AssertionError("in-flight projects must not start another index")

    monkeypatch.setattr("atlas_once.index_watcher.run_index", fail_run_index)

    refreshed = refresh_projects(
        paths,
        [target],
        dexterity_root=atlas_env / "dexterity",
        dexter_bin="dexter",
        shadow_root=paths.state_home / "code" / "shadows",
    )

    assert refreshed.projects[target.project_key].in_flight is True


def test_stale_lock_recovery(atlas_env: Path) -> None:
    paths = get_paths()
    state = IndexWatcherState(running=True, pid=999_999_999)
    save_state(paths, state)

    loaded, _ = load_state(paths)
    event_lines = paths.events_path.read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in event_lines]

    assert loaded.running is False
    assert loaded.pid is None
    assert events[-1]["command"] == "index.state_recovered"


def test_permission_and_retry_path(atlas_env: Path, monkeypatch) -> None:
    paths = get_paths()
    project = atlas_env / "code" / "demo"
    _make_project(project)
    target = make_watch_target(project, project_ref="demo")

    def failed_run_index(
        project_root: Path,
        *,
        dexterity_root: Path,
        shadow_root: Path,
        dexter_bin: str = "dexter",
    ) -> subprocess.CompletedProcess[str]:
        del project_root, dexterity_root, shadow_root, dexter_bin
        return subprocess.CompletedProcess(
            ["mix", "dexterity.index"],
            1,
            "",
            "permission denied",
        )

    monkeypatch.setattr("atlas_once.index_watcher.run_index", failed_run_index)

    state = refresh_projects(
        paths,
        [target],
        dexterity_root=atlas_env / "dexterity",
        dexter_bin="dexter",
        shadow_root=paths.state_home / "code" / "shadows",
    )
    loaded, _ = load_state(paths)

    assert state.projects[target.project_key].status == "error"
    assert state.projects[target.project_key].last_error == "permission denied"
    assert loaded.projects[target.project_key].retries == 1


def test_active_daemon_guard_blocks_duplicate_start(atlas_env: Path, monkeypatch) -> None:
    paths = get_paths()
    project = atlas_env / "code" / "demo"
    _make_project(project)
    target = make_watch_target(project, project_ref="demo")
    state = IndexWatcherState(running=True, pid=12345)
    save_state(paths, state)

    monkeypatch.setattr("atlas_once.index_watcher._is_alive", lambda pid: pid == 12345)

    def fail_run_index(
        project_root: Path,
        *,
        dexterity_root: Path,
        shadow_root: Path,
        dexter_bin: str = "dexter",
    ) -> subprocess.CompletedProcess[str]:
        del project_root, dexterity_root, shadow_root, dexter_bin
        raise AssertionError("duplicate daemon start should not run indexing")

    monkeypatch.setattr("atlas_once.index_watcher.run_index", fail_run_index)

    returned = start_watch(
        paths,
        [target],
        dexterity_root=atlas_env / "dexterity",
        dexter_bin="dexter",
        shadow_root=paths.state_home / "code" / "shadows",
        daemon=True,
    )

    assert returned.pid == 12345
    assert returned.running is True


def test_stop_reports_unstopped_when_process_survives(atlas_env: Path, monkeypatch) -> None:
    paths = get_paths()
    state = IndexWatcherState(running=True, pid=12345)
    save_state(paths, state)
    signals: list[tuple[int, int]] = []

    monkeypatch.setattr("atlas_once.index_watcher.DEFAULT_STOP_WAIT_SECONDS", 0.0)
    monkeypatch.setattr("atlas_once.index_watcher._is_alive", lambda pid: pid == 12345)

    def fake_kill(pid: int, sig: int) -> bool:
        signals.append((pid, sig))
        return True

    monkeypatch.setattr("atlas_once.index_watcher._send_signal", fake_kill)

    result = stop_watch(paths)

    assert signals == [(12345, signal.SIGTERM), (12345, signal.SIGKILL)]
    assert result["signal_sent"] is True
    assert result["force_escalated"] is True
    assert result["stopped"] is False
    assert result["running"] is True


def test_stop_clears_state_after_process_exits(atlas_env: Path, monkeypatch) -> None:
    paths = get_paths()
    state = IndexWatcherState(running=True, pid=12345)
    save_state(paths, state)
    alive_calls = 0

    def fake_is_alive(pid: int) -> bool:
        nonlocal alive_calls
        if pid != 12345:
            return False
        alive_calls += 1
        return alive_calls <= 2

    monkeypatch.setattr("atlas_once.index_watcher._is_alive", fake_is_alive)
    monkeypatch.setattr("atlas_once.index_watcher._send_signal", lambda pid, sig: True)

    result = stop_watch(paths)
    loaded, _ = load_state(paths)

    assert result["signal_sent"] is True
    assert result["force_escalated"] is False
    assert result["stopped"] is True
    assert result["running"] is False
    assert loaded.running is False
    assert loaded.pid is None
