# AGENTS.md

Atlas Once is a filesystem-first memory and context system. This repo is built around a single canonical interface:

```bash
uv run atlas ...
```

Compatibility commands such as `ctx`, `mctx`, `mcc`, `today`, and `memadd` still exist, but new automation should go through `atlas`.

## Agent Contract

- Prefer `uv run atlas --json ...` for automation.
- JSON responses use a stable envelope:
  - `schema_version`
  - `ok`
  - `command`
  - `exit_code`
  - `data`
  - `errors`
- Atlas writes an append-only event log to `~/.atlas_once/events.jsonl`.
- Mutating commands take file locks under `~/.atlas_once/locks`.

## Storage Model

- Durable user-facing notes live under `~/jb`.
- Persistent operational state lives under `~/.atlas_once`.
- Main state files:
  - `registry/projects.json`
  - `registry/meta.json`
  - `indexes/relationships.json`
  - `indexes/projects.json`
  - `indexes/tags.json`
  - `indexes/links.json`
  - `presets/mcc.json`
  - `cache/bundles/*.ctx`
  - `events.jsonl`

Environment overrides:

- `ATLAS_ONCE_HOME`
- `ATLAS_ONCE_STATE_HOME`
- `ATLAS_ONCE_CONFIG_HOME`
- `ATLAS_ONCE_CODE_ROOT`
- `ATLAS_ONCE_PROJECT_ROOTS`

## Generated Content

Atlas owns these generated sections inside notes:

- `<!-- atlas:backlinks:start --> ... <!-- atlas:backlinks:end -->`
- `<!-- atlas:related:start --> ... <!-- atlas:related:end -->`

Do not hand-edit those blocks. Atlas will rewrite them during sync.

## Preferred Flows

Resolve project state first:

```bash
uv run atlas --json status
uv run atlas --json next
uv run atlas --json resolve jsp
```

Build context:

```bash
uv run atlas --json context repo jsp current
uv run atlas --json context stack 1 3 5
uv run atlas --json context notes ~/jb/docs/20260411/atlas_once
```

Capture and promote:

```bash
uv run atlas --json capture --project jsp --kind decision --stdin
uv run atlas --json review inbox
uv run atlas --json promote auto
```

Notes:

```bash
uv run atlas --json note new "Routing notes" --project jsp --body-stdin
uv run atlas --json note find routing daemon
uv run atlas --json note sync
```

## Development Workflow

Install:

```bash
uv sync --dev
```

Quality gates:

```bash
uv run pytest
uv run ruff check .
uv run mypy src
```

## Documentation Rule

If you change the CLI surface, JSON contract, storage layout, or workflows, update:

- `README.md`
- `docs/architecture.md`
- `docs/cli_reference.md`
- `docs/human_onboarding.md`
- `docs/agent_onboarding.md`
- `docs/feature_checklist.md`
- `/home/home/jb/docs/20260411/atlas_once/`
