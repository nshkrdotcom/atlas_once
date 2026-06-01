from __future__ import annotations

from pathlib import Path

import pytest

from atlas_once.mcp.adapter import AtlasMcpCall, AtlasMcpError, call_atlas


def test_adapter_can_call_read_only_atlas_command(atlas_env: Path) -> None:
    result = call_atlas(AtlasMcpCall(command="atlas_status", args={}))

    assert result["ok"] is True
    assert result["command"] == "atlas_status"
    assert "storage" in result["data"]


def test_adapter_normalizes_known_atlas_errors(atlas_env: Path) -> None:
    result = call_atlas(
        AtlasMcpCall(
            command="atlas_context_ranked_repos",
            args={"scope": "missing-group"},
        )
    )

    assert result["ok"] is False
    assert result["error"]["kind"] in {"not_found", "validation_error"}
    assert result["errors"]


def test_adapter_rejects_unknown_command_names(atlas_env: Path) -> None:
    with pytest.raises(AtlasMcpError):
        call_atlas(AtlasMcpCall(command="atlas_missing", args={}))


def test_adapter_rejects_arbitrary_shell_strings(atlas_env: Path) -> None:
    with pytest.raises(AtlasMcpError):
        call_atlas(AtlasMcpCall(command="atlas_status; rm -rf /", args={}))


def test_adapter_does_not_write_to_home_when_fixture_overrides_env(atlas_env: Path) -> None:
    call_atlas(AtlasMcpCall(command="atlas_status", args={}))

    home = atlas_env / "home"
    assert not list(home.rglob("*"))
