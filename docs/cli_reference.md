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
atlas context ranked <group> [-o <file>]
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
atlas context ranked gn-ten
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
atlas prune snapshots [--days N] [--apply]
atlas find <query...>
atlas open [query...] [--print]
```

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
