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
- builds ranked multi-repo Elixir context bundles from named configs
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
atlas --json context ranked ops-default
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
atlas context ranked ops-default
```

Create and relate notes:

```bash
atlas note new "Routing notes" --project <ref> --tag routing
atlas related <note-path>
```

## Ranked Context Quickstart

Fastest path to a working ranked Elixir context bundle:

1. Confirm Atlas is installed:

```bash
atlas status
```

2. Open the ranked-context config file:

```bash
nano ~/.config/atlas_once/ranked_contexts.json
```

3. Define a named config such as `ops-default` with the repos you want context from.

4. Run it:

```bash
atlas --json context ranked ops-default
```

That command gives you a machine-readable manifest plus a cached bundle containing:

- repo `README.md`
- nested project `README.md` when present
- top ranked `lib/**.{ex,exs}` files per included Mix project
- file markers in the form `# FILE: ./repo_name/path/to/file`

If you want the rendered bundle itself instead of the JSON manifest:

```bash
atlas context ranked ops-default
```

## See And Manage Ranked Configs

Ranked repo groups are intentionally simple to manage: they live in one JSON file.

Config file location:

```bash
~/.config/atlas_once/ranked_contexts.json
```

Open it for editing:

```bash
nano ~/.config/atlas_once/ranked_contexts.json
```

Print the whole file:

```bash
cat ~/.config/atlas_once/ranked_contexts.json
```

List your named configs:

```bash
jq -r '.configs | keys[]' ~/.config/atlas_once/ranked_contexts.json
```

Inspect one config:

```bash
jq '.configs["ops-default"]' ~/.config/atlas_once/ranked_contexts.json
```

The normal workflow is:

- add or remove repos in `repos`
- tune repo defaults with `top_files`, `top_percent`, `include_readme`, and `overscan_limit`
- blacklist a nested Mix project with `exclude: true`
- graylist a nested Mix project by lowering `top_files` or `top_percent`
- rerun `atlas --json context ranked <config-name>`

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
atlas context ranked ops-default
```

Ranked Elixir context configs live at:

```bash
~/.config/atlas_once/ranked_contexts.json
```

Edit it directly:

```bash
nano ~/.config/atlas_once/ranked_contexts.json
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
- [Ranked Contexts](docs/ranked_contexts.md)
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
