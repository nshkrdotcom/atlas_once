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
atlas context ranked prepare <group>
atlas context ranked status <group>
atlas context ranked <group> [-o <file>] [--wait-fresh-ms N] [--ttl-ms N] [--allow-stale|--no-allow-stale]
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
Ranked JSON payloads include `index_freshness` with fresh/stale/warming/error counts, wait timing, and per-project freshness rows. The default `--wait-fresh-ms 0` does not block rendering. Freshness is based on the current source snapshot versus the indexed source snapshot; elapsed time alone does not make an unchanged index stale.

## Elixir Code Intelligence

These commands are meant to be run from inside a Mix repo. They default to `--project .`, use Atlas shadow workspaces, and keep Dexter/Dexterity state out of source repos.

```bash
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
atlas intelligence stop
atlas intelligence serve
```

`atlas def <Module>` uses raw Dexter lookup because module-only definition is a Dexter primitive. `atlas def <Module> <function> [arity]` uses `mix dexterity.query definition`.

All JSON responses keep the normal Atlas envelope and include `data.project.repo_root`, `data.project.shadow_root`, tool metadata, index metadata, and the mapped result. Code-intelligence queries skip synchronous indexing when the indexed source snapshot still matches the current source snapshot; manual `atlas index` still forces a refresh. Backend metadata includes retry attempts, `data.tool.cached`, and `data.tool.cache` with enabled/hit/stored/index-stamp fields for read-only query cache behavior. Known transient Dexterity store-lock failures are retried with bounded backoff.

Atlas serializes code-intelligence access per shadow workspace. The default lock wait is tuned so normal parallel agent calls queue behind an active index/query instead of failing quickly; override it with `ATLAS_ONCE_INTELLIGENCE_LOCK_TIMEOUT_SECONDS` when needed. Set `ATLAS_ONCE_INTELLIGENCE_CACHE=0` to disable read-only query caching.

`atlas intelligence start` launches an optional Atlas daemon that keeps a bounded lazy pool of Dexterity MCP workers. Code-intelligence commands use it when it is running and the query maps to a Dexterity MCP tool; otherwise they fall back to the subprocess path. `data.tool.transport` reports `mcp_service`, `subprocess`, or `cache`. `data.tool.service` reports worker metadata when the service is used. Worker count is capped by `ATLAS_ONCE_INTELLIGENCE_SERVICE_MAX_WORKERS` and idle workers expire after `ATLAS_ONCE_INTELLIGENCE_SERVICE_IDLE_TTL_SECONDS`. Set `ATLAS_ONCE_INTELLIGENCE_SERVICE=0` to force subprocess fallback.

`symbols` sorts primary implementation paths before config, support, tests, examples, docs, other, and external paths. `symbols` and `refs` JSON include `data.result_groups` with those categories while preserving the existing `data.result` shape.

`ranked-files`, `ranked-symbols`, and `impact` default to repo-source results. They hide external absolute paths, `_build`, `deps`, `.elixir_ls`, and vendored dependency paths such as `examples/*/deps/*` from `data.result`; `data.raw` keeps the unfiltered backend payload. Add `--include-external` to preserve backend results in `data.result`.

## Ranked Context Flow

Recommended:

```bash
atlas context ranked prepare <group>
atlas --json context ranked status <group>
atlas context ranked <group>
```

Packaged `nshkrdotcom` examples:

```bash
atlas registry scan
atlas context ranked prepare gn-ten
atlas --json context ranked status gn-ten
atlas --json context ranked gn-ten --wait-fresh-ms 1200
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
```

## Maintenance

```bash
atlas snapshot <name> -- <command...>
atlas index rebuild [--changed-only]
atlas index
atlas index here [project-ref-or-path]
atlas index status [--project <ref-or-path>] [--all] [--ttl-ms N]
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

`atlas index watch --once` performs one polling pass and exits. `atlas index watch --daemon` runs a polling watcher in the foreground until stopped or signaled; run it under your process supervisor or shell job control if you want a background service. The watcher uses source snapshots, not a wall-clock expiry, to decide whether a project needs reindexing.
`atlas --json index stop` requests clean shutdown, then escalates if the process tree does not exit. It reports `signal_sent`, `force_escalated`, and `stopped`; treat only `stopped: true` as a completed shutdown. Use `atlas index stop --force` for immediate hard-stop recovery.

Turn the watcher off:

```bash
atlas index stop
atlas index stop --force
```

For reboot persistence without a user `systemd` bus, install an `@reboot` crontab entry that runs `atlas index watch --daemon` from the repo checkout or installed environment.

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
