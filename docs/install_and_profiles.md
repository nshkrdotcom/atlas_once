# Install And Profiles

## Recommended Install

```bash
uv tool install git+https://github.com/nshkrdotcom/atlas_once
atlas install
```

Use the CLI directly after install:

```bash
atlas
```

Optional shell helper setup:

```bash
atlas config shell install
```

That installs the managed `d` helper for `docday`.

## Packaged Profiles

Atlas Once currently ships:

- `default`
- `nshkrdotcom`

Inspect them:

```bash
atlas config profile list
atlas config profile show default
atlas config profile show nshkrdotcom
```

Install with a specific profile:

```bash
atlas install --profile default
atlas install --profile nshkrdotcom
```

Switch later:

```bash
atlas config profile use default
```

The installed default remains `nshkrdotcom`.

## Ranked Config Seeding

`atlas install` and `atlas config profile use <name>` both seed the managed ranked-context config for the active profile.
The shipped config is repo-owned. If the packaged defaults change, reapply them with `atlas config ranked install --force` instead of hand-copying local edits.

Useful commands:

```bash
atlas config ranked path
atlas config ranked show
atlas config ranked install --force
atlas context ranked groups
atlas context ranked repos gn-ten
atlas config ranked group add my-slice app_kit:gn-ten AITrace
```

The shipped `nshkrdotcom` template now seeds:

- `owned-elixir-all` for self-owned primary Elixir repos under `~/p/g/n`
- `gn-ten` for the opinionated ten-repo workspace slice

`gn-ten` is the personal default sample config and currently expands to:

- `app_kit`
- `extravaganza`
- `mezzanine`
- `outer_brain`
- `citadel`
- `jido_integration`
- `execution_plane`
- `ground_plane`
- `stack_lab`
- `AITrace`

The ranked defaults are budget-first:

- byte budget via `max_bytes`
- estimated token budget via `max_tokens`
- project ordering via `priority_tier`
- Weld-aware project selection for the large monorepos that publish projected artifacts

`gn-ten` is not a special case in the CLI. It is a managed group from the packaged `nshkrdotcom` ranked config. The same template defines `gn-ten` variants for monorepos that need custom nested project controls. New groups can reuse those variants with refs like `citadel:gn-ten` and `jido_integration:gn-ten`; refs without a suffix use the default repo variant.

Typical ranked flow after install:

```bash
atlas registry scan
atlas index watch --once
atlas context ranked groups --names
atlas context ranked repos gn-ten
atlas --json context ranked status gn-ten
atlas context ranked gn-ten
atlas context ranked tree gn-ten
```

If the repos moved since the last scan, rerun `atlas registry scan` before rendering.

For active development sessions, run `atlas index start`, then use `atlas --json index status` to inspect freshness. The watcher is polling-based and compares current source snapshots to indexed source snapshots; unchanged repos stay fresh regardless of elapsed time. `atlas --json context ranked <group>`, `atlas --json context ranked status <group>`, and `atlas --json context ranked tree <group>` auto-prepare missing or stale prepared manifests and report `auto_prepared`, `auto_prepare_reason`, and `index_freshness`; pass `--wait-fresh-ms <N>` when you want a bounded freshness wait before rendering. Ranked preparation does not run `dexterity.index`; it queries the watcher-maintained index with a bounded timeout and falls back to local `lib/` file selection if the query is unavailable. The tree command shows the same ranked repo set as a monorepo-aware file tree, includes source projects even when ranked file selection excluded them for budget/policy reasons, defaults to directories such as `lib/`, `test/`, `tests/`, `src/`, `config/`, and `priv/`, and walks all files under those prefixes unless `--max-depth` is set.

Repo-local Elixir navigation works after the same install and ranked config setup:

```bash
cd ~/p/g/n/claude_agent_sdk
atlas agent status
atlas agent task "add streaming support"
atlas agent find Agent
atlas agent related lib/claude_agent_sdk/agent.ex
atlas index
atlas symbols Agent --limit 10
atlas files lib --limit 20
atlas def ClaudeAgentSDK.Agent
atlas ranked-files --active lib/claude_agent_sdk/agent.ex --limit 10
```

Atlas runs Dexter and Dexterity through shadow workspaces under `~/.atlas_once/code/shadows`, not through `.dexter.db` in the source repo. `atlas agent ...` is the short shell-friendly surface for Codex-style use; `atlas agent task "<goal>"` combines freshness, implementation-first repo structure, bounded backend enrichment, optional impact context, and next commands without long argument lists. Query commands reuse source-snapshot freshness state when available, serialize Dexterity access per shadow, cache successful read-only results against the current shadow index stamp, and filter ranked/impact output to repo-source paths by default. `atlas files <pattern>` falls back to an implementation-first source scan when Dexterity returns no matches. Agent commands use the persistent intelligence service when it is running, use the backend service query budget by default, and return partial task context when a backend call fails. Use `--include-external` when stdlib or dependency paths are intentionally needed.

For long sessions, start the optional persistent query service:

```bash
atlas intelligence start
atlas intelligence warm .
atlas intelligence status
```

This starts one Atlas daemon with a bounded lazy Dexterity MCP worker pool. It does not start workers for every configured repo; workers are created only for queried or explicitly warmed shadows, expire after the idle TTL, and are closed/removed if a request times out or errors. Use `atlas intelligence warm <ref-or-path>...` only for selected active repos.

If the repo-owned template changed in this repo checkout, reimport it into the managed config with:

```bash
atlas config ranked install --profile nshkrdotcom --force
```

If a monorepo layout drifts, `prepare` now warns and continues. The unmatched override names are preserved in `atlas --json context ranked status gn-ten` under `unmatched_project_overrides`.

If you want to inspect or customize the managed config:

```bash
nano "$(atlas config ranked path)"
```

For a simple explicit group, prefer the CLI helper over hand-editing JSON:

```bash
atlas config ranked group add my-slice app_kit:gn-ten jido_integration:gn-ten AITrace
```

## Shell Setup

Atlas installs normal commands on `PATH`. The shell snippet is only for `d`, because changing directories must happen in the current shell process.

Show the snippet:

```bash
atlas config shell show
```

Install it:

```bash
atlas config shell install
```

## Customize Paths

Show effective settings:

```bash
atlas config show
```

Adjust roots and storage:

```bash
atlas config set data_home ~/atlas_once
atlas config set code_root ~/code
atlas config roots add ~/code
atlas config roots remove ~/code
```

List all active roots:

```bash
atlas --json config show
```

The root list is `data.settings.project_roots`.

## Dev Checkout Mode

For local development:

```bash
git clone https://github.com/nshkrdotcom/atlas_once
cd atlas_once
uv sync --dev
uv run atlas
```
