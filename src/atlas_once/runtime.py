from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import IntEnum
from fcntl import LOCK_EX, LOCK_NB, LOCK_UN, flock
from typing import Any, cast

from .config import AtlasPaths, ensure_state

SCHEMA_VERSION = "1.0"


class ExitCode(IntEnum):
    OK = 0
    USAGE = 2
    NOT_FOUND = 3
    AMBIGUOUS = 4
    CONFLICT = 5
    LOCKED = 6
    EXTERNAL = 7
    VALIDATION = 8
    INTERNAL = 10


@dataclass(frozen=True)
class AtlasCliError(Exception):
    code: ExitCode
    kind: str
    message: str
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.message


def json_payload(
    command: str,
    ok: bool,
    exit_code: int,
    data: dict[str, Any] | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": ok,
        "command": command,
        "exit_code": int(exit_code),
        "data": data or {},
        "errors": errors or [],
    }


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def success(command: str, data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    return int(ExitCode.OK), json_payload(command, ok=True, exit_code=ExitCode.OK, data=data)


def failure(
    command: str,
    code: ExitCode,
    kind: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    return int(code), json_payload(
        command,
        ok=False,
        exit_code=code,
        data={},
        errors=[{"kind": kind, "message": message, "details": details or {}}],
    )


def approx_tokens(text: str) -> int:
    return max(1, round(len(text) / 4))


def event_summary_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("ok"):
        return cast(dict[str, Any], payload.get("data", {}))
    return {"errors": payload.get("errors", [])}


def append_event(
    paths: AtlasPaths,
    command: str,
    argv: list[str],
    exit_code: int,
    payload: dict[str, Any],
) -> None:
    ensure_state(paths)
    record = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "command": command,
        "argv": argv,
        "exit_code": exit_code,
        "ok": payload.get("ok", exit_code == 0),
        "summary": event_summary_from_payload(payload),
    }
    with paths.events_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


@contextmanager
def mutation_lock(
    paths: AtlasPaths,
    name: str = "global",
    timeout_seconds: float = 5.0,
) -> Iterator[Any]:
    ensure_state(paths)
    lock_path = paths.locks_root / f"{name}.lock"
    deadline = time.monotonic() + timeout_seconds
    with lock_path.open("w", encoding="utf-8") as handle:
        while True:
            try:
                flock(handle.fileno(), LOCK_EX | LOCK_NB)
                break
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    raise AtlasCliError(
                        ExitCode.LOCKED,
                        "lock_timeout",
                        f"Timed out waiting for lock: {lock_path}",
                        {"lock": str(lock_path)},
                    ) from exc
                time.sleep(0.05)
        try:
            yield lock_path
        finally:
            flock(handle.fileno(), LOCK_UN)


def map_exception(command: str, error: BaseException) -> tuple[int, dict[str, Any]]:
    if isinstance(error, AtlasCliError):
        return failure(command, error.code, error.kind, error.message, error.details)

    if isinstance(error, SystemExit):
        if isinstance(error.code, int):
            code = ExitCode.USAGE if error.code == 2 else ExitCode.INTERNAL
            return failure(command, code, "system_exit", f"Exited with code {error.code}")
        message = str(error)
        if message.startswith("Unknown project reference"):
            return failure(command, ExitCode.NOT_FOUND, "unknown_project", message)
        if message.startswith("Ambiguous project reference"):
            return failure(command, ExitCode.AMBIGUOUS, "ambiguous_project", message)
        if (
            message.startswith("Path does not exist")
            or message.startswith("Path is not a file")
            or message.startswith("Path is not a directory")
            or message.startswith("No matching notes found")
        ):
            return failure(command, ExitCode.NOT_FOUND, "path_not_found", message)
        if message.startswith("Note already exists") or message.startswith(
            "Session note already exists"
        ):
            return failure(command, ExitCode.CONFLICT, "conflict", message)
        if message.startswith("Unknown inbox entry id"):
            return failure(command, ExitCode.NOT_FOUND, "unknown_inbox_entry", message)
        return failure(command, ExitCode.VALIDATION, "validation_error", message)

    return failure(command, ExitCode.INTERNAL, "internal_error", str(error))
