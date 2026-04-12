# Human Onboarding

## Install

```bash
uv sync --dev
```

## First Run

```bash
uv run atlas init
uv run atlas registry scan
uv run atlas
```

## Daily Flow

```bash
uv run atlas status
uv run atlas today
uv run atlas capture "A loose thought to revisit later"
uv run atlas review daily
uv run atlas next
```

## Project Flow

```bash
uv run atlas resolve jsp
uv run atlas context repo jsp current
uv run atlas note new "Switchyard daemon notes" --project switchyard --tag daemon
```

## Review And Promotion

```bash
uv run atlas review inbox
uv run atlas promote auto
```

## Useful State Paths

- notes and durable memory: `~/jb`
- operational state: `~/.atlas_once`
- presets: `~/.atlas_once/presets/mcc.json`
- event log: `~/.atlas_once/events.jsonl`

## Quality Checks

```bash
uv run pytest
uv run ruff check .
uv run mypy src
```
