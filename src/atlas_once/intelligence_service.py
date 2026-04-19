from __future__ import annotations

import argparse
import json
import os
import select
import socket
import socketserver
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Protocol, cast

from .config import AtlasPaths, ensure_state, get_paths

SERVICE_ENV = "ATLAS_ONCE_INTELLIGENCE_SERVICE"
MAX_WORKERS_ENV = "ATLAS_ONCE_INTELLIGENCE_SERVICE_MAX_WORKERS"
IDLE_TTL_ENV = "ATLAS_ONCE_INTELLIGENCE_SERVICE_IDLE_TTL_SECONDS"
REQUEST_TIMEOUT_ENV = "ATLAS_ONCE_INTELLIGENCE_SERVICE_REQUEST_TIMEOUT_SECONDS"
DEFAULT_MAX_WORKERS = 4
DEFAULT_IDLE_TTL_SECONDS = 300.0
DEFAULT_REQUEST_TIMEOUT_SECONDS = 30.0
STARTUP_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class MCPCall:
    tool: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class WorkerTarget:
    project_ref: str
    repo_root: Path
    shadow_root: Path
    dexterity_root: Path

    @property
    def key(self) -> str:
        return str(self.shadow_root)


class WorkerLike(Protocol):
    pid: int | None

    def start(self, timeout_seconds: float) -> None: ...

    def call_tool(self, tool: str, arguments: dict[str, Any], timeout_seconds: float) -> Any: ...

    def close(self) -> None: ...

    def alive(self) -> bool: ...


def service_root(paths: AtlasPaths) -> Path:
    return paths.state_home / "code" / "intelligence_service"


def service_socket_path(paths: AtlasPaths) -> Path:
    return service_root(paths) / "service.sock"


def service_pid_path(paths: AtlasPaths) -> Path:
    return service_root(paths) / "service.pid"


def service_log_path(paths: AtlasPaths) -> Path:
    return service_root(paths) / "service.log"


def _env_enabled(name: str, *, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def service_enabled() -> bool:
    return _env_enabled(SERVICE_ENV, default=True)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _parse_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _add_if_present(target: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        target[key] = value


def _parse_option_args(option_args: list[str] | None) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    multi: dict[str, list[str]] = {
        "mentioned_files": [],
        "edited_files": [],
        "include_prefixes": [],
        "exclude_prefixes": [],
        "changed_files": [],
    }
    args = option_args or []
    index = 0
    while index < len(args):
        flag = args[index]
        value = args[index + 1] if index + 1 < len(args) else None
        if value is None:
            break
        if flag == "--limit":
            _add_if_present(parsed, "limit", _parse_int(value))
        elif flag == "--depth":
            _add_if_present(parsed, "depth", _parse_int(value))
        elif flag == "--token-budget":
            _add_if_present(parsed, "token_budget", _parse_int(value))
        elif flag == "--overscan-limit":
            _add_if_present(parsed, "overscan_limit", _parse_int(value))
        elif flag == "--active-file":
            parsed["active_file"] = value
        elif flag == "--mentioned-file":
            multi["mentioned_files"].append(value)
        elif flag == "--edited-file":
            multi["edited_files"].append(value)
        elif flag == "--include-prefix":
            multi["include_prefixes"].append(value)
        elif flag == "--exclude-prefix":
            multi["exclude_prefixes"].append(value)
        elif flag == "--changed-file":
            multi["changed_files"].append(value)
        index += 2
    for key, values in multi.items():
        if values:
            parsed[key] = values
    return parsed


def _symbol_args(positional: list[str]) -> dict[str, Any] | None:
    if not positional:
        return None
    return {"module": positional[0], **({"function": positional[1]} if len(positional) > 1 else {})}


def _module_function_args(positional: list[str]) -> dict[str, Any] | None:
    args = _symbol_args(positional)
    if args is None:
        return None
    if len(positional) > 2:
        arity = _parse_int(positional[2])
        if arity is not None:
            args["arity"] = arity
    return args


def mcp_request_for_query(
    action: str,
    positional: list[str],
    option_args: list[str] | None,
) -> MCPCall | None:
    options = _parse_option_args(option_args)
    if action == "definition":
        args = _module_function_args(positional)
        return MCPCall("query_definition", args) if args else None
    if action == "references":
        args = _module_function_args(positional)
        return MCPCall("query_references", args) if args else None
    if action == "symbols" and positional:
        return MCPCall("find_symbols", {"query": positional[0], **options})
    if action == "files" and positional:
        return MCPCall("match_files", {"pattern": positional[0], **options})
    if action == "blast" and positional:
        return MCPCall("query_blast", {"file": positional[0], **options})
    if action == "blast_count" and positional:
        return MCPCall("get_file_blast_radius", {"file": positional[0]})
    if action == "cochanges" and positional:
        return MCPCall("query_cochanges", {"file": positional[0], **options})
    if action == "ranked_files":
        return MCPCall("get_ranked_files", options)
    if action == "ranked_symbols":
        return MCPCall("get_ranked_symbols", options)
    if action == "impact_context":
        return MCPCall("get_impact_context", options)
    if action == "export_analysis":
        return MCPCall("get_export_analysis", options)
    if action == "unused_exports":
        return MCPCall("get_unused_exports", options)
    if action == "test_only_exports":
        return MCPCall("get_test_only_exports", options)
    if action == "file_graph":
        return MCPCall("get_file_graph_snapshot", options)
    if action == "symbol_graph":
        return MCPCall("get_symbol_graph_snapshot", options)
    if action == "structural_snapshot":
        return MCPCall("get_structural_snapshot", options)
    if action == "runtime_observations":
        return MCPCall("get_runtime_observations", options)
    return None


class MCPWorker:
    def __init__(self, target: WorkerTarget) -> None:
        self.target = target
        self.process: subprocess.Popen[str] | None = None
        self.pid: int | None = None
        self._next_id = 1
        self._lock = threading.Lock()

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self, timeout_seconds: float) -> None:
        if self.alive():
            return
        command = [
            "mix",
            "dexterity.mcp.serve",
            "--repo-root",
            str(self.target.shadow_root),
            "--backend",
            "Dexterity.Backend.Dexter",
        ]
        self.process = subprocess.Popen(
            command,
            cwd=str(self.target.dexterity_root),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env=os.environ.copy(),
        )
        self.pid = self.process.pid
        self._call_method("initialize", {"info": "atlas_once"}, timeout_seconds)

    def call_tool(self, tool: str, arguments: dict[str, Any], timeout_seconds: float) -> Any:
        with self._lock:
            self.start(timeout_seconds)
            return self._call_method(
                "tools/call",
                {"name": tool, "arguments": arguments},
                timeout_seconds,
            )

    def _call_method(self, method: str, params: dict[str, Any], timeout_seconds: float) -> Any:
        if self.process is None or self.process.stdin is None or self.process.stdout is None:
            raise RuntimeError("MCP worker is not started")
        request_id = self._next_id
        self._next_id += 1
        request = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        self.process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        return self._read_response(request_id, self.process.stdout, timeout_seconds)

    def _read_response(
        self,
        request_id: int,
        stdout: IO[str],
        timeout_seconds: float,
    ) -> Any:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            ready, _, _ = select.select([stdout], [], [], min(remaining, 0.25))
            if not ready:
                continue
            line = stdout.readline()
            if line == "":
                raise RuntimeError("MCP worker exited before responding")
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if payload.get("id") != request_id:
                continue
            if "error" in payload:
                raise RuntimeError(json.dumps(payload["error"], sort_keys=True))
            return payload.get("result")
        raise TimeoutError(f"Timed out waiting for MCP response id={request_id}")

    def close(self) -> None:
        process = self.process
        if process is None:
            return
        if process.poll() is None:
            with suppress(BrokenPipeError, RuntimeError, TimeoutError, OSError):
                self._call_method("shutdown", {}, 1.0)
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)


@dataclass
class WorkerEntry:
    target: WorkerTarget
    worker: WorkerLike
    last_used: float
    busy: bool = False


class WorkerPool:
    def __init__(
        self,
        *,
        max_workers: int = DEFAULT_MAX_WORKERS,
        idle_ttl_seconds: float = DEFAULT_IDLE_TTL_SECONDS,
        request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        worker_factory: Callable[[WorkerTarget], WorkerLike] | None = None,
    ) -> None:
        self.max_workers = max_workers
        self.idle_ttl_seconds = idle_ttl_seconds
        self.request_timeout_seconds = request_timeout_seconds
        self.worker_factory = worker_factory or MCPWorker
        self._workers: dict[str, WorkerEntry] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> WorkerPool:
        return cls(
            max_workers=_env_int(MAX_WORKERS_ENV, DEFAULT_MAX_WORKERS),
            idle_ttl_seconds=_env_float(IDLE_TTL_ENV, DEFAULT_IDLE_TTL_SECONDS),
            request_timeout_seconds=_env_float(
                REQUEST_TIMEOUT_ENV,
                DEFAULT_REQUEST_TIMEOUT_SECONDS,
            ),
        )

    def call(self, target: WorkerTarget, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        entry, reused = self._entry_for_target(target)
        try:
            result = entry.worker.call_tool(tool, arguments, self.request_timeout_seconds)
            return {
                "ok": True,
                "transport": "mcp_service",
                "worker": {
                    "key": target.key,
                    "pid": entry.worker.pid,
                    "started": not reused,
                    "reused": reused,
                },
                "result": result,
            }
        except TimeoutError as exc:
            self._drop_entry(target.key, entry)
            return {
                "ok": False,
                "error": {
                    "kind": "worker_timeout",
                    "message": str(exc),
                    "timeout_seconds": self.request_timeout_seconds,
                },
                "worker": {
                    "key": target.key,
                    "pid": entry.worker.pid,
                    "started": not reused,
                    "reused": reused,
                },
            }
        except Exception as exc:
            self._drop_entry(target.key, entry)
            return {
                "ok": False,
                "error": {
                    "kind": "worker_error",
                    "message": str(exc),
                },
                "worker": {
                    "key": target.key,
                    "pid": entry.worker.pid,
                    "started": not reused,
                    "reused": reused,
                },
            }
        finally:
            with self._lock:
                current = self._workers.get(target.key)
                if current is entry:
                    current.busy = False
                    current.last_used = time.time()

    def warm(self, target: WorkerTarget) -> dict[str, Any]:
        entry, reused = self._entry_for_target(target)
        try:
            entry.worker.start(self.request_timeout_seconds)
            return {
                "ok": True,
                "transport": "mcp_service",
                "worker": {
                    "key": target.key,
                    "pid": entry.worker.pid,
                    "started": not reused,
                    "reused": reused,
                },
            }
        except TimeoutError as exc:
            self._drop_entry(target.key, entry)
            return {
                "ok": False,
                "error": {
                    "kind": "worker_timeout",
                    "message": str(exc),
                    "timeout_seconds": self.request_timeout_seconds,
                },
                "worker": {
                    "key": target.key,
                    "pid": entry.worker.pid,
                    "started": not reused,
                    "reused": reused,
                },
            }
        except Exception as exc:
            self._drop_entry(target.key, entry)
            return {
                "ok": False,
                "error": {
                    "kind": "worker_error",
                    "message": str(exc),
                },
                "worker": {
                    "key": target.key,
                    "pid": entry.worker.pid,
                    "started": not reused,
                    "reused": reused,
                },
            }
        finally:
            with self._lock:
                current = self._workers.get(target.key)
                if current is entry:
                    current.busy = False
                    current.last_used = time.time()

    def _drop_entry(self, key: str, entry: WorkerEntry) -> None:
        with self._lock:
            current = self._workers.get(key)
            if current is entry:
                del self._workers[key]
        entry.worker.close()

    def _entry_for_target(self, target: WorkerTarget) -> tuple[WorkerEntry, bool]:
        with self._lock:
            self._sweep_locked(time.time())
            existing = self._workers.get(target.key)
            if existing is not None and existing.worker.alive():
                existing.busy = True
                existing.last_used = time.time()
                return existing, True

            if existing is not None:
                existing.worker.close()
                del self._workers[target.key]

            self._ensure_capacity_locked()
            worker = self.worker_factory(target)
            entry = WorkerEntry(target=target, worker=worker, last_used=time.time(), busy=True)
            self._workers[target.key] = entry
            return entry, False

    def _sweep_locked(self, now: float) -> None:
        doomed = [
            key
            for key, entry in self._workers.items()
            if not entry.busy
            and (not entry.worker.alive() or now - entry.last_used >= self.idle_ttl_seconds)
        ]
        for key in doomed:
            self._workers[key].worker.close()
            del self._workers[key]

    def _ensure_capacity_locked(self) -> None:
        if len(self._workers) < self.max_workers:
            return
        idle_entries = [
            (key, entry)
            for key, entry in self._workers.items()
            if not entry.busy
        ]
        if not idle_entries:
            raise RuntimeError("capacity_exhausted")
        key, entry = min(idle_entries, key=lambda item: item[1].last_used)
        entry.worker.close()
        del self._workers[key]

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._sweep_locked(time.time())
            return {
                "worker_count": len(self._workers),
                "max_workers": self.max_workers,
                "idle_ttl_seconds": self.idle_ttl_seconds,
                "workers": [
                    {
                        "key": key,
                        "project_ref": entry.target.project_ref,
                        "shadow_root": str(entry.target.shadow_root),
                        "pid": entry.worker.pid,
                        "busy": entry.busy,
                        "last_used": entry.last_used,
                    }
                    for key, entry in sorted(self._workers.items())
                ],
            }

    def close_all(self) -> None:
        with self._lock:
            entries = list(self._workers.values())
            self._workers = {}
        for entry in entries:
            entry.worker.close()


class IntelligenceUnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, socket_path: str, pool: WorkerPool) -> None:
        self.pool = pool
        super().__init__(socket_path, IntelligenceRequestHandler)


class IntelligenceRequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        raw = self.rfile.readline().decode("utf-8")
        try:
            request = json.loads(raw)
            response = handle_service_request(cast(IntelligenceUnixServer, self.server), request)
        except Exception as exc:
            response = {"ok": False, "error": {"kind": "internal_error", "message": str(exc)}}
        self.wfile.write((json.dumps(response, sort_keys=True) + "\n").encode("utf-8"))


def _worker_target_from_request(request: dict[str, Any]) -> WorkerTarget:
    return WorkerTarget(
        project_ref=str(request["project_ref"]),
        repo_root=Path(str(request["repo_root"])),
        shadow_root=Path(str(request["shadow_root"])),
        dexterity_root=Path(str(request["dexterity_root"])),
    )


def handle_service_request(
    server: IntelligenceUnixServer,
    request: dict[str, Any],
) -> dict[str, Any]:
    op = request.get("op")
    if op == "status":
        return {"ok": True, "running": True, "pool": server.pool.status(), "pid": os.getpid()}
    if op == "shutdown":
        threading.Thread(target=server.shutdown, daemon=True).start()
        return {"ok": True, "running": False, "pid": os.getpid()}
    if op == "mcp_call":
        target = _worker_target_from_request(request)
        return server.pool.call(target, str(request["tool"]), dict(request.get("arguments") or {}))
    if op == "warm":
        target = _worker_target_from_request(request)
        return server.pool.warm(target)
    return {"ok": False, "error": {"kind": "unknown_op", "message": f"unknown op {op}"}}


def _send_request(paths: AtlasPaths, request: dict[str, Any], timeout_seconds: float = 2.0) -> Any:
    socket_path = service_socket_path(paths)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout_seconds)
        client.connect(str(socket_path))
        client.sendall((json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8"))
        chunks = bytearray()
        while True:
            chunk = client.recv(65536)
            if not chunk:
                break
            chunks.extend(chunk)
            if b"\n" in chunk:
                break
    return json.loads(bytes(chunks).decode("utf-8"))


def call_intelligence_service(
    *,
    paths: AtlasPaths,
    target: Any,
    tool: str,
    arguments: dict[str, Any],
    timeout_seconds: float | None = None,
) -> dict[str, Any] | None:
    if not service_enabled():
        return None
    request = {
        "op": "mcp_call",
        "project_ref": target.project_ref,
        "repo_root": str(target.project_root),
        "shadow_root": str(target.shadow_root),
        "dexterity_root": str(target.runtime.dexterity_root),
        "tool": tool,
        "arguments": arguments,
    }
    request_timeout = timeout_seconds or _env_float(
        REQUEST_TIMEOUT_ENV,
        DEFAULT_REQUEST_TIMEOUT_SECONDS,
    )
    try:
        response = _send_request(paths, request, timeout_seconds=request_timeout)
    except TimeoutError:
        return {
            "ok": False,
            "transport": "mcp_service",
            "error": {
                "kind": "service_request_timeout",
                "message": (
                    f"Timed out waiting for intelligence service response "
                    f"after {request_timeout:.1f}s"
                ),
                "timeout_seconds": request_timeout,
            },
        }
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(response, dict):
        return None
    return response


def warm_intelligence_service(
    *,
    paths: AtlasPaths,
    target: Any,
    timeout_seconds: float | None = None,
) -> dict[str, Any] | None:
    if not service_enabled():
        return None
    request = {
        "op": "warm",
        "project_ref": target.project_ref,
        "repo_root": str(target.project_root),
        "shadow_root": str(target.shadow_root),
        "dexterity_root": str(target.runtime.dexterity_root),
    }
    try:
        response = _send_request(
            paths,
            request,
            timeout_seconds=timeout_seconds
            or _env_float(REQUEST_TIMEOUT_ENV, DEFAULT_REQUEST_TIMEOUT_SECONDS),
        )
    except (OSError, TimeoutError, json.JSONDecodeError):
        return None
    if not isinstance(response, dict):
        return None
    return response


def status_service(paths: AtlasPaths) -> dict[str, Any]:
    if not service_socket_path(paths).exists():
        return {
            "running": False,
            "socket": str(service_socket_path(paths)),
            "pid_path": str(service_pid_path(paths)),
        }
    try:
        response = _send_request(paths, {"op": "status"}, timeout_seconds=2.0)
    except (OSError, TimeoutError, json.JSONDecodeError):
        return {
            "running": False,
            "stale_socket": True,
            "socket": str(service_socket_path(paths)),
            "pid_path": str(service_pid_path(paths)),
        }
    response["socket"] = str(service_socket_path(paths))
    response["pid_path"] = str(service_pid_path(paths))
    return cast(dict[str, Any], response)


def start_service(paths: AtlasPaths) -> dict[str, Any]:
    ensure_state(paths)
    root = service_root(paths)
    root.mkdir(parents=True, exist_ok=True)
    current = status_service(paths)
    if current.get("running"):
        current["already_running"] = True
        return current

    stale_socket = service_socket_path(paths)
    stale_socket.unlink(missing_ok=True)
    log = service_log_path(paths).open("a", encoding="utf-8")
    subprocess.Popen(
        [sys.executable, "-m", "atlas_once.intelligence_service", "--serve"],
        stdout=log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        env=os.environ.copy(),
    )
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        status = status_service(paths)
        if status.get("running"):
            status["already_running"] = False
            return status
        time.sleep(0.1)
    return {
        "running": False,
        "started": False,
        "socket": str(service_socket_path(paths)),
        "pid_path": str(service_pid_path(paths)),
        "log_path": str(service_log_path(paths)),
        "error": "startup_timeout",
    }


def stop_service(paths: AtlasPaths) -> dict[str, Any]:
    status = status_service(paths)
    if not status.get("running"):
        return {**status, "stopped": False}
    try:
        response = _send_request(paths, {"op": "shutdown"}, timeout_seconds=2.0)
    except (OSError, TimeoutError, json.JSONDecodeError):
        response = {"ok": False}
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if not service_socket_path(paths).exists():
            return {"running": False, "stopped": True, "response": response}
        time.sleep(0.1)
    return {"running": True, "stopped": False, "response": response}


def serve(paths: AtlasPaths) -> None:
    ensure_state(paths)
    root = service_root(paths)
    root.mkdir(parents=True, exist_ok=True)
    socket_path = service_socket_path(paths)
    socket_path.unlink(missing_ok=True)
    pool = WorkerPool.from_env()
    server = IntelligenceUnixServer(str(socket_path), pool)
    service_pid_path(paths).write_text(str(os.getpid()), encoding="utf-8")
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        pool.close_all()
        server.server_close()
        socket_path.unlink(missing_ok=True)
        service_pid_path(paths).unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m atlas_once.intelligence_service")
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args(argv)
    if args.serve:
        serve(get_paths())
        return 0
    parser.error("expected --serve")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
