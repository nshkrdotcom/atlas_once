from __future__ import annotations

import json
from pathlib import Path

import pytest

from atlas_once.atlas import main


def test_config_mcp_show_json_returns_valid_server_config(atlas_env: Path, capsys) -> None:
    assert main(["--json", "config", "mcp", "show"]) == 0

    payload = json.loads(capsys.readouterr().out)
    data = payload["data"]
    assert payload["ok"] is True
    assert data["server_name"] == "atlas-once"
    assert data["server"]["command"] == "atlas-mcp"
    assert data["profile"] == "nshkrdotcom"
    assert data["codex"]["add_command"] == [
        "codex",
        "mcp",
        "add",
        "atlas-once",
        "--env",
        "ATLAS_ONCE_PROFILE=nshkrdotcom",
        "--",
        "atlas-mcp",
    ]


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
    assert payload["data"]["next_steps"]


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


def test_config_mcp_doctor_clean_path_reports_not_ready(
    atlas_env: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", str(atlas_env / "empty-bin"))

    assert main(["--json", "config", "mcp", "doctor"]) == 0

    data = json.loads(capsys.readouterr().out)["data"]
    assert data["ready"] is False
    assert data["server"]["executable"]["available"] is False
    assert data["server"]["executable"]["path"] is None
    assert any("uv tool install" in step["command"] for step in data["next_steps"])


def test_config_mcp_doctor_ignores_checkout_venv_executable(
    atlas_env: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout_bin = Path(__file__).resolve().parents[1] / ".venv" / "bin"
    monkeypatch.setenv("PATH", str(checkout_bin))

    assert main(["--json", "config", "mcp", "doctor"]) == 0

    data = json.loads(capsys.readouterr().out)["data"]
    assert data["ready"] is False
    assert data["server"]["executable"]["available"] is False
    assert data["server"]["executable"]["source"] == "checkout_venv"


def test_config_mcp_doctor_reports_codex_registration_when_cli_agrees(
    atlas_env: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_bin = atlas_env / "fake-bin"
    fake_bin.mkdir()
    atlas_mcp = fake_bin / "atlas-mcp"
    atlas_mcp.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    atlas_mcp.chmod(0o755)
    codex = fake_bin / "codex"
    codex.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"mcp\" ] && [ \"$2\" = \"get\" ] && [ \"$3\" = \"atlas-once\" ]; then\n"
        "  echo 'atlas-once'\n"
        "  echo '  enabled: true'\n"
        "  echo '  command: atlas-mcp'\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    codex.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin))
    assert main(["install", "--profile", "nshkrdotcom"]) == 0
    capsys.readouterr()

    assert main(["--json", "config", "mcp", "doctor"]) == 0

    data = json.loads(capsys.readouterr().out)["data"]
    assert data["server"]["executable"]["available"] is True
    assert data["codex"]["available"] is True
    assert data["codex"]["registered"] is True
    assert data["profile"]["ready"] is True
    assert data["ready"] is True
