from __future__ import annotations

from pathlib import Path

from atlas_once.mcp.adapter import AtlasMcpCall, AtlasMcpError, call_atlas
from atlas_once.mcp.tools import call_tool, iter_tool_definitions

EXPECTED_WRITE_ALLOWLIST = {
    "atlas_install",
    "atlas_config_profile_use",
    "atlas_config_ranked_install",
    "atlas_config_ranked_group_add",
    "atlas_memory_add",
    "atlas_note_create",
    "atlas_inbox_promote",
    "atlas_context_ranked_warm",
    "atlas_mcp_config_install",
}


def test_unknown_mcp_tool_is_rejected(atlas_env: Path) -> None:
    result = call_tool("atlas_unknown", {})

    assert result["ok"] is False
    assert result["error"]["kind"] == "unknown_tool"


def test_unknown_atlas_adapter_command_is_rejected(atlas_env: Path) -> None:
    try:
        call_atlas(AtlasMcpCall(command="atlas_unknown", args={}))
    except AtlasMcpError as exc:
        assert "Unknown Atlas MCP command" in str(exc)
    else:
        raise AssertionError("Unknown adapter command was accepted.")


def test_shell_metacharacters_are_treated_as_data(atlas_env: Path) -> None:
    result = call_tool(
        "atlas_memory_add",
        {
            "text": "hello; rm -rf /",
            "tags": ["literal"],
            "confirm_write": True,
        },
    )

    assert result["ok"] is True
    assert result["data"]["entry"]["text"] == "hello; rm -rf /"


def test_no_tool_accepts_raw_command_arrays() -> None:
    for definition in iter_tool_definitions():
        properties = definition.input_schema.get("properties", {})
        assert "argv" not in properties
        assert "command" not in properties
        assert "shell" not in properties


def test_write_tool_allowlist_is_explicit() -> None:
    write_tools = {
        definition.name
        for definition in iter_tool_definitions()
        if definition.access == "write"
    }

    assert write_tools == EXPECTED_WRITE_ALLOWLIST
