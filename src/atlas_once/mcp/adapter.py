from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from io import StringIO
from typing import Any

from atlas_once import atlas
from atlas_once.profiles import DEFAULT_INSTALL_PROFILE


class AtlasMcpError(Exception):
    """Raised when an MCP request violates the Atlas MCP allowlist."""


@dataclass(frozen=True)
class AtlasMcpCall:
    command: str
    args: dict[str, object]
    write: bool = False


CommandBuilder = Callable[[dict[str, object]], list[str]]


def _optional_text(args: dict[str, object], key: str) -> str | None:
    value = args.get(key)
    if value is None:
        return None
    return str(value)


def _text(args: dict[str, object], key: str, default: str | None = None) -> str:
    value = _optional_text(args, key)
    if value is None:
        if default is None:
            raise AtlasMcpError(f"Missing required argument: {key}")
        return default
    return value


def _optional_int(args: dict[str, object], key: str) -> int | None:
    value = args.get(key)
    if value is None:
        return None
    return int(str(value))


def _bool(args: dict[str, object], key: str, default: bool = False) -> bool:
    value = args.get(key)
    if value is None:
        return default
    return bool(value)


def _string_list(args: dict[str, object], key: str) -> list[str]:
    value = args.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise AtlasMcpError(f"{key} must be a list.")
    return [str(item) for item in value]


def _append_option(argv: list[str], flag: str, value: object | None) -> None:
    if value is not None:
        argv.extend([flag, str(value)])


def _append_repeated(argv: list[str], flag: str, values: list[str]) -> None:
    for value in values:
        argv.extend([flag, value])


def _build_help(args: dict[str, object]) -> list[str]:
    topic = _optional_text(args, "topic")
    return ["help", topic] if topic else ["help-full"]


def _build_profile_show(args: dict[str, object]) -> list[str]:
    return ["config", "profile", "show", _text(args, "profile")]


def _build_ranked_repos(args: dict[str, object]) -> list[str]:
    return ["context", "ranked", "repos", _text(args, "scope")]


def _ranked_render_args(args: dict[str, object], *, mode: str | None = None) -> list[str]:
    scope = _text(args, "scope")
    argv = ["context", "ranked"]
    if mode is not None:
        argv.append(mode)
    argv.append(scope)
    _append_option(argv, "--portion", _optional_int(args, "portion"))
    _append_option(argv, "--max-tokens", _optional_int(args, "max_tokens"))
    _append_option(argv, "--max-bytes", _optional_int(args, "max_bytes"))
    if _bool(args, "fresh", False):
        argv.append("--fresh-required")
    return argv


def _build_ranked_tree(args: dict[str, object]) -> list[str]:
    argv = _ranked_render_args(args, mode="tree")
    _append_option(argv, "--max-depth", _optional_int(args, "max_depth"))
    _append_repeated(argv, "--include", _string_list(args, "include"))
    if _bool(args, "include_all", False):
        argv.append("--all")
    return argv


def _build_git_status(args: dict[str, object]) -> list[str]:
    selectors = _string_list(args, "selectors") or ["@all"]
    argv = ["git", "status", *selectors]
    if _bool(args, "refresh", False):
        argv.append("--refresh")
    if _bool(args, "include_clean", False):
        argv.append("--include-clean")
    if _bool(args, "include_errors", False):
        argv.append("--include-errors")
    _append_option(argv, "--order-by", _optional_text(args, "order_by"))
    return argv


def _build_registry_scan(args: dict[str, object]) -> list[str]:
    argv = ["registry", "scan"]
    if _bool(args, "changed_only", False):
        argv.append("--changed-only")
    return argv


def _build_memory_find(args: dict[str, object]) -> list[str]:
    return ["find", _text(args, "query")]


def _build_notes_related(args: dict[str, object]) -> list[str]:
    argv = ["related", _text(args, "path")]
    _append_option(argv, "--limit", _optional_int(args, "limit"))
    return argv


def _build_inbox_review(args: dict[str, object]) -> list[str]:
    argv = ["review", "inbox"]
    _append_option(argv, "--date", _optional_text(args, "date"))
    return argv


def _build_install(args: dict[str, object]) -> list[str]:
    return ["install", "--profile", _text(args, "profile", DEFAULT_INSTALL_PROFILE)]


def _build_profile_use(args: dict[str, object]) -> list[str]:
    return ["config", "profile", "use", _text(args, "profile")]


def _build_ranked_install(args: dict[str, object]) -> list[str]:
    argv = ["config", "ranked", "install"]
    _append_option(argv, "--profile", _optional_text(args, "profile"))
    if _bool(args, "force", False):
        argv.append("--force")
    return argv


def _build_ranked_group_add(args: dict[str, object]) -> list[str]:
    refs = _string_list(args, "refs")
    if not refs:
        raise AtlasMcpError("refs must contain at least one repo reference.")
    argv = ["config", "ranked", "group", "add", _text(args, "group"), *refs]
    _append_option(argv, "--variant", _optional_text(args, "variant"))
    if _bool(args, "force", False):
        argv.append("--force")
    return argv


def _build_memory_add(args: dict[str, object]) -> list[str]:
    argv = ["capture", "--kind", _text(args, "kind", "note")]
    _append_option(argv, "--project", _optional_text(args, "project"))
    _append_repeated(argv, "--tag", _string_list(args, "tags"))
    argv.append(_text(args, "text"))
    return argv


def _build_note_create(args: dict[str, object]) -> list[str]:
    argv = ["note", "new", _text(args, "title"), "--kind", _text(args, "kind", "note")]
    _append_option(argv, "--project", _optional_text(args, "project"))
    _append_repeated(argv, "--tag", _string_list(args, "tags"))
    _append_option(argv, "--body", _optional_text(args, "body"))
    return argv


def _build_inbox_promote(args: dict[str, object]) -> list[str]:
    entry_id = _optional_text(args, "entry_id")
    if entry_id is None:
        argv = ["promote", "auto"]
        _append_option(argv, "--date", _optional_text(args, "date"))
        return argv
    argv = ["promote", "entry", entry_id]
    _append_option(argv, "--kind", _optional_text(args, "kind"))
    _append_option(argv, "--title", _optional_text(args, "title"))
    _append_option(argv, "--project", _optional_text(args, "project"))
    return argv


def _build_ranked_warm(args: dict[str, object]) -> list[str]:
    argv = ["context", "ranked", "warm", _text(args, "scope")]
    if _bool(args, "wait", False):
        argv.extend(["--wait-fresh-ms", "5000"])
    return argv


def _build_mcp_config_install(args: dict[str, object]) -> list[str]:
    argv = ["config", "mcp", "install"]
    _append_option(argv, "--client", _optional_text(args, "client"))
    _append_option(argv, "--profile", _optional_text(args, "profile"))
    return argv


COMMAND_BUILDERS: dict[str, CommandBuilder] = {
    "atlas_status": lambda _args: ["status"],
    "atlas_help": _build_help,
    "atlas_config_profile_list": lambda _args: ["config", "profile", "list"],
    "atlas_config_profile_show": _build_profile_show,
    "atlas_config_ranked_show": lambda _args: ["config", "ranked", "show"],
    "atlas_context_ranked_groups": lambda _args: ["context", "ranked", "groups"],
    "atlas_context_ranked_repos": _build_ranked_repos,
    "atlas_context_ranked": lambda args: _ranked_render_args(args),
    "atlas_context_ranked_tree": _build_ranked_tree,
    "atlas_context_ranked_cache": lambda args: _ranked_render_args(args, mode="cache"),
    "atlas_git_status": _build_git_status,
    "atlas_registry_scan": _build_registry_scan,
    "atlas_memory_find": _build_memory_find,
    "atlas_notes_related": _build_notes_related,
    "atlas_inbox_review": _build_inbox_review,
    "atlas_install": _build_install,
    "atlas_config_profile_use": _build_profile_use,
    "atlas_config_ranked_install": _build_ranked_install,
    "atlas_config_ranked_group_add": _build_ranked_group_add,
    "atlas_memory_add": _build_memory_add,
    "atlas_note_create": _build_note_create,
    "atlas_inbox_promote": _build_inbox_promote,
    "atlas_context_ranked_warm": _build_ranked_warm,
    "atlas_mcp_config_install": _build_mcp_config_install,
}


def _normalize_error(error: dict[str, Any]) -> dict[str, Any]:
    message = str(error.get("message", ""))
    kind = str(error.get("kind", "error"))
    if message.startswith(("Unknown profile:", "Unknown ranked group")):
        kind = "not_found"
    return {
        "kind": kind,
        "message": message,
        "details": error.get("details") if isinstance(error.get("details"), dict) else {},
    }


def _mcp_success(
    command: str,
    data: dict[str, Any],
    atlas_payload: dict[str, Any],
    warnings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "ok": True,
        "command": command,
        "data": data,
        "warnings": warnings or [],
        "errors": [],
        "atlas": {
            "schema_version": atlas_payload.get("schema_version"),
            "command": atlas_payload.get("command"),
            "exit_code": atlas_payload.get("exit_code"),
        },
    }


def _mcp_failure(
    command: str,
    *,
    kind: str,
    message: str,
    details: dict[str, Any] | None = None,
    atlas_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error = {"kind": kind, "message": message, "details": details or {}}
    response: dict[str, Any] = {
        "ok": False,
        "command": command,
        "data": {},
        "error": error,
        "warnings": [],
        "errors": [error],
    }
    if atlas_payload is not None:
        response["atlas"] = {
            "schema_version": atlas_payload.get("schema_version"),
            "command": atlas_payload.get("command"),
            "exit_code": atlas_payload.get("exit_code"),
        }
    return response


def validation_failure(command: str, message: str) -> dict[str, Any]:
    return _mcp_failure(command, kind="validation_error", message=message)


def unknown_tool_failure(command: str) -> dict[str, Any]:
    return _mcp_failure(
        command,
        kind="unknown_tool",
        message=f"Unknown Atlas MCP tool: {command}",
        details={"command": command},
    )


def adapter_failure(command: str, error: Exception) -> dict[str, Any]:
    return _mcp_failure(command, kind="adapter_error", message=str(error))


def call_atlas(call: AtlasMcpCall) -> dict[str, Any]:
    try:
        builder = COMMAND_BUILDERS[call.command]
    except KeyError as exc:
        raise AtlasMcpError(f"Unknown Atlas MCP command: {call.command}") from exc

    argv = ["--json", *builder(call.args)]
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = atlas.main(argv)

    raw_stdout = stdout.getvalue().strip()
    try:
        payload = json.loads(raw_stdout)
    except json.JSONDecodeError:
        return _mcp_failure(
            call.command,
            kind="invalid_atlas_response",
            message="Atlas did not return a JSON response.",
            details={
                "exit_code": exit_code,
                "stdout": raw_stdout,
                "stderr": stderr.getvalue().strip(),
            },
        )

    if bool(payload.get("ok")):
        data = payload.get("data")
        if not isinstance(data, dict):
            data = {}
        return _mcp_success(call.command, data, payload)

    raw_errors = payload.get("errors")
    errors = [
        _normalize_error(error)
        for error in raw_errors
        if isinstance(error, dict)
    ] if isinstance(raw_errors, list) else []
    if not errors:
        errors = [
            {
                "kind": "atlas_error",
                "message": "Atlas command failed.",
                "details": {"exit_code": exit_code},
            }
        ]
    primary = errors[0]
    if (
        call.command == "atlas_context_ranked_groups"
        and str(primary["message"]).startswith("Missing ranked context config:")
    ):
        return _mcp_success(
            call.command,
            {"groups": {"groups": [], "group_count": 0}, "names": []},
            payload,
            warnings=[primary],
        )
    response = _mcp_failure(
        call.command,
        kind=str(primary["kind"]),
        message=str(primary["message"]),
        details=primary["details"] if isinstance(primary.get("details"), dict) else {},
        atlas_payload=payload,
    )
    response["errors"] = errors
    return response
