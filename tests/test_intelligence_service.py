from __future__ import annotations

from pathlib import Path
from typing import Any

from atlas_once.atlas import main
from atlas_once.intelligence_service import (
    MCPCall,
    WorkerPool,
    WorkerTarget,
    mcp_request_for_query,
)


class FakeWorker:
    closed: list[str] = []

    def __init__(self, target: WorkerTarget) -> None:
        self.target = target
        self.pid = len(self.closed) + 1000
        self.started = False
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call_tool(self, tool: str, arguments: dict[str, Any], timeout_seconds: float) -> Any:
        del timeout_seconds
        self.started = True
        self.calls.append((tool, arguments))
        return {"result": {"tool": tool, "shadow": str(self.target.shadow_root)}}

    def close(self) -> None:
        self.closed.append(str(self.target.shadow_root))

    def alive(self) -> bool:
        return True


class TimeoutWorker(FakeWorker):
    def call_tool(self, tool: str, arguments: dict[str, Any], timeout_seconds: float) -> Any:
        del tool, arguments, timeout_seconds
        raise TimeoutError("worker timed out")


def _target(name: str, root: Path) -> WorkerTarget:
    return WorkerTarget(
        project_ref=name,
        repo_root=root / "repos" / name,
        shadow_root=root / "shadows" / name,
        dexterity_root=root / "dexterity",
    )


def test_worker_pool_is_lazy_reuses_and_bounds_workers(atlas_env: Path) -> None:
    FakeWorker.closed = []
    created: list[FakeWorker] = []

    def factory(target: WorkerTarget) -> FakeWorker:
        worker = FakeWorker(target)
        created.append(worker)
        return worker

    pool = WorkerPool(max_workers=2, idle_ttl_seconds=300, worker_factory=factory)
    first = _target("first", atlas_env)
    second = _target("second", atlas_env)
    third = _target("third", atlas_env)

    assert pool.status()["worker_count"] == 0

    one = pool.call(first, "find_symbols", {"query": "Agent"})
    two = pool.call(first, "find_symbols", {"query": "Client"})
    pool.call(second, "find_symbols", {"query": "Options"})
    pool.call(third, "find_symbols", {"query": "Protocol"})

    assert one["worker"]["started"] is True
    assert one["worker"]["reused"] is False
    assert two["worker"]["reused"] is True
    assert len(created) == 3
    assert pool.status()["worker_count"] == 2
    assert str(first.shadow_root) in FakeWorker.closed


def test_worker_pool_quarantines_timed_out_worker(atlas_env: Path) -> None:
    TimeoutWorker.closed = []
    pool = WorkerPool(
        max_workers=2,
        idle_ttl_seconds=300,
        request_timeout_seconds=0.01,
        worker_factory=TimeoutWorker,
    )
    target = _target("slow", atlas_env)

    response = pool.call(target, "find_symbols", {"query": "Citadel"})

    assert response["ok"] is False
    assert response["error"]["kind"] == "worker_timeout"
    assert pool.status()["worker_count"] == 0
    assert str(target.shadow_root) in TimeoutWorker.closed


def test_mcp_request_mapping_for_agent_workflow() -> None:
    assert mcp_request_for_query("symbols", ["Agent"], ["--limit", "12"]) == MCPCall(
        tool="find_symbols",
        arguments={"query": "Agent", "limit": 12},
    )
    assert mcp_request_for_query("references", ["ClaudeAgentSDK.Agent"], []) == MCPCall(
        tool="query_references",
        arguments={"module": "ClaudeAgentSDK.Agent"},
    )
    assert mcp_request_for_query(
        "ranked_files",
        [],
        ["--active-file", "lib/agent.ex", "--limit", "10"],
    ) == MCPCall(
        tool="get_ranked_files",
        arguments={"active_file": "lib/agent.ex", "limit": 10},
    )
    assert mcp_request_for_query(
        "impact_context",
        [],
        ["--changed-file", "lib/agent.ex", "--token-budget", "5000", "--limit", "8"],
    ) == MCPCall(
        tool="get_impact_context",
        arguments={"changed_files": ["lib/agent.ex"], "token_budget": 5000, "limit": 8},
    )


def test_intelligence_status_when_daemon_not_running(atlas_env: Path, capsys) -> None:
    assert main(["--json", "intelligence", "status"]) == 0
    payload = capsys.readouterr().out

    assert '"command": "intelligence.status"' in payload
    assert '"running": false' in payload
