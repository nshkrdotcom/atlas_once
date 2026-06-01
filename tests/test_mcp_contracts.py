from __future__ import annotations

import json
from pathlib import Path

from atlas_once.mcp.tools import call_tool, iter_tool_definitions

FORBIDDEN_TOOL_NAMES = {
    "run_shell",
    "exec",
    "shell",
    "write_file",
    "edit_config_file",
}


def test_every_registered_mcp_tool_has_discoverable_contract() -> None:
    definitions = list(iter_tool_definitions())

    assert definitions
    for definition in definitions:
        assert definition.name
        assert definition.description
        assert definition.access in {"read", "write"}
        assert definition.input_schema["type"] == "object"
        assert isinstance(definition.input_schema.get("properties"), dict)


def test_no_raw_shell_or_direct_config_tools_are_registered() -> None:
    names = {definition.name for definition in iter_tool_definitions()}

    assert names.isdisjoint(FORBIDDEN_TOOL_NAMES)
    assert all("shell" not in name for name in names)
    assert all("exec" not in name for name in names)
    assert all("edit_config" not in name for name in names)


def test_write_tools_require_explicit_confirm_write_field() -> None:
    for definition in iter_tool_definitions():
        if definition.access != "write":
            continue
        required = set(definition.input_schema.get("required", []))
        properties = definition.input_schema.get("properties", {})
        assert "confirm_write" in required
        assert properties["confirm_write"]["const"] is True


def test_mcp_tool_responses_are_json_serializable(atlas_env: Path) -> None:
    result = call_tool("atlas_status", {})

    assert result["ok"] is True
    json.dumps(result)


def test_mcp_tool_errors_normalize_to_structured_error(atlas_env: Path) -> None:
    result = call_tool("atlas_config_profile_show", {"profile": "missing-profile"})

    assert result["ok"] is False
    assert result["command"] == "atlas_config_profile_show"
    assert result["error"]["kind"] in {"not_found", "validation_error"}
    assert result["errors"]
    json.dumps(result)
