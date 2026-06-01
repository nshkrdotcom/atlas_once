from __future__ import annotations

from pathlib import Path

from atlas_once.mcp.tools import call_tool, get_tool_definition, iter_tool_definitions

READ_TOOLS = {
    "atlas_status",
    "atlas_help",
    "atlas_config_profile_list",
    "atlas_config_profile_show",
    "atlas_config_ranked_show",
    "atlas_context_ranked_groups",
    "atlas_context_ranked_repos",
    "atlas_context_ranked",
    "atlas_context_ranked_tree",
    "atlas_context_ranked_cache",
    "atlas_git_status",
    "atlas_registry_scan",
    "atlas_memory_find",
    "atlas_notes_related",
    "atlas_inbox_review",
}


def test_required_read_tools_are_registered_with_stable_schemas() -> None:
    registered = {definition.name for definition in iter_tool_definitions()}

    assert READ_TOOLS.issubset(registered)
    for name in READ_TOOLS:
        definition = get_tool_definition(name)
        assert definition.access == "read"
        assert definition.input_schema["additionalProperties"] is False


def test_read_tools_work_in_isolated_fixture(atlas_env: Path) -> None:
    assert call_tool("atlas_install", {"profile": "default", "confirm_write": True})["ok"]

    assert call_tool("atlas_status", {})["ok"]
    assert call_tool("atlas_help", {"topic": "context"})["ok"]
    assert call_tool("atlas_config_profile_list", {})["ok"]
    assert call_tool("atlas_config_profile_show", {"profile": "default"})["ok"]
    assert call_tool("atlas_config_ranked_show", {})["ok"]
    assert call_tool("atlas_context_ranked_groups", {})["ok"]
    assert call_tool("atlas_git_status", {"selectors": ["@all"]})["ok"]
    assert call_tool("atlas_registry_scan", {})["ok"]
    assert call_tool("atlas_memory_find", {"query": "nothing-here"})["ok"]
    assert call_tool("atlas_inbox_review", {})["ok"]


def test_invalid_profile_returns_structured_error(atlas_env: Path) -> None:
    result = call_tool("atlas_config_profile_show", {"profile": "missing"})

    assert result["ok"] is False
    assert result["error"]["kind"] in {"not_found", "validation_error"}


def test_missing_ranked_group_returns_structured_error(atlas_env: Path) -> None:
    assert call_tool("atlas_install", {"profile": "default", "confirm_write": True})["ok"]

    result = call_tool("atlas_context_ranked_repos", {"scope": "missing-group"})

    assert result["ok"] is False
    assert result["error"]["kind"] in {"not_found", "validation_error"}


def test_render_options_do_not_create_snapshot_for_missing_scope(atlas_env: Path) -> None:
    assert call_tool("atlas_install", {"profile": "default", "confirm_write": True})["ok"]
    before = sorted((atlas_env / "config" / "atlas_once" / "cache").rglob("*"))

    result = call_tool("atlas_context_ranked", {"scope": "missing-group", "portion": 50})

    after = sorted((atlas_env / "config" / "atlas_once" / "cache").rglob("*"))
    assert result["ok"] is False
    assert before == after
