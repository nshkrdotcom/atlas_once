from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from atlas_once.atlas import main
from atlas_once.config import get_paths
from atlas_once.index_watcher import (
    IndexProjectState,
    IndexWatcherState,
    make_watch_target,
    save_state,
)
from atlas_once.shadow_workspace import shadow_root_for_project


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


def _make_citadel_like_repo(root: Path) -> None:
    (root / ".git").mkdir(parents=True)
    (root / "lib" / "citadel" / "build").mkdir(parents=True)
    (root / "core" / "contract_core").mkdir(parents=True)
    (root / "bridges" / "query_bridge").mkdir(parents=True)
    (root / "apps" / "coding_assist").mkdir(parents=True)
    (root / "mix.exs").write_text("defmodule Citadel.MixProject do\nend\n", encoding="utf-8")
    (root / "core" / "contract_core" / "mix.exs").write_text(
        "defmodule ContractCore.MixProject do\n  def project, do: [app: :contract_core]\nend\n",
        encoding="utf-8",
    )
    (root / "bridges" / "query_bridge" / "mix.exs").write_text(
        "defmodule QueryBridge.MixProject do\n  def project, do: [app: :query_bridge]\nend\n",
        encoding="utf-8",
    )
    (root / "apps" / "coding_assist" / "mix.exs").write_text(
        "defmodule CodingAssist.MixProject do\n  def project, do: [app: :coding_assist]\nend\n",
        encoding="utf-8",
    )
    (root / "lib" / "citadel" / "workspace.ex").write_text(
        "defmodule Citadel.Workspace do\n  def root, do: :ok\nend\n",
        encoding="utf-8",
    )
    (root / "lib" / "citadel" / "build" / "dependency_resolver.ex").write_text(
        "defmodule Citadel.Build.DependencyResolver do\nend\n",
        encoding="utf-8",
    )


def _source_mtime(root: Path) -> float:
    return max(
        path.stat().st_mtime
        for path in [
            root / "mix.exs",
            root / "lib" / "demo" / "agent.ex",
        ]
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


def test_agent_status_defaults_to_current_mix_repo(
    atlas_env: Path,
    capsys,
    monkeypatch,
) -> None:
    _write_ranked_runtime(atlas_env)
    repo = atlas_env / "code" / "demo"
    _make_mix_repo(repo)
    monkeypatch.chdir(repo)

    assert main(["--json", "agent", "status"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["command"] == "agent.status"
    assert payload["data"]["project"]["project_ref"] == "demo"
    assert payload["data"]["project"]["project_path"] == str(repo.resolve())
    assert payload["data"]["freshness"]["source_dirty"] is True
    assert payload["data"]["commands"]["task"] == 'atlas agent task "<goal>"'


def test_agent_find_wraps_symbol_search_with_short_command(
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
        **_: Any,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[:2] == ["mix", "dexterity.index"]:
            return subprocess.CompletedProcess(cmd, 0, "index refreshed\n", "")
        assert cmd[:3] == ["mix", "dexterity.query", "symbols"]
        assert "Agent" in cmd
        assert "--limit" in cmd
        return subprocess.CompletedProcess(
            cmd,
            0,
            json.dumps({"ok": True, "result": [{"file": "lib/demo/agent.ex"}]}),
            "",
        )

    monkeypatch.setattr("atlas_once.code_intelligence.subprocess.run", fake_run)

    assert main(["--json", "agent", "find", "Agent"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["command"] == "agent.find"
    assert payload["data"]["query"] == "Agent"
    assert payload["data"]["result"] == [{"file": "lib/demo/agent.ex"}]
    assert payload["data"]["agent"]["next_commands"][0] == "atlas agent def <Module>"
    assert [cmd[1] for cmd in calls] == ["dexterity.index", "dexterity.query"]


def test_symbols_prioritize_library_results_and_include_groups(
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
        if cmd[:2] == ["mix", "dexterity.index"]:
            return subprocess.CompletedProcess(cmd, 0, "index refreshed\n", "")
        assert cmd[:3] == ["mix", "dexterity.query", "symbols"]
        return subprocess.CompletedProcess(
            cmd,
            0,
            json.dumps(
                {
                    "ok": True,
                    "result": [
                        {"file": "examples/email/lib/email/agent.ex", "module": "Email.Agent"},
                        {"file": "test/demo/agent_test.exs", "module": "Demo.AgentTest"},
                        {"file": "lib/demo/agent.ex", "module": "Demo.Agent"},
                    ],
                }
            ),
            "",
        )

    monkeypatch.setattr("atlas_once.code_intelligence.subprocess.run", fake_run)

    assert main(["--json", "symbols", "Agent"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["data"]["result"][0]["file"] == "lib/demo/agent.ex"
    assert payload["data"]["result_groups"]["implementation"][0]["module"] == "Demo.Agent"
    assert payload["data"]["result_groups"]["examples"][0]["module"] == "Email.Agent"
    assert payload["data"]["result_groups"]["tests"][0]["module"] == "Demo.AgentTest"


def test_agent_task_builds_compact_context_without_repo_map(
    atlas_env: Path,
    capsys,
    monkeypatch,
) -> None:
    _write_ranked_runtime(atlas_env)
    repo = atlas_env / "code" / "demo"
    _make_mix_repo(repo)
    monkeypatch.chdir(repo)
    query_actions: list[str] = []

    def fake_run(
        cmd: list[str],
        **_: Any,
    ) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["mix", "dexterity.index"]:
            return subprocess.CompletedProcess(cmd, 0, "index refreshed\n", "")
        assert cmd[:2] != ["mix", "dexterity.map"]
        assert cmd[:2] == ["mix", "dexterity.query"]
        query_actions.append(cmd[2])
        if cmd[2] == "symbols":
            return subprocess.CompletedProcess(
                cmd,
                0,
                json.dumps(
                    {
                        "ok": True,
                        "result": [
                            {"file": "lib/demo/agent.ex", "module": "Demo.Agent"},
                        ],
                    }
                ),
                "",
            )
        if cmd[2] == "ranked_files":
            assert "--active-file" in cmd
            assert "lib/demo/agent.ex" in cmd
            return subprocess.CompletedProcess(
                cmd,
                0,
                json.dumps({"ok": True, "result": [["lib/demo/agent.ex", 0.9]]}),
                "",
            )
        raise AssertionError(f"unexpected query action: {cmd[2]}")

    monkeypatch.setattr("atlas_once.code_intelligence.subprocess.run", fake_run)

    assert main(["--json", "agent", "task", "add", "new", "agentic", "functionality"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["command"] == "agent.task"
    assert payload["data"]["terms"] == ["Agent"]
    assert payload["data"]["seed_files"] == ["lib/demo/agent.ex"]
    assert payload["data"]["ranked_files"]["result"] == [["lib/demo/agent.ex", 0.9]]
    assert payload["data"]["symbol_searches"][0]["result"][0]["module"] == "Demo.Agent"
    assert "atlas agent refs Demo.Agent" in payload["data"]["next_commands"]
    assert query_actions == ["symbols", "ranked_files"]


def test_agent_task_returns_repo_structure_without_backend_for_broad_architecture_goal(
    atlas_env: Path,
    capsys,
    monkeypatch,
) -> None:
    _write_ranked_runtime(atlas_env)
    repo = atlas_env / "code" / "citadel"
    _make_citadel_like_repo(repo)
    monkeypatch.chdir(repo)

    def fail_backend(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        raise AssertionError(f"backend should not be needed for broad structure task: {cmd}")

    monkeypatch.setattr("atlas_once.code_intelligence.subprocess.run", fail_backend)

    assert (
        main(
            [
                "--json",
                "agent",
                "task",
                "understand",
                "repository",
                "architecture",
                "key",
                "modules",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)

    structure = payload["data"]["repo_structure"]
    assert structure["multi_mix"] is True
    assert structure["layer_counts"]["core"] == 1
    assert structure["layer_counts"]["bridges"] == 1
    assert structure["layer_counts"]["apps"] == 1
    assert "lib/citadel/workspace.ex" in payload["data"]["likely_files"]
    assert payload["data"]["backend_errors"] == []


def test_agent_task_returns_partial_context_when_symbol_query_times_out(
    atlas_env: Path,
    capsys,
    monkeypatch,
) -> None:
    _write_ranked_runtime(atlas_env)
    repo = atlas_env / "code" / "demo"
    _make_mix_repo(repo)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("ATLAS_ONCE_AGENT_QUERY_TIMEOUT_SECONDS", "0.01")

    def fake_run(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["mix", "dexterity.index"]:
            return subprocess.CompletedProcess(cmd, 0, "index refreshed\n", "")
        raise subprocess.TimeoutExpired(cmd, 0.01)

    monkeypatch.setattr("atlas_once.code_intelligence.subprocess.run", fake_run)

    assert main(["--json", "agent", "task", "add", "agent", "behavior"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["data"]["repo_structure"]["mix_project_count"] == 1
    assert payload["data"]["backend_errors"][0]["stage"] == "symbols:Agent"
    assert payload["data"]["backend_errors"][0]["kind"] == "dexterity_query_failed_timeout"
    assert "lib/demo/agent.ex" in payload["data"]["likely_files"]


def test_agent_find_times_out_with_explicit_error_kind(
    atlas_env: Path,
    capsys,
    monkeypatch,
) -> None:
    _write_ranked_runtime(atlas_env)
    repo = atlas_env / "code" / "demo"
    _make_mix_repo(repo)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("ATLAS_ONCE_AGENT_QUERY_TIMEOUT_SECONDS", "0.01")

    def fake_run(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["mix", "dexterity.index"]:
            return subprocess.CompletedProcess(cmd, 0, "index refreshed\n", "")
        raise subprocess.TimeoutExpired(cmd, 0.01)

    monkeypatch.setattr("atlas_once.code_intelligence.subprocess.run", fake_run)

    assert main(["--json", "agent", "find", "Agent"]) == 7
    payload = json.loads(capsys.readouterr().out)

    assert payload["errors"][0]["kind"] == "dexterity_query_failed_timeout"
    assert payload["errors"][0]["details"]["timed_out"] is True


def test_agent_find_invalid_json_is_explicit(
    atlas_env: Path,
    capsys,
    monkeypatch,
) -> None:
    _write_ranked_runtime(atlas_env)
    repo = atlas_env / "code" / "demo"
    _make_mix_repo(repo)
    monkeypatch.chdir(repo)

    def fake_run(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["mix", "dexterity.index"]:
            return subprocess.CompletedProcess(cmd, 0, "index refreshed\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("atlas_once.code_intelligence.subprocess.run", fake_run)

    assert main(["--json", "agent", "find", "Agent"]) == 7
    payload = json.loads(capsys.readouterr().out)

    assert payload["errors"][0]["kind"] == "invalid_dexterity_json"


def test_refs_include_grouped_results(
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
        if cmd[:2] == ["mix", "dexterity.index"]:
            return subprocess.CompletedProcess(cmd, 0, "index refreshed\n", "")
        assert cmd[:3] == ["mix", "dexterity.query", "references"]
        return subprocess.CompletedProcess(
            cmd,
            0,
            json.dumps(
                {
                    "ok": True,
                    "result": [
                        "test/demo/agent_test.exs:12",
                        "examples/email/lib/email/agent.ex:3",
                        "lib/demo/options.ex:9",
                        "README.md:44",
                        "test/support/fixtures.ex:5",
                    ],
                }
            ),
            "",
        )

    monkeypatch.setattr("atlas_once.code_intelligence.subprocess.run", fake_run)

    assert main(["--json", "refs", "Demo.Agent"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["data"]["result_groups"]["implementation"] == ["lib/demo/options.ex:9"]
    assert payload["data"]["result_groups"]["tests"] == ["test/demo/agent_test.exs:12"]
    assert payload["data"]["result_groups"]["examples"] == [
        "examples/email/lib/email/agent.ex:3"
    ]
    assert payload["data"]["result_groups"]["docs"] == ["README.md:44"]
    assert payload["data"]["result_groups"]["support"] == ["test/support/fixtures.ex:5"]


def test_symbols_skip_index_when_watcher_state_is_fresh(
    atlas_env: Path,
    capsys,
    monkeypatch,
) -> None:
    _write_ranked_runtime(atlas_env)
    repo = atlas_env / "code" / "demo"
    _make_mix_repo(repo)
    monkeypatch.chdir(repo)
    paths = get_paths()
    target = make_watch_target(repo.resolve(), project_ref=repo.name)
    shadow = shadow_root_for_project(repo.resolve(), atlas_env / "state" / "shadows")
    (shadow / "lib").mkdir(parents=True)
    (shadow / "mix.exs").write_text("shadow exists\n", encoding="utf-8")
    source_mtime = _source_mtime(repo)
    save_state(
        paths,
        IndexWatcherState(
            projects={
                target.project_key: IndexProjectState(
                    project_key=target.project_key,
                    project_ref=target.project_ref,
                    project_path=str(target.project_path),
                    last_file_mtime=source_mtime,
                    indexed_file_mtime=source_mtime,
                    last_refresh_finished_at=1.0,
                )
            }
        ),
    )
    calls: list[list[str]] = []

    def fake_run(
        cmd: list[str],
        **_: Any,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        assert cmd[:3] == ["mix", "dexterity.query", "symbols"]
        return subprocess.CompletedProcess(
            cmd,
            0,
            json.dumps({"ok": True, "result": [{"file": "lib/demo/agent.ex"}]}),
            "",
        )

    monkeypatch.setattr("atlas_once.code_intelligence.subprocess.run", fake_run)

    assert main(["--json", "symbols", "Agent"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert [cmd[1] for cmd in calls] == ["dexterity.query"]
    assert payload["data"]["index"]["skipped"] is True
    assert payload["data"]["index"]["freshness"]["status"] == "fresh"


def test_query_retries_transient_database_busy(
    atlas_env: Path,
    capsys,
    monkeypatch,
) -> None:
    _write_ranked_runtime(atlas_env)
    repo = atlas_env / "code" / "demo"
    _make_mix_repo(repo)
    monkeypatch.chdir(repo)
    query_attempts = 0

    def fake_run(
        cmd: list[str],
        **_: Any,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal query_attempts
        if cmd[:2] == ["mix", "dexterity.index"]:
            return subprocess.CompletedProcess(cmd, 0, "index refreshed\n", "")
        assert cmd[:3] == ["mix", "dexterity.query", "symbols"]
        query_attempts += 1
        if query_attempts == 1:
            return subprocess.CompletedProcess(cmd, 1, "", "Database busy\n")
        return subprocess.CompletedProcess(
            cmd,
            0,
            json.dumps({"ok": True, "result": [{"file": "lib/demo/agent.ex"}]}),
            "",
        )

    monkeypatch.setattr("atlas_once.code_intelligence.subprocess.run", fake_run)

    assert main(["--json", "symbols", "Agent"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert query_attempts == 2
    assert payload["data"]["tool"]["attempts"] == 2
    assert payload["data"]["result"] == [{"file": "lib/demo/agent.ex"}]


def test_query_uses_intelligence_service_when_available(
    atlas_env: Path,
    capsys,
    monkeypatch,
) -> None:
    _write_ranked_runtime(atlas_env)
    repo = atlas_env / "code" / "demo"
    _make_mix_repo(repo)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("ATLAS_ONCE_INTELLIGENCE_CACHE", "0")
    calls: list[list[str]] = []
    service_calls: list[dict[str, Any]] = []

    def fake_run(
        cmd: list[str],
        **_: Any,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[:2] == ["mix", "dexterity.index"]:
            return subprocess.CompletedProcess(cmd, 0, "index refreshed\n", "")
        raise AssertionError("query should use intelligence service when available")

    def fake_service_call(
        *,
        paths: Any,
        target: Any,
        tool: str,
        arguments: dict[str, Any],
        **_: Any,
    ) -> Any:
        del paths, target
        service_calls.append({"tool": tool, "arguments": arguments})
        return {
            "transport": "mcp_service",
            "worker": {"key": "demo", "pid": 123, "started": True, "reused": False},
            "result": {"result": [{"file": "lib/demo/agent.ex"}]},
        }

    monkeypatch.setattr("atlas_once.code_intelligence.subprocess.run", fake_run)
    monkeypatch.setattr("atlas_once.code_intelligence.call_intelligence_service", fake_service_call)

    assert main(["--json", "symbols", "Agent"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert [cmd[1] for cmd in calls] == ["dexterity.index"]
    assert service_calls == [{"tool": "find_symbols", "arguments": {"query": "Agent"}}]
    assert payload["data"]["tool"]["transport"] == "mcp_service"
    assert payload["data"]["tool"]["service"]["worker"]["pid"] == 123


def test_query_falls_back_when_intelligence_service_is_unavailable(
    atlas_env: Path,
    capsys,
    monkeypatch,
) -> None:
    _write_ranked_runtime(atlas_env)
    repo = atlas_env / "code" / "demo"
    _make_mix_repo(repo)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("ATLAS_ONCE_INTELLIGENCE_CACHE", "0")
    calls: list[list[str]] = []

    def fake_run(
        cmd: list[str],
        **_: Any,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[:2] == ["mix", "dexterity.index"]:
            return subprocess.CompletedProcess(cmd, 0, "index refreshed\n", "")
        assert cmd[:3] == ["mix", "dexterity.query", "symbols"]
        return subprocess.CompletedProcess(
            cmd,
            0,
            json.dumps({"ok": True, "result": [{"file": "lib/demo/agent.ex"}]}),
            "",
        )

    def unavailable_service_call(**_: Any) -> Any:
        return None

    monkeypatch.setattr("atlas_once.code_intelligence.subprocess.run", fake_run)
    monkeypatch.setattr(
        "atlas_once.code_intelligence.call_intelligence_service",
        unavailable_service_call,
    )

    assert main(["--json", "symbols", "Agent"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert [cmd[1] for cmd in calls] == ["dexterity.index", "dexterity.query"]
    assert payload["data"]["tool"]["transport"] == "subprocess"
    assert payload["data"]["tool"]["service"]["used"] is False


def test_query_records_sync_index_so_next_query_can_skip(
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
        **_: Any,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[:2] == ["mix", "dexterity.index"]:
            return subprocess.CompletedProcess(cmd, 0, "index refreshed\n", "")
        assert cmd[:3] == ["mix", "dexterity.query", "symbols"]
        return subprocess.CompletedProcess(
            cmd,
            0,
            json.dumps({"ok": True, "result": [{"file": "lib/demo/agent.ex"}]}),
            "",
        )

    monkeypatch.setattr("atlas_once.code_intelligence.subprocess.run", fake_run)

    assert main(["--json", "symbols", "Agent"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert main(["--json", "symbols", "Agent"]) == 0
    second = json.loads(capsys.readouterr().out)

    assert [cmd[1] for cmd in calls] == [
        "dexterity.index",
        "dexterity.query",
    ]
    assert first["data"]["index"]["skipped"] is False
    assert second["data"]["index"]["skipped"] is True
    assert second["data"]["tool"]["cache"]["hit"] is True


def test_query_cache_skips_repeated_backend_query_when_index_stamp_matches(
    atlas_env: Path,
    capsys,
    monkeypatch,
) -> None:
    _write_ranked_runtime(atlas_env)
    repo = atlas_env / "code" / "demo"
    _make_mix_repo(repo)
    monkeypatch.chdir(repo)
    query_attempts = 0

    def fake_run(
        cmd: list[str],
        **_: Any,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal query_attempts
        if cmd[:2] == ["mix", "dexterity.index"]:
            shadow = Path(cmd[cmd.index("--repo-root") + 1])
            store = shadow / ".dexterity"
            store.mkdir(parents=True, exist_ok=True)
            (store / "dexterity.db").write_text("stamp-one", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, "index refreshed\n", "")
        assert cmd[:3] == ["mix", "dexterity.query", "symbols"]
        query_attempts += 1
        return subprocess.CompletedProcess(
            cmd,
            0,
            json.dumps({"ok": True, "result": [{"file": "lib/demo/agent.ex"}]}),
            "",
        )

    monkeypatch.setattr("atlas_once.code_intelligence.subprocess.run", fake_run)

    assert main(["--json", "symbols", "Agent"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert main(["--json", "symbols", "Agent"]) == 0
    second = json.loads(capsys.readouterr().out)

    assert query_attempts == 1
    assert first["data"]["tool"]["cache"]["hit"] is False
    assert second["data"]["tool"]["cache"]["hit"] is True
    assert second["data"]["result"] == [{"file": "lib/demo/agent.ex"}]


def test_query_cache_invalidates_when_index_stamp_changes(
    atlas_env: Path,
    capsys,
    monkeypatch,
) -> None:
    _write_ranked_runtime(atlas_env)
    repo = atlas_env / "code" / "demo"
    _make_mix_repo(repo)
    monkeypatch.chdir(repo)
    query_attempts = 0

    def fake_run(
        cmd: list[str],
        **_: Any,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal query_attempts
        if cmd[:2] == ["mix", "dexterity.index"]:
            shadow = Path(cmd[cmd.index("--repo-root") + 1])
            store = shadow / ".dexterity"
            store.mkdir(parents=True, exist_ok=True)
            (store / "dexterity.db").write_text("stamp-one", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, "index refreshed\n", "")
        assert cmd[:3] == ["mix", "dexterity.query", "symbols"]
        query_attempts += 1
        return subprocess.CompletedProcess(
            cmd,
            0,
            json.dumps({"ok": True, "result": [{"file": f"lib/demo/agent{query_attempts}.ex"}]}),
            "",
        )

    monkeypatch.setattr("atlas_once.code_intelligence.subprocess.run", fake_run)

    assert main(["--json", "symbols", "Agent"]) == 0
    json.loads(capsys.readouterr().out)
    paths = get_paths()
    target = make_watch_target(repo.resolve(), project_ref=repo.name)
    source_mtime = _source_mtime(repo)
    save_state(
        paths,
        IndexWatcherState(
            projects={
                target.project_key: IndexProjectState(
                    project_key=target.project_key,
                    project_ref=target.project_ref,
                    project_path=str(target.project_path),
                    last_file_mtime=source_mtime,
                    indexed_file_mtime=source_mtime,
                    last_refresh_finished_at=1.0,
                )
            }
        ),
    )

    assert main(["--json", "symbols", "Agent"]) == 0
    second = json.loads(capsys.readouterr().out)

    assert query_attempts == 2
    assert second["data"]["tool"]["cache"]["hit"] is False
    assert second["data"]["result"] == [{"file": "lib/demo/agent2.ex"}]


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


def test_ranked_files_filter_external_and_dependency_results_by_default(
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
        if cmd[:2] == ["mix", "dexterity.index"]:
            return subprocess.CompletedProcess(cmd, 0, "index refreshed\n", "")
        assert cmd[:3] == ["mix", "dexterity.query", "ranked_files"]
        return subprocess.CompletedProcess(
            cmd,
            0,
            json.dumps(
                {
                    "ok": True,
                    "result": [
                        ["lib/demo/agent.ex", 0.9],
                        ["/opt/elixir/lib/elixir/lib/enum.ex", 0.8],
                        ["deps/dep/lib/dep.ex", 0.7],
                        ["examples/research_agent/deps/credo/lib/credo/check.ex", 0.6],
                    ],
                }
            ),
            "",
        )

    monkeypatch.setattr("atlas_once.code_intelligence.subprocess.run", fake_run)

    assert main(["--json", "ranked-files", "--active", "lib/demo/agent.ex"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["data"]["result"] == [["lib/demo/agent.ex", 0.9]]
    assert payload["data"]["filter"]["removed_count"] == 3
    assert len(payload["data"]["raw"]["result"]) == 4


def test_ranked_files_include_external_preserves_backend_results(
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
        if cmd[:2] == ["mix", "dexterity.index"]:
            return subprocess.CompletedProcess(cmd, 0, "index refreshed\n", "")
        assert cmd[:3] == ["mix", "dexterity.query", "ranked_files"]
        return subprocess.CompletedProcess(
            cmd,
            0,
            json.dumps(
                {
                    "ok": True,
                    "result": [
                        ["lib/demo/agent.ex", 0.9],
                        ["/opt/elixir/lib/elixir/lib/enum.ex", 0.8],
                    ],
                }
            ),
            "",
        )

    monkeypatch.setattr("atlas_once.code_intelligence.subprocess.run", fake_run)

    assert (
        main(["--json", "ranked-files", "--active", "lib/demo/agent.ex", "--include-external"])
        == 0
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["data"]["filter"]["mode"] == "none"
    assert payload["data"]["result"] == [
        ["lib/demo/agent.ex", 0.9],
        ["/opt/elixir/lib/elixir/lib/enum.ex", 0.8],
    ]


def test_impact_filters_dependency_only_lines_by_default(
    atlas_env: Path,
    capsys,
    monkeypatch,
) -> None:
    _write_ranked_runtime(atlas_env)
    repo = atlas_env / "code" / "demo"
    _make_mix_repo(repo)
    monkeypatch.chdir(repo)
    impact = "\n".join(
        [
            "### Demo.Agent.run/0 [CHANGED]",
            "- file: `lib/demo/agent.ex`",
            "- `Demo.Options.t/0` in `lib/demo/options.ex`",
            "- `Credo.Check.format_issue/2` in `deps/credo/lib/credo/check.ex`",
            "- `Credo.Check.format_issue/3` in "
            "`examples/research_agent/deps/credo/lib/credo/check.ex`",
            "",
        ]
    )

    def fake_run(
        cmd: list[str],
        **_: Any,
    ) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["mix", "dexterity.index"]:
            return subprocess.CompletedProcess(cmd, 0, "index refreshed\n", "")
        assert cmd[:3] == ["mix", "dexterity.query", "impact_context"]
        return subprocess.CompletedProcess(
            cmd,
            0,
            json.dumps({"ok": True, "result": impact}),
            "",
        )

    monkeypatch.setattr("atlas_once.code_intelligence.subprocess.run", fake_run)

    assert main(["--json", "impact", "lib/demo/agent.ex"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert "Demo.Options.t/0" in payload["data"]["result"]
    assert "Credo.Check" not in payload["data"]["result"]
    assert "Credo.Check" in payload["data"]["raw"]["result"]
    assert payload["data"]["filter"]["removed_count"] == 2


def test_impact_include_external_preserves_dependency_lines(
    atlas_env: Path,
    capsys,
    monkeypatch,
) -> None:
    _write_ranked_runtime(atlas_env)
    repo = atlas_env / "code" / "demo"
    _make_mix_repo(repo)
    monkeypatch.chdir(repo)
    impact = "- `Credo.Check.format_issue/2` in `deps/credo/lib/credo/check.ex`\n"

    def fake_run(
        cmd: list[str],
        **_: Any,
    ) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["mix", "dexterity.index"]:
            return subprocess.CompletedProcess(cmd, 0, "index refreshed\n", "")
        assert cmd[:3] == ["mix", "dexterity.query", "impact_context"]
        return subprocess.CompletedProcess(
            cmd,
            0,
            json.dumps({"ok": True, "result": impact}),
            "",
        )

    monkeypatch.setattr("atlas_once.code_intelligence.subprocess.run", fake_run)

    assert main(["--json", "impact", "lib/demo/agent.ex", "--include-external"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert "Credo.Check" in payload["data"]["result"]
    assert payload["data"]["filter"]["mode"] == "none"


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
