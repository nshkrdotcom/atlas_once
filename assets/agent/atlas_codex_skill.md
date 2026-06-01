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
