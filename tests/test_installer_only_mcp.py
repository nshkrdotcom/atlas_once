from __future__ import annotations

import json
from pathlib import Path

from atlas_once.atlas import main
from atlas_once.mcp.tools import call_tool


def test_mcp_write_tools_cannot_write_to_arbitrary_absolute_paths(atlas_env: Path) -> None:
    result = call_tool(
        "atlas_memory_add",
        {
            "text": "unsafe",
            "path": "/tmp/atlas-unsafe.md",
            "confirm_write": True,
        },
    )

    assert result["ok"] is False
    assert result["error"]["kind"] == "validation_error"


def test_mcp_config_install_only_writes_under_managed_fixture_roots(
    atlas_env: Path,
    capsys,
) -> None:
    assert main(["--json", "config", "mcp", "install"]) == 0

    payload = json.loads(capsys.readouterr().out)
    path = Path(payload["data"]["path"])
    assert path.is_relative_to(atlas_env / "config")
    assert not list((atlas_env / "home").rglob("*"))


def test_no_mcp_module_directly_creates_home_config_paths() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "atlas_once" / "mcp"
    offenders: list[str] = []

    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "Path.home(" in text or "~/.config/atlas_once" in text:
            offenders.append(str(path.relative_to(root)))

    assert offenders == []


def test_no_docs_instruct_manual_atlas_config_mutation() -> None:
    root = Path(__file__).resolve().parents[1]
    docs = [
        root / "docs" / "mcp.md",
        root / "docs" / "codex_cli_mcp.md",
        root / "docs" / "agent_mcp_usage.md",
    ]

    for path in docs:
        text = path.read_text(encoding="utf-8")
        assert "echo " not in text or "~/.config/atlas_once" not in text
        assert "cp " not in text or "~/.config/atlas_once" not in text
