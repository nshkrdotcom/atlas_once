# Agent MCP Usage

Agents should treat Atlas MCP as the primary structured interface for Atlas Once.

Atlas must be installed as a tool before Codex can start `atlas-mcp`:

```bash
uv tool install git+https://github.com/nshkrdotcom/atlas_once
atlas install --profile nshkrdotcom
codex mcp add atlas-once --env ATLAS_ONCE_PROFILE=nshkrdotcom -- atlas-mcp
```

For local checkout development, use:

```bash
uv tool install --reinstall /path/to/atlas_once
```

`uv run atlas ...` does not install `atlas-mcp` for Codex.

Start with:

```text
atlas_status
atlas_config_profile_list
atlas_context_ranked_groups
atlas_git_status
atlas_registry_scan
```

For ranked context, inspect before rendering:

```text
atlas_context_ranked_groups
atlas_context_ranked_repos
atlas_context_ranked_tree
atlas_context_ranked
```

Use render budgets such as `portion`, `max_tokens`, and `max_bytes` when a full context bundle is
not needed.

For the packaged `nshkrdotcom` profile, use `gn-twelve` as the primary ranked group. It preserves
the earlier policies and includes Synapse through its managed, budgeted variant.

Writes are installer-only and schema-gated. Use `confirm_write=true` only when the user asks for a
write:

```text
atlas_install
atlas_config_profile_use
atlas_config_ranked_install
atlas_memory_add
atlas_note_create
atlas_mcp_config_install
```

Do not request or simulate raw shell access through MCP. Do not directly modify Atlas config files.
Configuration and client setup must go through `atlas config ...` commands or their MCP tool
equivalents.

`atlas config mcp install` also writes the packaged `atlas-codex` skill asset under Atlas-managed
MCP config for reusable Codex onboarding.
