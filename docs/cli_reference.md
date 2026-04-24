# CLI Reference

## Global Form

```bash
atlas [--json] <command> ...
```

Use `--json` for machine-readable output.

## Core Commands

```bash
atlas
atlas help <topic>
atlas menu
atlas install [--profile <name>] [--shell-setup] [--shell-target <path>] [--print-shell]
atlas config ...
atlas status
atlas next
atlas resolve <ref>
atlas init [--scan]
```

## Config

```bash
atlas config show
atlas config set <data_home|code_root|review_window_days|auto_sync_relationships> <value>
atlas config roots add <path>
atlas config roots remove <path>
atlas config profile list
atlas config profile show <name>
atlas config profile current
atlas config profile use <name>
atlas config shell show [--profile <name>]
atlas config shell install [--profile <name>] [--target <path>]
atlas config ranked path
atlas config ranked show
atlas config ranked install [--profile <name>] [--force]
atlas config ranked group add <group> <ref[:variant]>... [--variant <variant>] [--force]
```

## Registry

```bash
atlas registry scan [--changed-only]
atlas registry list [--owner <self|external|unknown>] [--language <name>] [--relation <primary|fork|external|unknown>]
atlas registry show <ref>
atlas registry resolve <ref>
atlas registry root-add <path>
atlas registry root-remove <path>
atlas registry alias-add <ref> <alias>
atlas registry alias-remove <ref> <alias>
```

## Notes And Memory

```bash
atlas today [--print]
atlas capture [--project <ref>] [--kind <kind>] [--tag <tag>] [--stdin] [text...]
atlas review inbox [--date YYYYMMDD]
atlas review daily [--date YYYYMMDD]
atlas promote entry <id> [--kind <kind>] [--title <title>] [--project <ref>]
atlas promote auto [--date YYYYMMDD]
atlas note new <title> [--kind <kind>] [--project <ref>] [--tag <tag>] [--body <text>] [--body-stdin]
atlas note find <query...>
atlas note open [query...] [--print]
atlas note sync [path...]
atlas related <path> [--limit N]
```

## Context

```bash
atlas context notes [--pwd-only] [-o <file>] <path>
atlas context repo <project-ref-or-path> [group] [-o <file>]
atlas context stack [--group <group>] [--remember] [-o <file>] <items...>
atlas context ranked groups [--names]
atlas context ranked repos <group> [--names]
atlas context ranked prepare <group>
atlas context ranked status <group>
atlas context ranked <group> [-o <file>] [--wait-fresh-ms N] [--ttl-ms N] [--allow-stale|--no-allow-stale]
atlas context ranked tree <group> [--include <prefix>] [--all] [--max-depth N] [--wait-fresh-ms N] [--ttl-ms N] [--allow-stale|--no-allow-stale]
```

`atlas context stack --remember` stores presets under:

```text
~/.atlas_once/presets/context_stack.json
```

Context JSON manifests include:

- `bundle_path`
- `bytes`
- `approx_tokens`
- `file_count`
- `included_files`
- `source_roots`
- `cache_key`

Ranked context `status` also exposes the prepared manifest with repo and project summaries.
Repo summaries can include `unmatched_project_overrides` when configured project names lag behind repo layout changes.
Ranked render and status auto-prepare the group when the prepared manifest is missing, stale, or points at deleted files; explicit `prepare` is still available for prepared-manifest prewarming. Ranked preparation queries the watcher-maintained Dexterity index with a bounded timeout and falls back to deterministic local `lib/` file selection when the query is unavailable; it does not run `dexterity.index`. The ranked query timeout defaults to 3 seconds and can be overridden with `ATLAS_ONCE_RANKED_QUERY_TIMEOUT_SECONDS`. Ranked JSON payloads include `auto_prepared`, `auto_prepare_reason`, and `index_freshness` with fresh/stale/warming/error counts, wait timing, and per-project freshness rows. The default `--wait-fresh-ms 0` does not block rendering. Freshness is based on the current source snapshot versus the indexed source snapshot; elapsed time alone does not make an unchanged index stale.

## Elixir Code Intelligence

These commands are meant to be run from inside a Mix repo. They default to `--project .`, use Atlas shadow workspaces, and keep Dexter/Dexterity state out of source repos.

```bash
atlas agent
atlas agent status [--project <ref-or-path>]
atlas agent task [--project <ref-or-path>] [--active <file>] [--mentioned <file>] [--edited <file>] [--limit N] [--symbol-limit N] [--max-terms N] [--token-budget N] [--include-external] <goal...>
atlas agent find [--project <ref-or-path>] [--limit N] <query...>
atlas agent def [--project <ref-or-path>] <Module> [function] [arity]
atlas agent refs [--project <ref-or-path>] <Module> [function] [arity]
atlas agent related [--project <ref-or-path>] [--limit N] [--mentioned <file>] [--edited <file>] [--include-external] [active-file]
atlas agent impact [--project <ref-or-path>] [--token-budget N] [--limit N] [--include-external] <file>
atlas agent map [--project <ref-or-path>] [--active <file>] [--mentioned <file>] [--edited <file>] [--limit N] [--token-budget N]
atlas index
atlas index here [project-ref-or-path]
atlas def [--project <ref-or-path>] <Module> [function] [arity]
atlas refs [--project <ref-or-path>] <Module> [function] [arity]
atlas symbols [--project <ref-or-path>] [--limit N] <query...>
atlas files [--project <ref-or-path>] [--limit N] <sql-like-pattern>
atlas ranked-files [--project <ref-or-path>] [--active <file>] [--mentioned <file>] [--edited <file>] [--include-prefix <prefix>] [--exclude-prefix <prefix>] [--include-external] [--overscan-limit N] [--limit N]
atlas ranked-symbols [--project <ref-or-path>] [--active <file>] [--mentioned <file>] [--include-external] [--limit N]
atlas impact [--project <ref-or-path>] [--token-budget N] [--limit N] [--include-external] <file>
atlas blast [--project <ref-or-path>] [--depth N] <file>
atlas cochanges [--project <ref-or-path>] [--limit N] <file>
atlas exports [--project <ref-or-path>] [--limit N]
atlas unused-exports [--project <ref-or-path>] [--limit N]
atlas test-only-exports [--project <ref-or-path>] [--limit N]
atlas repo-map [--project <ref-or-path>] [--active <file>] [--mentioned <file>] [--edited <file>] [--limit N] [--token-budget N]
atlas dexter [--project <ref-or-path>] lookup <Module> [function] [--strict] [--no-follow-delegates]
atlas dexter [--project <ref-or-path>] refs <Module> [function]
atlas dexter [--project <ref-or-path>] init [--force]
atlas dexter [--project <ref-or-path>] reindex [file]
atlas intelligence status
atlas intelligence start
atlas intelligence warm [--project <ref-or-path>] [ref-or-path...]
atlas intelligence stop
atlas intelligence serve
```

`atlas agent ...` is the preferred UX for Codex and other shell-driving agents. It keeps the command surface short, infers the current Mix repo, defaults to repo-source filtering, and returns next commands in JSON under `data.agent.next_commands` or `data.next_commands`. `atlas agent task "<goal>"` starts with a cheap implementation-first repo-structure scan, then adds bounded Dexterity enrichment when the goal contains useful search terms or the user supplies active files. If a backend query times out or returns malformed JSON, `agent task` still exits successfully with partial context and records the issue under `data.backend_errors`. `agent find` also falls back to repo-structure module matches when Dexterity fails. It deliberately avoids `repo-map` by default because full maps are higher-latency and can be noisy; use `atlas agent map` explicitly when a complete map is needed.

`atlas def <Module>` uses raw Dexter lookup because module-only definition is a Dexter primitive. `atlas def <Module> <function> [arity]` uses `mix dexterity.query definition`.

All JSON responses keep the normal Atlas envelope and include `data.project.repo_root`, `data.project.shadow_root`, tool metadata, index metadata, and the mapped result. Code-intelligence queries skip synchronous indexing when the indexed source snapshot still matches the current source snapshot; manual `atlas index` still forces a refresh. Backend metadata includes retry attempts, `data.tool.cached`, and `data.tool.cache` with enabled/hit/stored/index-stamp fields for read-only query cache behavior. Known transient Dexterity store-lock failures are retried with bounded backoff.

Atlas serializes code-intelligence access per shadow workspace. Lower-level commands use `ATLAS_ONCE_INTELLIGENCE_LOCK_TIMEOUT_SECONDS`. Agent commands use `ATLAS_ONCE_AGENT_LOCK_TIMEOUT_SECONDS` and `ATLAS_ONCE_AGENT_QUERY_TIMEOUT_SECONDS` so a stuck backend reports health failure instead of silently queuing forever. The default agent query budget matches the service request budget, 30 seconds, and the default agent lock budget is 10 seconds. Set `ATLAS_ONCE_INTELLIGENCE_CACHE=0` to disable read-only query caching.

`atlas intelligence start` launches an optional Atlas daemon that keeps a bounded lazy pool of Dexterity MCP workers. Code-intelligence commands, including `atlas agent ...`, use it when it is running and the query maps to a Dexterity MCP tool; subprocess fallback remains available when the service is disabled or unavailable. `atlas intelligence warm <ref-or-path>...` starts/reuses workers for selected active repos so the first semantic query can avoid backend startup latency. It still respects the global worker cap and LRU eviction; it does not warm every configured repo. `data.tool.transport` reports `mcp_service`, `subprocess`, or `cache`. `data.tool.service` reports worker metadata when the service is used. Worker count is capped by `ATLAS_ONCE_INTELLIGENCE_SERVICE_MAX_WORKERS`, idle workers expire after `ATLAS_ONCE_INTELLIGENCE_SERVICE_IDLE_TTL_SECONDS`, and timed-out or errored workers are closed and removed from the pool. Service timeouts are reported as backend failures instead of falling through to a second subprocess timeout. Set `ATLAS_ONCE_INTELLIGENCE_SERVICE=0` to force subprocess fallback.

`symbols` sorts primary implementation paths before config, support, tests, examples, docs, other, and external paths. `symbols` and `refs` JSON include `data.result_groups` with those categories while preserving the existing `data.result` shape. `atlas files <pattern>` uses Dexterity first and falls back to an implementation-first source scan when the backend returns no file matches; fallback metadata is exposed under `data.fallback`.

`ranked-files`, `ranked-symbols`, and `impact` default to repo-source results. They hide external absolute paths, `_build`, `deps`, `.elixir_ls`, and vendored dependency paths such as `examples/*/deps/*` from `data.result`; `data.raw` keeps the unfiltered backend payload. Add `--include-external` to preserve backend results in `data.result`.

## Ranked Context Flow

Recommended:

```bash
atlas context ranked groups
atlas context ranked repos <group>
atlas --json context ranked status <group>
atlas context ranked <group>
atlas context ranked tree <group>
```

Use `atlas context ranked groups` to inspect configured group summaries without preparing context. Use `--names` for a one-name-per-line list. Use `atlas context ranked repos <group>` to inspect the repo labels, paths, variants, strategies, and project override counts that a group resolves to. It also supports `--names`.
Use `atlas context ranked prepare <group>` when you want to prewarm the prepared manifest explicitly. Keep Dexterity indexes warm with `atlas index start` or `atlas index refresh`. Normal render/status/tree auto-prepare.
Use `atlas context ranked tree <group>` when you need the monorepo-aware file tree for the same ranked repo set before deciding which files to render or inspect. The ranked group chooses repos, but tree output includes source projects even when ranked content selection excluded them for budget/policy reasons. By default it includes implementation-first prefixes such as `lib`, `test`, `tests`, `src`, `config`, and `priv`, walks all files under those prefixes, and skips generated/dependency directories such as `_build`, `deps`, `.git`, and `node_modules`. Repeat `--include <prefix>` to narrow the tree, pass `--all` to show all non-skipped source paths, and cap traversal explicitly with `--max-depth`.

Packaged `nshkrdotcom` examples:

```bash
atlas registry scan
atlas --json context ranked groups
atlas --json context ranked repos gn-ten
atlas --json context ranked status gn-ten
atlas --json context ranked gn-ten --wait-fresh-ms 1200
atlas --json context ranked tree gn-ten
```

`gn-ten` is the default personal workspace sample and expands to:

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

`gn-ten` is a packaged ranked config entry, not a hard-coded CLI branch. The `nshkrdotcom` template also defines per-repo variants named `gn-ten` for monorepos that need custom nested project policy. New groups can reuse those variants with `<ref>:gn-ten` or use the repo default variant.

Reimport the repo-owned packaged ranked config after upgrading Atlas Once:

```bash
atlas config ranked install --profile nshkrdotcom --force
```

Helper commands on `PATH`:

```bash
ctx
mixctx
mctx
mcc
```

Use these config helpers:

```bash
atlas config ranked path
atlas config ranked show
atlas config ranked group add my-slice app_kit:gn-ten jido_integration:gn-ten AITrace
atlas config ranked group add my-slice app_kit jido_integration --variant default
```

## Maintenance

```bash
atlas snapshot <name> -- <command...>
atlas index rebuild [--changed-only]
atlas index
atlas index here [project-ref-or-path]
atlas index status [--project <ref-or-path>] [--all] [--ttl-ms N]
atlas index start [--poll-interval-ms N] [--debounce-ms N] [--ttl-ms N] [--project <ref-or-path>]
atlas index watch [--once|--daemon] [--poll] [--poll-interval-ms N] [--debounce-ms N] [--ttl-ms N] [--project <ref-or-path>]
atlas index refresh [--project <ref-or-path>] [--all] [--ttl-ms N] [--wait-fresh-ms N]
atlas index stop [--force]
atlas intelligence status
atlas intelligence start
atlas intelligence stop
atlas prune snapshots [--days N] [--apply]
atlas find <query...>
atlas open [query...] [--print]
```

`atlas index start` launches the polling watcher in the background and logs to `~/.atlas_once/logs/index-watcher.log`. `atlas index watch --once` performs one polling pass and exits. `atlas index watch --daemon` runs the foreground polling loop used by `index start` and external supervisors. The watcher uses source snapshots, not a wall-clock expiry, to decide whether a project needs reindexing.
`atlas --json index stop` requests clean shutdown, then escalates if the process tree does not exit. It reports `signal_sent`, `force_escalated`, and `stopped`; treat only `stopped: true` as a completed shutdown. Use `atlas index stop --force` for immediate hard-stop recovery.

Turn the watcher off:

```bash
atlas index stop
atlas index stop --force
```

`atlas config shell install` installs a bash snippet that runs `atlas intelligence start` and `atlas index start` when the shell loads. Set `ATLAS_ONCE_SHELL_AUTOSTART=0` before sourcing the snippet to opt out.

## Helper Commands

These are still installed as companion utilities:

```bash
docday
today
memadd
memfind
memopen
memsnap
session-close
atlas-index
atlas-related
atlas-prune
```
