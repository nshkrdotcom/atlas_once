# Feature Checklist

## Core Platform

- [x] Canonical `atlas` CLI for primary workflows
- [x] Profile-based install and config seeding
- [x] Config root under `~/.config/atlas_once`
- [x] State root under `~/.atlas_once`
- [x] Append-only event log at `~/.atlas_once/events.jsonl`
- [x] Mutation locks under `~/.atlas_once/locks`
- [x] Stable JSON envelope for automation

## Registry

- [x] Multi-root repo scanning
- [x] Ref and alias resolution
- [x] Owner-scope and fork classification
- [x] Language inventory and primary-language detection
- [x] Repo capability detection for context strategies
- [x] Registry state written under `registry/repos.json`, `registry/projects.json`, and `registry/meta.json`

## Context Bundles

- [x] Notes bundle generation
- [x] Repo bundle generation
- [x] Explicit and remembered stack bundles
- [x] Stack preset storage under `~/.atlas_once/presets/context_stack.json`
- [x] Bundle cache under `~/.atlas_once/cache/bundles`

## Ranked Contexts V3

- [x] Single supported ranked schema version: `3`
- [x] Selector-driven groups with root scoping
- [x] Explicit repo groups with reusable repo variants
- [x] Per-project Mix overrides with `top_files`, `top_percent`, and `exclude`
- [x] Budget-first ranked selection with `max_bytes` and `max_tokens`
- [x] Priority-tier project ordering with `priority_tier`
- [x] Candidate filtering with `exclude_path_prefixes` and `exclude_globs`
- [x] Classification-aware nested Mix project discovery
- [x] Default exclusion of legacy, test, fixture, example, support, temp, dependency, doc, bench, and vendor trees
- [x] Atlas-managed shadow workspaces under `~/.atlas_once/code/shadows`
- [x] No Dexterity state written into source repos
- [x] Per-repo prepared manifest cache under `~/.atlas_once/cache/ranked_contexts/repos`
- [x] Per-group prepared manifests with repo and project summaries
- [x] Ranked render/status auto-prepare missing, stale, or deleted-file manifests
- [x] `atlas context ranked groups [--names]` ranked group summaries without preparation
- [x] `atlas context ranked repos <group> [--names]` resolved repo/variant summaries without preparation
- [x] `atlas context ranked tree <group>` monorepo-aware source tree for the prepared ranked repo set
- [x] Ranked prepare/render/tree consume watcher-maintained indexes and fall back to local `lib/` selection instead of running synchronous `dexterity.index`
- [x] `atlas config ranked group add ...` helper for explicit group creation
- [x] Deterministic fallback when Dexterity returns no ranked files
- [x] Non-fatal reporting of stale project overrides via progress warnings and `unmatched_project_overrides`
- [x] Ranked JSON freshness metadata via `index_freshness`

## Realtime Indexing

- [x] `atlas index status` JSON control-plane status
- [x] `atlas index watch --once` polling pass
- [x] `atlas index start` background watcher launcher
- [x] `atlas index watch --daemon` foreground polling loop
- [x] `atlas index refresh` manual project refresh
- [x] `atlas index stop [--force]` stop/recovery command
- [x] Watcher state under `~/.atlas_once/index_watcher`
- [x] Shared Atlas-managed shadow workspaces for watcher and ranked prepare
- [x] Cold-start indexing even after status-only mtime observation
- [x] Deterministic source-snapshot freshness; unchanged repos do not become stale by age
- [x] Status reads cannot hide dirty source from the watcher queue

## Fleet Control And Prompt Workflows

- [x] Registry-backed repo model normalization for fleet commands
- [x] JSON alternate manifest support for fleet repo selection
- [x] Selectors for `@all`, `@group:<name>`, refs/aliases, path globs, exclusions, `@dirty`, `@unpushed`, and `@stale`
- [x] `atlas git status` cache reads and `--refresh` foreground probes
- [x] Git-health cache under `~/.atlas_once/git_health`
- [x] Git-health task state exposed in `atlas --json index status`
- [x] Background git-health refresh attached to `atlas index start|stop|status`, without `atlas git watch`
- [x] `atlas prompt-run-sdk ... --dry-run` target resolution and planned run records
- [x] Workflow run history under `~/.atlas_once/workflows/runs`
- [x] Workflow preset list/show/upsert/run and status commands
- [ ] Non-JSON alternate manifest formats
- [ ] Prompt-runner real-run target injection beyond SDK packet behavior
- [x] SDK-owned prompt-runner preflight gate before Atlas real provider runs
- [x] Atlas `--preflight-only` and `--skip-preflight` workflow knobs with persisted preflight records
- [ ] Explicit setup workflow knobs for packet setup commands

## Agent Code Intelligence

- [x] Repo-local `atlas index` indexes the current Mix repo through an Atlas shadow root
- [x] `atlas def`, `atlas refs`, `atlas symbols`, and `atlas files`
- [x] `atlas ranked-files`, `atlas ranked-symbols`, `atlas impact`, `atlas blast`, and `atlas cochanges`
- [x] `atlas exports`, `atlas unused-exports`, and `atlas test-only-exports`
- [x] `atlas repo-map`
- [x] `atlas agent status|task|find|def|refs|related|impact|map` short UX for shell-driving agents
- [x] `atlas agent task "<goal>"` composes freshness, symbols, ranked files, optional impact context, and next commands without using full repo-map by default
- [x] `atlas agent task "<goal>"` returns cheap repo-structure context first and partial results when Dexterity times out or returns invalid JSON
- [x] Agent commands use the persistent intelligence service when available, with backend-sized query timeout and bounded lock timeout
- [x] `atlas intelligence warm <ref-or-path>...` prewarms selected service workers under the same cap/LRU policy
- [x] `atlas files <pattern>` falls back to implementation-first source scanning when backend file matching returns no hits
- [x] Raw `atlas dexter lookup|refs|init|reindex` through the same shadow policy
- [x] JSON responses expose repo root, shadow root, tool command, index metadata, retry attempts, cache metadata, freshness skip status, filters, result groups, and result
- [x] Query commands skip synchronous indexing when watcher state says the project is fresh
- [x] Dexterity-backed commands serialize access per shadow workspace, queue behind active per-shadow work, and retry known transient store-lock failures
- [x] Read-only code-intelligence commands cache successful results against the shadow index stamp
- [x] Optional persistent intelligence service with bounded lazy Dexterity MCP workers
- [x] Timed-out or errored persistent intelligence workers are closed and removed from the pool
- [x] `symbols` ranks implementation results ahead of examples/tests and `symbols`/`refs` expose grouped results
- [x] `ranked-files`, `ranked-symbols`, and `impact` default to repo-source results with `--include-external` as an escape hatch
- [x] Real source repos remain free of `.dexter.db`, `.dexterity`, and Atlas lock state

## Memory And Notes

- [x] Capture inbox
- [x] Review inbox and daily review
- [x] Promotion into durable notes
- [x] Backlink generation
- [x] Related-note generation
- [x] Project, tag, relationship, and link indexes

## Public Surface Cleanup

- [x] Legacy `ctx`, `mixctx`, `mctx`, and `mcc` command entrypoints preserved in installs
- [x] Install output and shell snippet no longer advertise removed compatibility commands
- [x] Ranked docs updated to the v3 model and current group examples
