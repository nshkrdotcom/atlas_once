# Codex CLI MCP Setup

Install Atlas Once:

```bash
uv tool install git+https://github.com/nshkrdotcom/atlas_once
atlas install --profile nshkrdotcom
```

Generate the Atlas-owned MCP client snippet:

```bash
atlas config mcp install
atlas config mcp show
atlas config mcp doctor
```

The generated snippet uses the stable `atlas-mcp` command, not a source checkout path. Add that
snippet to Codex or another MCP-capable client using the client’s MCP configuration flow.
The same installer command also writes the repo-owned `atlas-codex` skill under Atlas-managed MCP
config so the skill travels with package installs.

Test the server:

```bash
atlas-mcp --help
atlas mcp doctor
atlas mcp tools --json
```

Recommended Codex instructions:

- Prefer Atlas MCP tools over shell commands for Atlas workflows.
- Use read tools before write tools.
- Use write tools only when explicitly asked and pass `confirm_write=true`.
- Do not edit Atlas config directly; use installer/config commands.
- Use `nshkrdotcom` as the installed default profile for this user.
- Keep core Atlas behavior generic and keep the `default` profile host-neutral.
