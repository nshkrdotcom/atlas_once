[![GitHub](https://img.shields.io/badge/GitHub-nshkrdotcom%2Fatlas__once-181717?logo=github)](https://github.com/nshkrdotcom/atlas_once)
[![License: MIT](https://img.shields.io/badge/License-MIT-15803D.svg)](LICENSE)

![Atlas Once](assets/atlas_once.svg)

# Atlas Once

Atlas Once is a filesystem-first personal memory system and Unix-native context engineering toolkit. It unifies daily notes, structured capture, project registry resolution, repo and note context generation, promotion workflows, and write-time note graph maintenance behind a single top-level CLI:

```bash
atlas
```

## What It Does

- manages durable notes under `~/jb`
- persists operational state under `~/.atlas_once`
- scans multiple project roots and resolves aliases
- builds note, repo, and multi-repo context bundles
- captures inbox entries and promotes them into durable memory
- injects backlinks and related-note sections on write
- keeps compatibility with older short commands like `ctx`, `mctx`, and `mcc`

## Quick Start

```bash
uv sync --dev
uv run atlas init
uv run atlas registry scan
uv run atlas
```

## Primary Workflows

Start the day:

```bash
uv run atlas today
```

Capture a thought:

```bash
uv run atlas capture --project jsp --kind decision "Prefer workspace root for mixed bundles"
```

Review and promote:

```bash
uv run atlas review inbox
uv run atlas promote auto
```

Build context:

```bash
uv run atlas context repo jsp current
uv run atlas context stack 1 3 5
```

Create and relate notes:

```bash
uv run atlas note new "Switchyard routing notes" --project switchyard --tag routing
uv run atlas related ~/jb/docs/20260411/switchyard/switchyard-routing-notes.md
```

## Command Surface

Top-level commands:

- `atlas`
- `atlas help <topic>`
- `atlas menu`
- `atlas init`
- `atlas registry ...`
- `atlas today`
- `atlas capture ...`
- `atlas review ...`
- `atlas promote ...`
- `atlas note ...`
- `atlas context ...`
- `atlas snapshot ...`
- `atlas related ...`
- `atlas index ...`
- `atlas prune ...`

Compatibility commands:

- `ctx`
- `mixctx` / `mctx`
- `mcc`
- `docday`
- `today`
- `memadd`
- `memfind`
- `memopen`
- `memsnap`
- `session-close`
- `atlas-index`
- `atlas-related`
- `atlas-prune`

## Architecture

Atlas Once is built on:

- Python 3.12+
- `uv`
- `ruff`
- `pytest`
- `mypy`
- plain Markdown and JSON

Data lives in `~/jb`. Persistent operational state lives in `~/.atlas_once`.

## Documentation

- [Architecture](docs/architecture.md)
- [CLI Reference](docs/cli_reference.md)
- [Human Onboarding](docs/human_onboarding.md)
- [Agent Onboarding](docs/agent_onboarding.md)

## Development

```bash
uv sync --dev
uv run pytest
uv run ruff check .
uv run mypy src
```

## License

Released under the [MIT License](LICENSE). Copyright (c) 2026 nshkrdotcom.
