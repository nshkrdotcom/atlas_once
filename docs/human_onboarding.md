# Human Onboarding

## First Run

Install Atlas Once and seed the active profile:

```bash
uv tool install git+https://github.com/nshkrdotcom/atlas_once
atlas install
```

Optional shell helper:

```bash
atlas config shell install
```

## Check Your Setup

```bash
atlas config show
atlas registry scan
atlas registry list
atlas status
atlas next
```

If your repos live outside the current roots:

```bash
atlas config roots add ~/code
atlas registry scan
```

To see every configured root later:

```bash
atlas --json config show
```

Look at `data.settings.project_roots`.

## Ranked Code Context

Find the managed ranked config:

```bash
atlas config ranked path
atlas config ranked show
atlas --help-full
```

Render the packaged workspace group:

```bash
atlas registry scan
atlas index watch --once
atlas context ranked groups
atlas context ranked repos gn-ten
atlas --json context ranked status gn-ten
atlas context ranked gn-ten
atlas context ranked tree gn-ten
```

Use `atlas context ranked groups --names` when you only want configured group names. Use `atlas context ranked repos gn-ten --names` when you simply want the repo names in `gn-ten`.
Use `atlas context ranked tree gn-ten` to see the file tree for the same ten repos without rendering file contents. It is useful for large monorepos because Atlas groups discovered projects and defaults to relevant source/test/config directories while skipping build and dependency output.
Use `atlas context ranked <path> --portion 50` when you want the same ranked engine against an ad-hoc workspace root without creating a new managed group.

For the packaged `nshkrdotcom` defaults, `gn-ten` is the primary workspace group and covers:

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

`gn-ten` comes from the packaged `nshkrdotcom` ranked config, not a hard-coded CLI branch. The template also has monorepo-specific `gn-ten` repo variants for repos that need nested project budgets, priorities, or excludes. New explicit groups can reuse those variants with `<ref>:gn-ten`.

If you pull a newer Atlas Once version and want the shipped defaults from this repo to replace the managed ranked config, run:

```bash
atlas config ranked install --force
```

Add a simple group without hand-editing JSON:

```bash
atlas config ranked group add my-slice app_kit:gn-ten jido_integration:gn-ten AITrace
```

Edit the config if you want selectors, different repo variants, or different per-project budget/priority overrides:

```bash
nano "$(atlas config ranked path)"
```

Key ranked behaviors:

- Elixir ranking works per Mix project.
- `atlas index watch`, `atlas index refresh`, and `atlas index status` keep/show ranked index freshness.
- `atlas index start` launches the watcher in the background; `atlas index watch --daemon` is the foreground loop for supervisors.
- The same watcher lifecycle keeps fleet git-health cache warm. Inspect it with `atlas git status @all --json` or `atlas --json index status`.
- `atlas index stop` turns it off cleanly and escalates if needed; in JSON, only `stopped: true` means the process exited. `atlas index stop --force` hard-stops and clears stale process markers.
- Default discovery excludes fixtures, tests, examples, support code, legacy trees, and temp trees.
- Budget-first fields are first class: `max_bytes`, `max_tokens`, and `priority_tier`.
- Dexterity state is kept under `~/.atlas_once/code/shadows`, not inside your repos.
- Ranked render/status auto-prepare missing or stale prepared manifests. JSON includes `auto_prepared`, `auto_prepare_reason`, and `index_freshness`; normal rendering does not wait unless `--wait-fresh-ms` is set. Ranked preparation queries watcher-maintained indexes and falls back to local `lib/` file selection instead of running `dexterity.index` itself.
- Atlas compares current source snapshots to indexed source snapshots. An unchanged repo stays fresh regardless of index age.
- If a configured project override stops matching the live repo layout, prepare/render warns and `status` records the stale names under `unmatched_project_overrides`.

Fleet prompt dry-runs use repo selectors and create workflow records without provider side effects:

```bash
atlas prompt-run-sdk foo-prompt simulated . --targets atlas_once --dry-run --json
atlas workflow preset list
atlas workflow status <run-id> --json
```

Real prompt-runner runs need packet readiness first. Prompt Runner packets may require packet-local repos or workspaces to exist before `run` can execute. Check that in the SDK layer:

```bash
cd ~/p/g/n/prompt_runner_sdk
mix prompt_runner packet preflight <packet-dir>
mix prompt_runner packet doctor <packet-dir>
mix prompt_runner plan <packet-dir>
```

Run any packet-documented setup command explicitly. Atlas dry-runs do not execute setup scripts or providers. Atlas real runs call the SDK-owned preflight gate before invoking a provider; `--preflight-only` records readiness without running the provider, and `--skip-preflight` is an explicit escape hatch.

## Elixir Repo Commands

From inside a Mix repo, the fastest path is:

```bash
atlas agent status
atlas agent task "add streaming support"
atlas agent find Agent
atlas agent def ClaudeAgentSDK.Agent
atlas agent refs ClaudeAgentSDK.Agent
atlas agent related lib/claude_agent_sdk/agent.ex
atlas agent impact lib/claude_agent_sdk/agent.ex
```

The lower-level commands are still available for direct debugging:

```bash
atlas index
atlas symbols Agent --limit 10
atlas files lib --limit 20
atlas def ClaudeAgentSDK.Agent
atlas refs ClaudeAgentSDK.Agent
atlas ranked-files --active lib/claude_agent_sdk/agent.ex --limit 10
atlas impact lib/claude_agent_sdk/agent.ex --token-budget 5000
```

These commands use Atlas-managed shadow indexes, so source repos do not get `.dexter.db` or `.dexterity` state. Query commands use the source-snapshot freshness record to avoid unnecessary synchronous indexing when the repo is already fresh. `atlas agent task "<goal>"` is the compact agent-friendly entrypoint and returns implementation-first repo structure, likely files, symbols when useful, freshness, and next commands without requiring long flags. `atlas files <pattern>` falls back to an implementation-first source scan when Dexterity returns no file matches. Agent queries use the persistent intelligence service when it is running and use the backend service timeout by default; if Dexterity is slow, task/find commands keep structure context and report `backend_errors` instead of hanging. Ranked and impact commands default to repo-source results; add `--include-external` when you intentionally want stdlib or dependency paths. Add `--project <ref-or-path>` when running from another directory.

For repeated Elixir code navigation in one work session, start the optional persistent query service:

```bash
atlas intelligence start
atlas intelligence warm .
atlas intelligence status
```

It uses one Atlas daemon and a small lazy pool of Dexterity MCP workers. It does not start a worker for every repo; it starts workers only for repos you query or explicitly warm and stops idle workers.

## Memory Workflow

Capture:

```bash
atlas capture --project <ref> --kind decision --stdin
```

Review:

```bash
atlas review inbox
atlas review daily
```

Promote:

```bash
atlas promote auto
```

Notes:

```bash
atlas note new "Routing notes" --project <ref> --body-stdin
atlas note find routing daemon
atlas note sync
```

## Where Things Live

Config:

- `~/.config/atlas_once`

State:

- `~/.atlas_once`
- `~/.atlas_once/cache/ranked_contexts`
- `~/.atlas_once/code/shadows`
- `~/.atlas_once/index_watcher`
- `~/.atlas_once/events.jsonl`

Durable data:

- `~/atlas_once` by default, or the active profile data root
