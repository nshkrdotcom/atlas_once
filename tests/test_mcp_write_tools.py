from __future__ import annotations

import hashlib
from pathlib import Path

from atlas_once.config import get_paths
from atlas_once.mcp.tools import call_tool, get_tool_definition

WRITE_TOOLS = {
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


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_required_write_tools_are_registered() -> None:
    for name in WRITE_TOOLS:
        definition = get_tool_definition(name)
        assert definition.access == "write"


def test_atlas_install_is_idempotent_for_managed_config(atlas_env: Path) -> None:
    first = call_tool("atlas_install", {"profile": "nshkrdotcom", "confirm_write": True})
    paths = get_paths()
    tracked = [paths.settings_path, paths.profile_state_path, paths.ranked_contexts_path]
    first_hashes = {path: _file_hash(path) for path in tracked}

    second = call_tool("atlas_install", {"profile": "nshkrdotcom", "confirm_write": True})
    second_hashes = {path: _file_hash(path) for path in tracked}

    assert first["ok"] is True
    assert second["ok"] is True
    assert first_hashes == second_hashes


def test_atlas_install_supports_default_generic_profile(atlas_env: Path) -> None:
    result = call_tool("atlas_install", {"profile": "default", "confirm_write": True})

    assert result["ok"] is True
    assert result["data"]["profile"]["name"] == "default"


def test_write_tools_reject_arbitrary_output_paths(atlas_env: Path) -> None:
    result = call_tool(
        "atlas_note_create",
        {
            "title": "Unsafe",
            "body": "body",
            "output_path": "/tmp/unsafe.md",
            "confirm_write": True,
        },
    )

    assert result["ok"] is False
    assert result["error"]["kind"] == "validation_error"


def test_write_tools_reject_unknown_profiles(atlas_env: Path) -> None:
    result = call_tool("atlas_install", {"profile": "missing", "confirm_write": True})

    assert result["ok"] is False
    assert result["error"]["kind"] in {"not_found", "validation_error"}


def test_memory_write_stays_under_managed_fixture_roots(atlas_env: Path) -> None:
    assert call_tool("atlas_install", {"profile": "default", "confirm_write": True})["ok"]

    result = call_tool(
        "atlas_memory_add",
        {
            "text": "remember this",
            "tags": ["memory"],
            "confirm_write": True,
        },
    )

    paths = get_paths()
    created = Path(result["data"]["entry"]["source_path"]).resolve()
    assert result["ok"] is True
    assert created.is_relative_to(paths.data_home)


def test_mcp_modules_do_not_directly_target_path_home() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "atlas_once" / "mcp"

    offenders = [
        path
        for path in root.rglob("*.py")
        if "Path.home(" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
