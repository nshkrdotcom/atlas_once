# Agent Onboarding

Atlas Once is designed to be easy for agents to drive from the CLI.

## Use The Canonical Interface

Installed environment:

```bash
atlas --json ...
```

Repo checkout environment:

```bash
uv run atlas --json ...
```

Short commands like `ctx`, `mctx`, and `mcc` remain available, but `atlas` is the stable interface.

## JSON Contract

Every top-level `atlas --json` command returns:

- `schema_version`
- `ok`
- `command`
- `exit_code`
- `data`
- `errors`

This makes Atlas suitable for deterministic agent loops.

## First Calls

Start here:

```bash
atlas --json status
atlas --json next
atlas --json resolve <ref>
```

These tell you the current system state, the next recommended action, and the canonical repo path for a project ref.

## Common Flows

Build repo context:

```bash
atlas --json context repo <ref> current
```

Build multi-repo context:

```bash
atlas --json context stack 1 3 5
atlas --json context stack --group current <ref-a> <ref-b>
```

Build note context:

```bash
atlas --json context notes <notes-dir>
```

Capture and promote:

```bash
printf 'Prefer workspace root for mixed bundles.' | \
  atlas --json capture --stdin --project <ref> --kind decision

atlas --json review inbox
atlas --json promote auto
```

Create or sync notes:

```bash
printf 'Links to [[beta]].' | \
  atlas --json note new "Alpha" --project <ref> --body-stdin

atlas --json note sync
```

## Storage Assumptions

- user config lives under `~/.config/atlas_once`
- operational state lives under `~/.atlas_once`
- bundle cache lives under `~/.atlas_once/cache/bundles`
- event log lives at `~/.atlas_once/events.jsonl`
- actual data root depends on the active profile/settings

## Profile Awareness

Useful setup calls:

```bash
atlas --json config show
atlas --json config profile current
atlas --json config profile list
```

The shipped install default is the `nshkrdotcom` sample profile, but users may switch or customize it.

## Generated Sections

Atlas owns these note blocks:

- backlinks
- related notes

Do not treat those as hand-authored source of truth.

## Repo Quality Gates

Before shipping changes:

```bash
pytest
ruff check .
mypy src
```
