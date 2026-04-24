# Architecture

Atlas Once has three storage layers and one canonical CLI.

## Layers

1. Data root
   Durable human-facing notes, inbox files, sessions, projects, decisions, people, topics, and snapshots.
2. Config root
   User config under `~/.config/atlas_once` by default.
3. State root
   Rebuildable operational state under `~/.atlas_once` by default.
4. CLI
   `atlas` is the primary interface for both humans and agents.

## Design Principles

- filesystem-first storage
- deterministic output for automation
- explicit prepared state for expensive context generation
- clean separation between durable notes and rebuildable indexes
- no source-repo pollution from ranking/indexing state

## Main Subsystems

### Profiles And Config

`atlas install` applies a packaged profile and seeds ranked-context config for that profile.

Packaged profiles currently include:

- `default`
- `nshkrdotcom`

Config is managed through `atlas config ...`.

For the packaged `nshkrdotcom` profile, the repo-owned ranked template seeds `gn-ten` as the primary personal workspace slice and `owned-elixir-all` as the broader selector-driven group. `gn-ten` is packaged config, not command code; the template also defines reusable `gn-ten` repo variants for large monorepos that need nested project policy. `atlas context ranked groups` and `atlas context ranked repos <group>` expose config summaries without preparing context. `atlas context ranked tree <group>` uses the same prepared ranked manifests as render/status to expose a monorepo-aware source tree for that repo set without rendering file contents. New explicit groups can be added through `atlas config ranked group add ...`. Reapplying the packaged template is an explicit import step via `atlas config ranked install --force`.

### Registry

`atlas registry` scans project roots and records:

- repo path
- aliases
- owner scope
- fork relation
- language inventory
- primary language
- strategy capabilities
- nested Mix project inventory

Registry state is written under:

```text
~/.atlas_once/registry/
  repos.json
  projects.json
  meta.json
```

### Context

`atlas context` builds bundles from:

- notes trees
- a single repo
- a saved or explicit stack of repos
- ranked repo groups

Ranked context is the multi-stage code-intelligence pipeline:

1. Resolve a named group into repo variants.
2. Discover rankable Mix projects or deterministic source files.
3. Check Atlas watcher state for soft-real-time index freshness.
4. For Elixir repos, index each included Mix project with Dexterity when preparing or explicitly refreshing.
5. Run ranking against an Atlas-managed shadow workspace.
6. Persist per-repo and per-group prepared manifests.
7. Render current file contents from the prepared manifest.

Prepared repo summaries preserve config drift explicitly. If a configured Mix project override no longer matches the live repo layout, Atlas keeps preparing the rest of the group and records the stale names as `unmatched_project_overrides`.

### Shadow Workspaces

Dexterity state is isolated under:

```text
~/.atlas_once/code/shadows/
```

Each shadow workspace mirrors one Mix project with real directories and symlinked source files. It owns local `.dexter.db`, `.dexterity/*`, and Atlas/Dexterity lock state. Source repos remain clean.

### Agent Code Intelligence

Atlas exposes short repo-local commands over Dexter and Dexterity:

- `atlas agent status`, `atlas agent task`, `atlas agent find`, `atlas agent def`, `atlas agent refs`, `atlas agent related`, and `atlas agent impact` are the preferred shell-driving agent surface.
- `atlas index` or `atlas index here` refreshes the current Mix project shadow index.
- `atlas def <Module>` and `atlas dexter lookup <Module>` use raw Dexter module lookup.
- `atlas def <Module> <function> [arity]` and `atlas refs ...` use Dexterity definition/reference queries.
- `atlas symbols`, `atlas files`, `atlas ranked-files`, `atlas ranked-symbols`, `atlas impact`, `atlas blast`, `atlas cochanges`, `atlas exports`, `atlas unused-exports`, `atlas test-only-exports`, and `atlas repo-map` expose Dexterity query and map surfaces.

Every command defaults to `--project .` for repo-local use, accepts `--project <ref-or-path>` for cross-repo use, and returns the normal Atlas JSON envelope when `--json` is present. `atlas agent task "<goal>"` starts with a deterministic implementation-first repo-structure scan that detects multi-Mix layouts and key modules before it asks Dexterity for anything. Dexterity enrichment is bounded and partial: symbol search, ranked files, and active-file impact context can fail independently while the command still returns useful structure, likely files, freshness, and next commands. It deliberately leaves the slower full map behind explicit `atlas agent map`. Tool metadata records the actual Dexter/Dexterity invocation, retry attempts, cache status, index skip status, and the shadow root used.

Code-intelligence commands share a per-shadow lock with the realtime watcher so indexing and querying do not race the same Dexterity store. Lower-level commands use `ATLAS_ONCE_INTELLIGENCE_LOCK_TIMEOUT_SECONDS`; agent commands use `ATLAS_ONCE_AGENT_LOCK_TIMEOUT_SECONDS` and `ATLAS_ONCE_AGENT_QUERY_TIMEOUT_SECONDS` so a stuck backend reports health failure instead of silently queuing forever. Agent queries default to the backend service query budget, currently 30 seconds, and a 10-second lock budget. Query commands consult watcher freshness state and skip synchronous indexing when the target source snapshot matches the indexed source snapshot; manual `atlas index` still forces refresh. Successful read-only Dexter/Dexterity queries are cached under `~/.atlas_once/code/query_cache` using a key that includes the project, backend command, and current shadow index stamp; `ATLAS_ONCE_INTELLIGENCE_CACHE=0` disables this cache. Ranked and impact commands default to repo-source results and keep unfiltered backend output under `data.raw`; `--include-external` exposes stdlib and dependency paths in `data.result` when needed. `symbols` and `refs` add `data.result_groups` for implementation/test/example/doc separation. `atlas files` uses Dexterity first and falls back to an implementation-first source scan when the backend returns no file matches.

The optional persistent intelligence service runs as one Atlas daemon controlled by `atlas intelligence start|status|warm|stop|serve`. It listens on a Unix socket under `~/.atlas_once/code/intelligence_service` and lazily starts Dexterity MCP subprocess workers for queried or explicitly warmed shadows. It does not run one worker per configured repo. `atlas intelligence warm <ref-or-path>...` prewarms selected active repos while respecting the same cap and LRU eviction. The pool is capped and idle workers are evicted, so a workspace with many registered repos only consumes persistent Dexterity workers for the repos actively queried in the current session. Timed-out or errored workers are quarantined by closing and removing them from the pool; a service timeout is reported once as backend health failure instead of falling through to a second subprocess timeout. If the service is unavailable, full subprocess fallback remains intact.

Ranked context render/status/tree auto-prepare their prepared manifests when missing, stale, or pointing at deleted files. `atlas context ranked groups` and `atlas context ranked repos <group>` are read-only summary commands and do not prepare manifests. Explicit `atlas context ranked prepare <group>` remains a prepared-manifest prewarm operation, not a required step before normal render or tree inspection. Ranked preparation queries the watcher-maintained Dexterity index with a bounded timeout and falls back to deterministic local `lib/` file selection when the query is unavailable; it does not run `mix dexterity.index` itself.

### Realtime Index Watcher

`atlas index` owns the soft-real-time freshness control plane for ranked Elixir indexes. The current implementation is a polling watcher, not an OS `inotify` watcher. It scans relevant Elixir source files (`mix.exs`, `.ex`, `.exs`) and records both the current source snapshot and the snapshot that Dexterity last indexed.

- `atlas index watch --once` performs a single polling pass.
- `atlas index start` launches the watcher loop in the background.
- `atlas index watch --daemon` runs the foreground watcher loop used by `index start` and external supervisors.
- `atlas index status` reports daemon, queue, retry, and per-project freshness state.
- `atlas index refresh` performs a manual synchronous refresh of selected projects.
- `atlas index stop [--force]` requests watcher shutdown or clears stale process state. Normal stop requests clean shutdown and escalates after the wait window. JSON stop payloads expose `signal_sent`, `force_escalated`, and `stopped`; `stopped` is only true after the watcher process is gone.

Watcher state is rebuildable operational state under:

```text
~/.atlas_once/index_watcher/
  state.json
  watcher.pid
  stop.json
```

Freshness is deterministic: elapsed wall-clock time does not make an unchanged repo stale. A project is fresh when the indexed source snapshot matches the current source snapshot, stale when source metadata changed after the last successful index, missing when no successful index is recorded, warming while an index is running, and error after a failed refresh. `age_ms` and `ttl_ms` are diagnostic/compatibility metadata, not code-change detection.

The watcher does not replace Dexterity or Dexter. Atlas schedules `mix dexterity.index` against shadow workspaces, and ranked preparation consumes that index with `mix dexterity.query ranked_files --json`. Index refresh is owned by `atlas index start`, `atlas index watch`, and `atlas index refresh`, not by ranked render.

### Capture, Review, And Promotion

`atlas capture`, `atlas review`, and `atlas promote` implement the inbox-to-memory flow.

### Note Graph

`atlas note`, `atlas related`, and index rebuild commands maintain:

- backlinks
- related-note suggestions
- project indexes
- tag indexes
- link indexes

## Storage Layout

Config:

```text
~/.config/atlas_once/
  settings.json
  profile.json
  ranked_contexts.json
  ranked_contexts.state.json
  shell/
```

State:

```text
~/.atlas_once/
  events.jsonl
  code/
    shadows/
  index_watcher/
    state.json
    watcher.pid
    stop.json
  registry/
    repos.json
    projects.json
    meta.json
  indexes/
    relationships.json
    projects.json
    tags.json
    links.json
  presets/
    context_stack.json
  cache/
    bundles/
    ranked_contexts/
      repos/
  locks/
```

Data root:

```text
<data-root>/
  docs/
  mem/
    inbox/
    sessions/
    projects/
    decisions/
    people/
    topics/
    snapshots/
```

## Ranked Config Model

Only ranked schema version `3` is supported.

The root object contains:

- `version`
- `defaults`
- `repos`
- `groups`

The most important ranked controls are:

- `defaults.runtime.shadow_root`
- `defaults.project_discovery`
- `groups[].selectors[].roots`
- repo `variants`
- per-project `top_files`, `top_percent`, `max_bytes`, `max_tokens`, `priority_tier`, and `exclude`
- per-repo `unmatched_project_overrides` in prepared summaries when repo layout drifts past packaged config

Installed helper commands such as `ctx`, `mixctx` / `mctx`, and `mcc` remain available for existing workflows, even though `atlas` is the canonical interface.
