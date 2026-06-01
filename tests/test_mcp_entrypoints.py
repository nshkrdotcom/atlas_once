from __future__ import annotations

import json
import tomllib
from pathlib import Path

from atlas_once.atlas import main


def test_pyproject_exposes_atlas_mcp_console_script() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))

    assert payload["project"]["scripts"]["atlas-mcp"] == "atlas_once.mcp.server:main"


def test_atlas_mcp_help_works(atlas_env: Path, capsys) -> None:
    assert main(["mcp", "--help"]) == 0

    out = capsys.readouterr().out
    assert "atlas mcp" in out
    assert "serve" in out
    assert "tools" in out
    assert "doctor" in out


def test_atlas_mcp_tools_json_returns_envelope(atlas_env: Path, capsys) -> None:
    assert main(["--json", "mcp", "tools"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["command"] == "mcp.tools"
    assert payload["data"]["tool_count"] > 0
    assert "atlas_status" in {tool["name"] for tool in payload["data"]["tools"]}


def test_atlas_mcp_doctor_json_reports_install_context(atlas_env: Path, capsys) -> None:
    assert main(["--json", "mcp", "doctor"]) == 0

    payload = json.loads(capsys.readouterr().out)
    data = payload["data"]
    assert payload["ok"] is True
    assert payload["command"] == "mcp.doctor"
    assert data["mcp_available"] is True
    assert data["server"]["command"] == "atlas-mcp"
    assert data["tools"]["total"] > 0
    assert data["tools"]["read"] > 0
    assert data["tools"]["write"] > 0
    assert "config_home" in data["paths"]
