[![GitHub](https://img.shields.io/badge/GitHub-nshkrdotcom%2Fatlas__once-181717?logo=github)](https://github.com/nshkrdotcom/atlas_once)
[![License: MIT](https://img.shields.io/badge/License-MIT-15803D.svg)](LICENSE)

![Atlas Once](assets/atlas_once.svg)

# Atlas Once

Atlas Once is a filesystem-first memory system and Unix-native context engineering toolkit. It unifies daily notes, structured capture, project registry resolution, repo and note context generation, promotion workflows, and write-time note graph maintenance behind a single top-level CLI:

```bash
atlas
```

## What It Does

- installs as normal CLI commands such as `atlas`, `ctx`, `mctx`, and `mcc`
- manages durable notes under a configurable data root
- persists operational state under a configurable state root
- scans multiple project roots and resolves aliases
- builds note, repo, and multi-repo context bundles
- captures inbox entries and promotes them into durable memory
- injects backlinks and related-note sections on write
- exposes a stable `--json` contract for agents
- records an append-only event log for command activity
- ships named configuration profiles, including the `nshkrdotcom` sample profile
- keeps compatibility with older short commands like `ctx`, `mctx`, and `mcc`

## Installation

Recommended:

```bash
uv tool install git+https://github.com/nshkrdotcom/atlas_once
atlas install
```

That puts `atlas` and the companion commands on `PATH`.

By default, `atlas install` applies the shipped `nshkrdotcom` sample profile. You can switch to a neutral profile instead:

```bash
atlas install --profile default
```

Optional shell helper setup:

```bash
atlas config shell install
```

That adds the `d` helper function through a managed shell snippet. Aliases are optional; `atlas`, `ctx`, `mctx`, `mcc`, and `docday` are normal installed commands.

## Quick Start

Human-oriented:

```bash
atlas status
atlas next
atlas today
```

Agent-oriented:

```bash
atlas --json status
atlas --json next
atlas --json resolve <ref>
atlas --json context repo <ref> current
```

## Primary Workflows

Start the day:

```bash
atlas today
```

Capture a thought:

```bash
atlas capture --project <ref> --kind decision "Prefer workspace root for mixed bundles"
```

Review and promote:

```bash
atlas review inbox
atlas promote auto
```

Build context:

```bash
atlas context repo <ref> current
atlas context stack 1 3 5
```

Create and relate notes:

```bash
atlas note new "Routing notes" --project <ref> --tag routing
atlas related <note-path>
```

## Profiles And Config

Atlas Once separates:

- packaged profiles
- user settings
- runtime state

Useful commands:

```bash
atlas config show
atlas config profile list
atlas config profile use default
atlas config set data_home ~/atlas_once
atlas config set code_root ~/code
atlas config roots add ~/code
atlas config shell show
atlas config shell install
```

The shipped `nshkrdotcom` profile is a sample/default profile, not a requirement. Users can switch away from it immediately or customize settings after install.

## Command Surface

Top-level commands:

- `atlas`
- `atlas help <topic>`
- `atlas menu`
- `atlas install`
- `atlas config ...`
- `atlas status`
- `atlas next`
- `atlas resolve <ref>`
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

## Development Checkout

For local development inside the repo:

```bash
git clone https://github.com/nshkrdotcom/atlas_once
cd atlas_once
uv sync --dev
uv run atlas
```

## Architecture

Atlas Once is built on:

- Python 3.12+
- `uv`
- `ruff`
- `pytest`
- `mypy`
- plain Markdown and JSON

Runtime locations come from configuration and profiles. The neutral built-in data-root default is `~/atlas_once`; the shipped `nshkrdotcom` sample profile uses `~/jb`.

## Documentation

- [AGENTS](AGENTS.md)
- [Architecture](docs/architecture.md)
- [Install And Profiles](docs/install_and_profiles.md)
- [CLI Reference](docs/cli_reference.md)
- [Human Onboarding](docs/human_onboarding.md)
- [Agent Onboarding](docs/agent_onboarding.md)
- [Feature Checklist](docs/feature_checklist.md)

## Development

```bash
uv sync --dev
uv run pytest
uv run ruff check .
uv run mypy src
```

## License

Released under the [MIT License](LICENSE). Copyright (c) 2026 nshkrdotcom.
