# Atlas Once MCP

Atlas Once ships a first-class MCP server named `atlas-mcp`. It exposes Atlas through
discoverable tools with JSON schemas while keeping the `atlas` CLI as the canonical
implementation surface.

## Tools

Read tools are safe defaults for agents:

- `atlas_status`
- `atlas_help`
- `atlas_config_profile_list`
- `atlas_config_profile_show`
- `atlas_config_ranked_show`
- `atlas_context_ranked_groups`
- `atlas_context_ranked_repos`
- `atlas_context_ranked`
- `atlas_context_ranked_tree`
- `atlas_context_ranked_cache`
- `atlas_git_status`
- `atlas_registry_scan`
- `atlas_memory_find`
- `atlas_notes_related`
- `atlas_inbox_review`

Write tools are controlled and require `confirm_write=true`:

- `atlas_install`
- `atlas_config_profile_use`
- `atlas_config_ranked_install`
- `atlas_config_ranked_group_add`
- `atlas_memory_add`
- `atlas_note_create`
- `atlas_inbox_promote`
- `atlas_context_ranked_warm`
- `atlas_mcp_config_install`

There is no raw shell tool, arbitrary file writer, direct config editor, or tool that bypasses
Atlas installer/config commands.

## Security Model

The MCP adapter uses an explicit allowlist of Atlas operations and invokes the in-process
`atlas --json` command dispatcher with structured arguments. It does not execute shell strings.
Read tools may update Atlas-managed caches only when the existing Atlas command already owns that
cache behavior. Write tools mutate only through Atlas-managed installer, config, note, inbox, and
ranked-context paths.

Configuration is installer-only: use `atlas config mcp install`, `atlas config ranked install`,
`atlas config profile use`, or the matching MCP tools. Do not manually mutate Atlas config files.

## Profiles

`default` remains generic. `nshkrdotcom` remains the installed default profile for this
distribution. Personal paths, owners, ranked groups, and local defaults belong in the named
profile/template layer, not core MCP code.

## Resources And Prompts

The server exposes read-only resources for install/profile docs, CLI reference, agent onboarding,
profile metadata, current status, and the MCP tool registry. It also exposes prompts for agent
onboarding, ranked context usage, safe writes, and Codex CLI setup.

## Troubleshooting

Inspect the server and generated config:

```bash
atlas-mcp --help
atlas mcp tools --json
atlas mcp doctor --json
atlas config mcp show --json
atlas config mcp doctor --json
```

Regenerate the repo-owned MCP client snippet:

```bash
atlas config mcp install
```

That command also installs the repo-owned `atlas-codex` skill asset under the Atlas-managed MCP
config directory for client onboarding.
