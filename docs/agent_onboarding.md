# Agent Onboarding

Atlas Once is designed to be easy for agents to drive from the CLI.

## Use The Canonical Interface

Prefer:

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
uv run atlas --json status
uv run atlas --json next
uv run atlas --json resolve jsp
```

These tell you the current system state, the next recommended action, and the canonical repo path for a project ref.

## Common Flows

Build repo context:

```bash
uv run atlas --json context repo jsp current
```

Build multi-repo context:

```bash
uv run atlas --json context stack 1 3 5
uv run atlas --json context stack --group current jsp jido_domain
```

Build note context:

```bash
uv run atlas --json context notes ~/jb/docs/20260411/atlas_once
```

Capture and promote:

```bash
printf 'Prefer workspace root for mixed bundles.' | \
  uv run atlas --json capture --stdin --project jsp --kind decision

uv run atlas --json review inbox
uv run atlas --json promote auto
```

Create or sync notes:

```bash
printf 'Links to [[beta]].' | \
  uv run atlas --json note new "Alpha" --project jsp --body-stdin

uv run atlas --json note sync
```

## Storage Assumptions

- user-facing notes live under `~/jb`
- operational state lives under `~/.atlas_once`
- bundle cache lives under `~/.atlas_once/cache/bundles`
- event log lives at `~/.atlas_once/events.jsonl`

## Generated Sections

Atlas owns these note blocks:

- backlinks
- related notes

Do not treat those as hand-authored source of truth.

## Quality Gates

Before shipping changes:

```bash
uv run pytest
uv run ruff check .
uv run mypy src
```
