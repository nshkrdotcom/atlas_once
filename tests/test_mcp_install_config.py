from __future__ import annotations

import json
from pathlib import Path

from atlas_once.atlas import main


def test_config_mcp_show_json_returns_valid_server_config(atlas_env: Path, capsys) -> None:
    assert main(["--json", "config", "mcp", "show"]) == 0

    payload = json.loads(capsys.readouterr().out)
    data = payload["data"]
    assert payload["ok"] is True
    assert data["server_name"] == "atlas-once"
    assert data["server"]["command"] == "atlas-mcp"
    assert data["profile"] == "nshkrdotcom"


def test_config_mcp_install_json_writes_only_managed_fixture_config(
    atlas_env: Path,
    capsys,
) -> None:
    assert main(["--json", "config", "mcp", "install"]) == 0

    payload = json.loads(capsys.readouterr().out)
    path = Path(payload["data"]["path"])
    skill_path = Path(payload["data"]["codex_skill_path"])
    assert payload["ok"] is True
    assert path.is_file()
    assert (skill_path / "SKILL.md").is_file()
    assert path.is_relative_to(atlas_env / "config")
    assert skill_path.is_relative_to(atlas_env / "config")
    assert not path.is_relative_to(atlas_env / "home")


def test_config_mcp_install_is_idempotent(atlas_env: Path, capsys) -> None:
    assert main(["--json", "config", "mcp", "install"]) == 0
    first = json.loads(capsys.readouterr().out)["data"]

    assert main(["--json", "config", "mcp", "install"]) == 0
    second = json.loads(capsys.readouterr().out)["data"]

    assert first["path"] == second["path"]
    assert first["codex_skill_path"] == second["codex_skill_path"]
    assert second["changed"] is False


def test_config_mcp_install_uses_console_command_not_checkout_path(
    atlas_env: Path,
    capsys,
) -> None:
    assert main(["--json", "config", "mcp", "install", "--client", "codex"]) == 0

    data = json.loads(capsys.readouterr().out)["data"]
    config = json.loads(Path(data["path"]).read_text(encoding="utf-8"))
    server = config["mcpServers"]["atlas-once"]
    assert server["command"] == "atlas-mcp"
    assert "/src/" not in json.dumps(server)


def test_config_mcp_profile_can_be_selected_explicitly(atlas_env: Path, capsys) -> None:
    assert main(["--json", "config", "mcp", "show", "--profile", "default"]) == 0

    data = json.loads(capsys.readouterr().out)["data"]
    assert data["profile"] == "default"
    assert data["server"]["env"]["ATLAS_ONCE_PROFILE"] == "default"


def test_config_mcp_does_not_mutate_global_client_config_by_default(
    atlas_env: Path,
    capsys,
) -> None:
    assert main(["--json", "config", "mcp", "install"]) == 0

    capsys.readouterr()
    assert not list((atlas_env / "home").rglob("*"))
