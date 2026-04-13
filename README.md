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
atlas --json context ranked prepare ops-default
atlas --json context ranked status ops-default
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
atlas context ranked prepare ops-default
atlas context ranked ops-default
```

Create and relate notes:

```bash
atlas note new "Routing notes" --project <ref> --tag routing
atlas related <note-path>
```

## Ranked Context Quickstart

Fastest path to a working repo-group context bundle:

1. Install Atlas with a packaged profile:

```bash
atlas install
```

That seeds the managed ranked-context config from the active profile. The shipped `nshkrdotcom` profile includes:

- explicit repo groups such as `ops-default`
- selector-driven groups such as `owned-elixir-all`
- reusable per-repo variants, for example `jido_integration` `ops-lite`

2. Confirm where the managed config lives:

```bash
atlas config ranked path
```

3. Inspect the seeded config:

```bash
atlas config ranked show
```

4. Prepare the ranked file list:

```bash
atlas --json context ranked prepare ops-default
```

That step is the slow one. It resolves the group, reuses or refreshes per-repo prepared manifests, prints explicit progress, and writes a prepared group manifest listing the selected files.

5. Inspect the prepared file list when needed:

```bash
atlas --json context ranked status ops-default
```

6. Render the current contents instantly:

```bash
atlas --json context ranked ops-default
```

The rendered bundle always uses:

- `# FILE: ./repo_name/path/to/file`

For Elixir repos, the default strategy includes:

- repo `README.md`
- nested project `README.md` when present
- top ranked `lib/**.{ex,exs}` files per included Mix project

For non-Elixir repos, Atlas picks a deterministic default strategy from repo capabilities, for example Python or Rust source selection.

If you want the rendered bundle itself instead of the JSON manifest:

```bash
atlas context ranked ops-default
```

The normal flow is:

- `atlas context ranked prepare <config-name>` when you want to recompute which files matter
- `atlas context ranked <config-name>` when you want the current contents of those prepared files

## See And Manage Ranked Configs

Ranked repo groups and reusable per-repo variants live in one JSON file.

Config file location:

```bash
atlas config ranked path
```

Open it for editing:

```bash
nano "$(atlas config ranked path)"
```

Print the whole file:

```bash
atlas config ranked show
```

Inspect the current prepared manifest:

```bash
atlas --json context ranked status ops-default
```

List your named groups:

```bash
jq -r '.groups | keys[]' "$(atlas config ranked path)"
```

Inspect one group:

```bash
jq '.groups["ops-default"]' "$(atlas config ranked path)"
```

Inspect one repo definition and its variants:

```bash
jq '.repos["jido_integration"]' "$(atlas config ranked path)"
```

The normal workflow is:

- add explicit repo definitions under `repos` when you need reusable per-repo variants
- leave repos out of `repos` when the implicit generated `default` variant is enough
- build groups from explicit `items` and dynamic `selectors`
- tune repo or variant defaults with `strategy`, `top_files`, `top_percent`, `include_readme`, and `overscan_limit`
- blacklist a nested Mix project with `exclude: true`
- graylist a nested Mix project by lowering `top_files` or `top_percent`
- rerun `atlas --json context ranked prepare <config-name>`
- render current contents with `atlas --json context ranked <config-name>`

If you want to restore the shipped template for the active profile:

```bash
atlas config ranked install --force
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
atlas config ranked path
atlas config ranked show
atlas config ranked install --force
atlas context ranked prepare ops-default
atlas --json context ranked status ops-default
atlas context ranked ops-default
```

Ranked Elixir context configs live at:

```bash
atlas config ranked path
```

Edit it directly:

```bash
nano "$(atlas config ranked path)"
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
