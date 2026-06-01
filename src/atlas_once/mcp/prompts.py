from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PromptDefinition:
    name: str
    description: str
    text: str


PROMPTS: tuple[PromptDefinition, ...] = (
    PromptDefinition(
        "atlas_agent_onboarding",
        "Onboard an MCP-capable agent to Atlas Once.",
        """Use Atlas MCP tools as the first Atlas interface.

Read before writing: atlas_status, atlas_config_profile_list, atlas_context_ranked_groups,
atlas_git_status, and atlas_registry_scan.

Use write tools only when the user asks for a write. Keep the default profile generic.
For this distribution, nshkrdotcom is the installed default profile and host-specific values
belong only in that named profile/template layer.
""",
    ),
    PromptDefinition(
        "atlas_ranked_context_usage",
        "Guide an agent through ranked-context discovery and rendering.",
        """Start with atlas_context_ranked_groups, then atlas_context_ranked_repos for a selected
scope. Use atlas_context_ranked_tree for a cheap file view before rendering content with
atlas_context_ranked. Prefer render options such as portion, max_tokens, and max_bytes over
creating new configuration.
""",
    ),
    PromptDefinition(
        "atlas_safe_write_policy",
        "State the Atlas MCP installer-only write policy.",
        """Never edit Atlas config files directly.

Use installer-backed MCP tools such as atlas_install, atlas_config_profile_use,
atlas_config_ranked_install, atlas_config_ranked_group_add, and atlas_mcp_config_install.
Every write tool requires confirm_write=true. Do not request raw shell access, arbitrary file
writes, or direct config-file mutation.
""",
    ),
    PromptDefinition(
        "atlas_codex_cli_setup",
        "Explain how Codex CLI should connect to Atlas MCP.",
        """Install Atlas, then generate the repo-owned MCP config snippet with
atlas config mcp install and inspect it with atlas config mcp show. Configure Codex or another
MCP-capable client to run atlas-mcp. Prefer MCP tools over shell commands for Atlas workflows,
read first, and use write tools only when the user explicitly asks for mutation.
""",
    ),
)

PROMPTS_BY_NAME = {prompt.name: prompt for prompt in PROMPTS}


def list_prompts() -> tuple[PromptDefinition, ...]:
    return PROMPTS


def get_prompt(name: str) -> PromptDefinition:
    return PROMPTS_BY_NAME[name]
