from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_mcp_docs_mention_required_commands_and_rules() -> None:
    combined = "\n".join(
        [
            _read("docs/mcp.md"),
            _read("docs/codex_cli_mcp.md"),
            _read("docs/agent_mcp_usage.md"),
        ]
    )

    assert "atlas-mcp" in combined
    assert "atlas config mcp install" in combined
    assert "codex mcp add atlas-once" in combined
    assert "uv tool install git+https://github.com/nshkrdotcom/atlas_once" in combined
    assert "uv tool install --reinstall /path/to/atlas_once" in combined
    assert "uv run atlas" in combined
    assert "does not install `atlas-mcp` for Codex" in combined
    assert "installer-only" in combined.lower()
    assert "nshkrdotcom" in combined
    assert "default" in combined
    assert "Codex" in combined


def test_agent_skill_asset_exists_and_has_safe_write_policy() -> None:
    text = _read("assets/agent/atlas_codex_skill.md")

    assert "Atlas Once Codex Skill" in text
    assert "Use Atlas MCP tools before shell commands" in text
    assert "Never directly edit Atlas config files" in text


def test_codex_skill_folder_is_repo_owned() -> None:
    skill = ROOT / "assets" / "agent" / "atlas-codex" / "SKILL.md"
    packaged_skill = (
        ROOT / "src" / "atlas_once" / "mcp_assets" / "agent" / "atlas-codex" / "SKILL.md"
    )

    text = skill.read_text(encoding="utf-8")
    assert "name: atlas-codex" in text
    assert "Atlas MCP" in text
    assert packaged_skill.read_text(encoding="utf-8") == text


def test_docs_do_not_instruct_manual_atlas_config_mutation() -> None:
    docs = [
        ROOT / "docs" / "mcp.md",
        ROOT / "docs" / "codex_cli_mcp.md",
        ROOT / "docs" / "agent_mcp_usage.md",
    ]
    forbidden = ["> ~/.config/atlas_once", "vim ~/.config/atlas_once"]

    for path in docs:
        text = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in text
