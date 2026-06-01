---
name: atlas-codex
description: Use when Codex is working in an environment with Atlas Once available through MCP or the atlas CLI, especially for status discovery, ranked context, project registry inspection, memory/note capture, profile setup, or Codex CLI MCP onboarding. Prefer schema-backed Atlas MCP tools and installer-managed configuration over shell recipes or direct config-file edits.
---

# Atlas Codex

## Workflow

Start with read-only MCP tools:

- `atlas_status`
- `atlas_config_profile_list`
- `atlas_context_ranked_groups`
- `atlas_context_ranked_repos`
- `atlas_context_ranked_tree`
- `atlas_git_status`
- `atlas_registry_scan`

Use context tools before rendering large bundles. Call `atlas_context_ranked_groups`, then
`atlas_context_ranked_repos`, then `atlas_context_ranked_tree`, and only then
`atlas_context_ranked` with a budget such as `portion`, `max_tokens`, or `max_bytes`.

## Writes

Use write tools only when the user explicitly asks for mutation, and always pass
`confirm_write=true`. Write tools must go through Atlas-owned commands:

- `atlas_install`
- `atlas_config_profile_use`
- `atlas_config_ranked_install`
- `atlas_config_ranked_group_add`
- `atlas_memory_add`
- `atlas_note_create`
- `atlas_inbox_promote`
- `atlas_context_ranked_warm`
- `atlas_mcp_config_install`

Never edit Atlas config files directly. Never ask for arbitrary shell execution through MCP.
Use installer/config commands for profile, ranked-context, and MCP client setup.

## Profiles

Keep `default` generic. Treat `nshkrdotcom` as the installed default profile for this
distribution. Host-specific owners, paths, ranked groups, and personal defaults belong only in
the named profile/template layer.

## Codex Setup

Generate client configuration through Atlas:

```bash
atlas config mcp install
atlas config mcp show
atlas config mcp doctor
codex mcp add atlas-once --env ATLAS_ONCE_PROFILE=nshkrdotcom -- atlas-mcp
```

Connect Codex or another MCP-capable agent to the generated `atlas-mcp` server command.
`atlas-mcp` must be installed as a normal tool executable; `uv run atlas ...` does not install it
for Codex. For local checkout development use `uv tool install --reinstall /path/to/atlas_once`.
