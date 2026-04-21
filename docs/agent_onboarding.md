# Agent Onboarding

## Command Contract

Use `atlas --json ...` for automation whenever possible.

JSON responses use the stable envelope:

- `schema_version`
- `ok`
- `command`
- `exit_code`
- `data`
- `errors`

Atlas also writes an append-only event log at `~/.atlas_once/events.jsonl`.

## Recommended Startup Flow

Resolve environment state first:

```bash
atlas --json status
atlas --json next
atlas --json resolve <ref>
atlas --json registry scan
```

To inspect every configured root, read:

```bash
atlas --json config show
```

Use `data.settings.project_roots`.

If you need repo discovery details:

```bash
atlas --json registry list
atlas --json registry show <ref>
```

## Context Flows

Single repo:

```bash
atlas --json context repo <ref> current
```

Explicit or remembered stack:

```bash
atlas --json context stack <item>...
```

Ranked multi-repo code context:

```bash
atlas --json registry scan
atlas --json index status
atlas --json index refresh --project <ref>
atlas --json context ranked status <group>
atlas --json context ranked <group>
atlas --json context ranked tree <group>
```

For the packaged `nshkrdotcom` profile, default automation should treat `gn-ten` as the primary workspace group unless the user asks for a different slice. Use `atlas --json context ranked tree gn-ten` when an agent needs repo/project shape for the same ranked set before choosing files; tree JSON includes per-repo and per-project nodes, defaults to source/test/config prefixes, and skips generated or dependency directories.

Legacy helper commands remain installed:

- `ctx`
- `mixctx` / `mctx`
- `mcc`

Render, status, and tree auto-prepare when the prepared manifest is missing, stale, or points at deleted files. Use `atlas --json context ranked prepare <group>` only when explicitly prewarming. Ranked JSON includes `auto_prepared`, `auto_prepare_reason`, and `index_freshness`; agents should inspect freshness before deciding whether to refresh, wait, or proceed with stale context. The default render path uses `--wait-fresh-ms 0`, so it does not block on indexing unless the caller requests a bounded wait.

## Repo-Local Elixir Navigation

When the working directory is an Elixir Mix repo, prefer the short agent surface before grepping:

```bash
atlas --json agent status
atlas --json agent task "add streaming support"
atlas --json agent find Agent
atlas --json agent def ClaudeAgentSDK.Agent
atlas --json agent refs ClaudeAgentSDK.Agent
atlas --json agent related lib/claude_agent_sdk/agent.ex
atlas --json agent impact lib/claude_agent_sdk/agent.ex
```

`atlas agent task "<goal>"` is the normal first command for a coding task. It returns deterministic freshness, a cheap implementation-first repo-structure summary, likely files, selected symbol searches when the goal has useful terms, optional impact context for active/edited files, and concrete next `atlas agent ...` commands. Backend calls use the persistent intelligence service when it is running and use the backend service timeout by default; if Dexterity times out or returns invalid JSON, the command keeps the repo-structure context and records the problem under `data.backend_errors`. `atlas agent find <query>` uses the same fallback for module matches. It does not call the full repo map by default; use `atlas agent map` only when the task specifically needs that broader, slower view.

The lower-level commands remain available when a specific primitive is useful:

```bash
atlas --json index
atlas --json symbols Agent --limit 10
atlas --json def ClaudeAgentSDK.Agent
atlas --json def ClaudeAgentSDK.Agent new 1
atlas --json refs ClaudeAgentSDK.Agent
atlas --json ranked-files --active lib/claude_agent_sdk/agent.ex --limit 10
atlas --json impact lib/claude_agent_sdk/agent.ex --token-budget 5000
atlas --json repo-map --active lib/claude_agent_sdk/agent.ex --limit 10
atlas --json files lib --limit 20
```

Atlas skips synchronous indexing for these query commands when the indexed source snapshot still matches the current source snapshot. Read-only code-intelligence calls cache successful backend results against the current shadow index stamp; check `data.tool.cache.hit` to see whether a response avoided a backend call. `atlas files <pattern>` falls back to an implementation-first source scan when Dexterity returns no matches, with metadata under `data.fallback`. Ranked and impact commands default to repo-source output, filtering stdlib, `_build`, `deps`, and vendored dependency paths from `data.result`; use `--include-external` when dependency context is the target. `data.raw` remains available for backend debugging.

For `symbols` and `refs`, prefer `data.result_groups` when planning edits. It groups hits into implementation, config, support, tests, examples, docs, other, and external buckets while keeping `data.result` compatible with existing automation.

For lower-level repeated code-intelligence sessions, the persistent query service is optional:

```bash
atlas --json intelligence start
atlas --json intelligence warm .
atlas --json intelligence warm snakebridge snakepit citadel
atlas --json intelligence status
```

When it is running, mapped Dexterity queries, including `atlas agent ...`, may report `data.tool.transport == "mcp_service"` and include `data.tool.service.worker`. Atlas uses a bounded lazy worker pool: no Dexterity worker exists until a shadow is queried or explicitly warmed, idle workers are evicted, the default global cap is four workers, and timed-out workers are closed and removed from the pool. `atlas intelligence warm` is for selected active repos only; do not use it to warm every configured repo. Service timeouts are reported once as backend health failures instead of falling through to a second subprocess timeout. Subprocess fallback remains available when the service is disabled or unavailable.

From outside the repo, add `--project <ref-or-path>`:

```bash
atlas --json agent find Agent --project ~/p/g/n/claude_agent_sdk
```

Use raw Dexter through Atlas when an exact module lookup or direct Dexter reference query is the right primitive:

```bash
atlas --json dexter lookup ClaudeAgentSDK.Agent
atlas --json dexter refs ClaudeAgentSDK.Agent
```

Use Dexterity-backed Atlas commands for ranked files, ranked symbols, impact context, blast radius, cochanges, file/symbol search, and export analysis.

## Index Freshness Controls

Use these commands for the ranked Elixir index control plane:

```bash
atlas --json index watch --once
atlas --json index start
atlas --json index watch --daemon
atlas --json index status
atlas --json index refresh --project <ref>
atlas --json index stop
atlas --json intelligence start
atlas --json intelligence status
atlas --json intelligence stop
```

`index start` launches the watcher in the background. `watch --daemon` is the foreground long-running polling process used by `index start` and external supervisors. Freshness is source-snapshot based; elapsed time alone does not make an unchanged repo stale. `atlas config shell install` installs bash autostart for `atlas intelligence start` and `atlas index start`; set `ATLAS_ONCE_SHELL_AUTOSTART=0` to opt out.

## Ranked Config Model

Only ranked config version `3` is supported.

Top-level shape:

- `version`
- `defaults`
- `repos`
- `groups`

Relevant ranked fields for automation:

- `defaults.runtime.dexterity_root`
- `defaults.runtime.dexter_bin`
- `defaults.runtime.shadow_root`
- `defaults.project_discovery`
- `groups[].selectors[].roots`
- `repos[].variants`
- `repos[].variants[].projects`
- `max_bytes`
- `max_tokens`
- `priority_tier`
- `exclude_path_prefixes`
- `exclude_globs`

Prepared ranked manifests include:

- selected files
- source roots
- repo count
- project count
- selection mode
- consumed bytes
- consumed token estimate
- repo manifest paths
- per-repo summaries
- per-repo `unmatched_project_overrides` when configured project names no longer match the live repo layout
- per-project category, exclusion reason, selected count, selected bytes, selected token estimate, fallback usage, priority tier, and shadow root

## Ranking Behavior

- Elixir repos use Dexterity per Mix project.
- Ranking is limited to `lib/`.
- Default project discovery excludes `_legacy`, tests, fixtures, examples, support, tmp, dist, deps, docs, bench, and vendor trees.
- Budget enforcement happens after candidate production. `top_files` / `top_percent` can narrow the candidate pool, then `max_bytes` / `max_tokens` trim the final prepared selection.
- Lower `priority_tier` is higher priority under repo budget pressure.
- If Dexterity returns no ranked files, Atlas falls back to lexicographic `lib/**.{ex,exs}` order.
- Dexterity state lives in Atlas-managed shadow workspaces, not in source repos.
- Repo-local `atlas def`, `atlas refs`, `atlas symbols`, `atlas ranked-files`, `atlas impact`, `atlas repo-map`, and `atlas dexter ...` commands use the same shadow policy.
- Missing project overrides are warnings, not fatal prepare failures. Agents should inspect `unmatched_project_overrides` from `status` if drift matters for the task.
- Repo cache hits are rejected when a previously selected file disappeared, so a fresh `prepare` rebuilds that repo before render.

## Storage Paths

Config:

- `~/.config/atlas_once/ranked_contexts.json`
- `~/.config/atlas_once/ranked_contexts.state.json`

State:

- `~/.atlas_once/cache/ranked_contexts`
- `~/.atlas_once/cache/ranked_contexts/repos`
- `~/.atlas_once/code/shadows`
- `~/.atlas_once/index_watcher/state.json`
- `~/.atlas_once/registry/repos.json`
- `~/.atlas_once/registry/projects.json`
- `~/.atlas_once/events.jsonl`
