# Atlas Once Codex Skill

Use Atlas MCP tools before shell commands.

Read-only first:

- `atlas_status`
- `atlas_config_profile_list`
- `atlas_context_ranked_groups`
- `atlas_context_ranked_repos`
- `atlas_context_ranked_tree`
- `atlas_context_ranked`
- `atlas_git_status`
- `atlas_registry_scan`

Writes only when explicitly requested:

- `atlas_install`
- `atlas_config_profile_use`
- `atlas_config_ranked_install`
- `atlas_config_ranked_group_add`
- `atlas_memory_add`
- `atlas_note_create`
- `atlas_inbox_promote`
- `atlas_context_ranked_warm`
- `atlas_mcp_config_install`

Never directly edit Atlas config files. Use Atlas installer/config commands only. Keep
`default` generic and keep `nshkrdotcom` as the installed default profile for this distribution.

Codex setup:

```bash
uv tool install git+https://github.com/nshkrdotcom/atlas_once
atlas install --profile nshkrdotcom
atlas config mcp install
codex mcp add atlas-once --env ATLAS_ONCE_PROFILE=nshkrdotcom -- atlas-mcp
atlas config mcp doctor --json
```

For local checkout development use `uv tool install --reinstall /path/to/atlas_once`. `uv run
atlas ...` does not install `atlas-mcp` for Codex.
