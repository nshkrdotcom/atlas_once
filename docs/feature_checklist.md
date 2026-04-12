# Feature Checklist

This is the implementation checklist for the current Atlas Once build.

## Command Surface

- [x] Single canonical `atlas` entry point
- [x] Dashboard via bare `atlas`
- [x] Human help topics via `atlas help <topic>`
- [x] Interactive menu fallback via `atlas menu`
- [x] Top-level `atlas resolve <ref>`
- [x] Top-level `atlas status`
- [x] Top-level `atlas next`

## Agent Contract

- [x] Global `--json` envelope on the top-level CLI
- [x] Stable JSON shape with `schema_version`, `ok`, `command`, `exit_code`, `data`, `errors`
- [x] Stable exit codes for common failure classes
- [x] Append-only event log at `~/.atlas_once/events.jsonl`
- [x] File locks for mutating commands
- [x] Stdin-friendly capture and note creation

## Storage And Persistence

- [x] User data rooted at `~/jb`
- [x] Operational state rooted at `~/.atlas_once`
- [x] Environment overrides for data root, state root, code root, and project roots
- [x] Persistent registry, indexes, presets, cache, and event log directories
- [x] Legacy `mcc` preset migration into the new state tree

## Project Registry

- [x] Multi-root registry scanning
- [x] Alias generation and manual aliases
- [x] Cross-root resolution for `~/p/g/n`, `~/p/g/North-Shore-AI`, and future roots
- [x] `atlas registry root-add` and `root-remove`
- [x] `atlas registry alias-add` and `alias-remove`
- [x] Incremental `atlas registry scan --changed-only`

## Context Bundling

- [x] Markdown tree bundling via `atlas context notes`
- [x] Elixir repo bundling via `atlas context repo`
- [x] Multi-repo stack bundling via `atlas context stack`
- [x] Shared bundle manifests with included files, source roots, byte count, token estimate, and cache key
- [x] Bundle cache under `~/.atlas_once/cache/bundles`
- [x] Stack preset persistence and `--remember`

## Capture, Review, Promotion

- [x] Structured inbox entries
- [x] Inbox review and daily review flows
- [x] Auto-promotion for promotable entries
- [x] Manual promotion by entry id
- [x] Durable project, topic, person, decision, note, and session targets

## Note Graph

- [x] Backlink generation on write
- [x] Related-note generation on write
- [x] Incremental note graph sync
- [x] Relationship cache and metadata
- [x] Project, tag, and link indexes

## Compatibility Commands

- [x] `ctx`
- [x] `mixctx` / `mctx`
- [x] `mcc`
- [x] `docday`
- [x] `today`
- [x] `memadd`
- [x] `memfind`
- [x] `memopen`
- [x] `memsnap`
- [x] `session-close`

## Tooling And QC

- [x] `uv` project setup
- [x] `pytest`
- [x] `ruff`
- [x] `mypy`
- [x] CI workflow
- [x] SVG asset
- [x] MIT license

## Documentation

- [x] README with badges and SVG
- [x] Architecture guide
- [x] CLI reference
- [x] Human onboarding
- [x] Agent onboarding
- [x] Root `AGENTS.md`
- [x] External design docs in `~/jb/docs/20260411/atlas_once`

## Next Nice Additions

- [ ] Optional watch mode for automatic registry/index refresh
- [ ] Bulk promotion policies beyond the current heuristics
- [ ] Optional archive lifecycle for stale inbox/session material
- [ ] Optional remote sync/export adapters
