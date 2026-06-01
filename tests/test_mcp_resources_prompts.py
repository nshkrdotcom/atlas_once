from __future__ import annotations

from atlas_once.mcp.prompts import get_prompt, list_prompts
from atlas_once.mcp.resources import list_resources


def test_resource_list_includes_required_read_only_resources() -> None:
    resources = list_resources()
    uris = {resource.uri for resource in resources}

    assert "atlas://docs/install-and-profiles" in uris
    assert "atlas://docs/cli-reference" in uris
    assert "atlas://docs/agent-onboarding" in uris
    assert "atlas://profiles/default" in uris
    assert "atlas://profiles/nshkrdotcom" in uris
    assert "atlas://config/status" in uris
    assert "atlas://mcp/tools" in uris
    assert all(resource.read_only for resource in resources)


def test_prompt_list_includes_required_agent_prompts() -> None:
    names = {prompt.name for prompt in list_prompts()}

    assert "atlas_agent_onboarding" in names
    assert "atlas_ranked_context_usage" in names
    assert "atlas_safe_write_policy" in names
    assert "atlas_codex_cli_setup" in names


def test_prompt_text_contains_installer_only_rule() -> None:
    text = get_prompt("atlas_safe_write_policy").text

    assert "Never edit Atlas config files directly" in text
    assert "installer" in text.lower()
    assert "atlas_install" in text


def test_prompts_do_not_embed_host_specific_absolute_paths() -> None:
    for prompt in list_prompts():
        assert "~/p/g/n" not in prompt.text
        assert "jido_brainstorm" not in prompt.text
