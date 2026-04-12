# Human Onboarding

## Install

```bash
uv sync --dev
```

## First Run

```bash
atlas init
atlas registry scan
atlas
```

## Daily Flow

```bash
atlas today
atlas capture "A loose thought to revisit later"
atlas review daily
```

## Project Flow

```bash
atlas registry resolve jsp
atlas context repo jsp current
atlas note new "Switchyard daemon notes" --project switchyard --tag daemon
```

## Review and Promotion

```bash
atlas review inbox
atlas promote auto
```

## Quality Checks

```bash
uv run pytest
uv run ruff check .
uv run mypy src
```
