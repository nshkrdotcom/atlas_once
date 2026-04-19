[![GitHub](https://img.shields.io/badge/GitHub-nshkrdotcom%2Fatlas__once-181717?logo=github)](https://github.com/nshkrdotcom/atlas_once)
[![License: MIT](https://img.shields.io/badge/License-MIT-15803D.svg)](LICENSE)

![Atlas Once](assets/atlas_once.svg)

# Atlas Once

Atlas Once is a filesystem-first memory and context system for local workspaces. It keeps durable notes in plain files, tracks repo state across multiple roots, and builds deterministic LLM-ready bundles through one canonical CLI:

```bash
atlas
```

## What It Does

- manages durable notes, captures, reviews, and promotion workflows under a configurable data root
- keeps operational state under `~/.atlas_once` and user config under `~/.config/atlas_once` by default
- scans repo roots, resolves refs and aliases, and records repo capabilities for context selection
- builds repo, stack, notes, and ranked multi-repo context bundles
- uses Dexterity for Elixir ranked file selection without writing `.dexter.db` or `.dexterity/*` into source repos
- supports budget-first ranked selection with byte caps, estimated token caps, and project priority tiers
- keeps short helper commands such as `ctx`, `mixctx`/`mctx`, and `mcc` installed on `PATH`
- exposes a stable `--json` envelope for agents and automation
- records an append-only event log at `~/.atlas_once/events.jsonl`
- ships packaged profiles, including `default` and `nshkrdotcom`

## Installation

Recommended:

```bash
uv tool install git+https://github.com/nshkrdotcom/atlas_once
atlas install
```

Optional shell helper setup:

```bash
atlas config shell install
```

That installs the managed `d` helper. Normal command use does not require aliases.

## Quick Start

Human-oriented:

```bash
atlas status
atlas next
atlas today
atlas registry scan
```

Agent-oriented:

```bash
atlas --json status
atlas --json next
atlas --json resolve <ref>
atlas --json context repo <ref> current
atlas --json context ranked prepare gn-ten
atlas --json context ranked status gn-ten
atlas --json context ranked gn-ten
```

## Ranked Contexts

`atlas context ranked` is the main multi-repo code-intelligence flow.

```bash
atlas context ranked prepare <group>
atlas --json context ranked status <group>
atlas context ranked <group>
```

For the packaged `nshkrdotcom` profile, the first-class sample group is `gn-ten`:

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

Rebuild that index from the current workspace state:

```bash
atlas registry scan
atlas context ranked prepare gn-ten
atlas context ranked gn-ten
```

Keep ranked Elixir indexes warm during active work:

```bash
atlas index watch --once
atlas --json index status
atlas --json index refresh --project app_kit
atlas --json context ranked gn-ten --wait-fresh-ms 1200
```

`atlas index watch --daemon` runs a foreground polling watcher. Use shell job control or a process supervisor if you want it to stay in the background. Ranked rendering remains non-blocking by default; `--wait-fresh-ms` opts into a bounded wait and JSON output includes `index_freshness`.

Enable it on reboot with your init system. On machines without a working user `systemd` bus, a user crontab entry is sufficient:

```cron
@reboot cd /path/to/atlas_once && /home/home/.local/bin/uv run atlas index watch --daemon >> /home/home/.atlas_once/logs/index-watcher.log 2>&1
```

Stop it cleanly with:

```bash
atlas index stop
```

JSON stop payloads separate `signal_sent`, `force_escalated`, and `stopped`; only `stopped: true` means the watcher process has actually exited. A normal stop requests clean shutdown first and escalates if the process tree does not exit.

Force recovery from stale state:

```bash
atlas index stop --force
```

Reapply the repo-owned packaged defaults after pulling a newer Atlas Once version:

```bash
atlas config ranked install --force
```

The ranked config lives at:

```bash
atlas config ranked path
```

Atlas currently supports one ranked schema version: `3`.

The managed config contains:

- `defaults.runtime`
- `defaults.registry`
- `defaults.strategies`
- `defaults.project_discovery`
- `repos`
- `groups`

Key ranked-context behaviors:

- Elixir ranking runs per Mix project, not per repo.
- Default project discovery excludes `_legacy`, `test`, `tests`, `fixtures`, `examples`, `support`, `tmp`, `dist`, `deps`, `docs`, `bench`, and `vendor`.
- Groups can target precise workspace roots with `selectors[].roots`.
- Budget-first selection is first class: `max_bytes`, `max_tokens`, and `priority_tier` now sit beside `top_files`.
- Repo definitions can override individual Mix projects with `top_files`, `top_percent`, `max_bytes`, `max_tokens`, `priority_tier`, or `exclude`.
- Prepared manifests include repo-level and project-level selection metadata so selection is auditable.
- If repo layout drifts and a configured project override no longer exists, `prepare` warns with `reason=unknown-project-override` and records `unmatched_project_overrides` in `status` output instead of aborting the whole group.
- If a cached repo manifest points at files that were deleted, the next `prepare` rebuilds that repo cache automatically instead of preserving a broken render path.

Example selector for self-owned primary Elixir repos under `~/p/g/n`:

```json
{
  "owner_scope": "self",
  "primary_language": "elixir",
  "relation": "primary",
  "roots": ["~/p/g/n"],
  "variant": "default"
}
```

Example per-project override for a monorepo:

```json
{
  "connectors/github": {"top_files": 6, "priority_tier": 1},
  "core/platform": {"top_files": 6, "priority_tier": 1},
  "apps/example_app": {"exclude": true}
}
```

Reapply the shipped ranked defaults from the packaged profile template with:

```bash
atlas config ranked install --force
```

## Shadow Workspaces

Dexterity indexing runs against Atlas-managed shadow workspaces under:

```text
~/.atlas_once/code/shadows/
```

Each shadow workspace is a symlinked mirror of one Mix project plus local Dexterity state. This keeps `.dexter.db` and `.dexterity/*` out of source repos while preserving deterministic ranking behavior.

Watcher state lives under:

```text
~/.atlas_once/index_watcher/
```

It is rebuildable state and can be inspected with `atlas --json index status` or cleared with `atlas index stop --force` when recovering from a stale process marker.

## Typical Flows

Resolve and scan:

```bash
atlas config show
atlas registry scan
atlas registry list
atlas resolve <ref>
```

Build context:

```bash
atlas context repo <ref> current
atlas context stack 1 3 5
atlas index status
atlas index refresh --project <ref>
atlas context ranked prepare gn-ten
atlas context ranked owned-elixir-all
```

Capture and promote:

```bash
atlas capture --project <ref> --kind decision --stdin
atlas review inbox
atlas promote auto
```

Notes:

```bash
atlas note new "Routing notes" --project <ref> --body-stdin
atlas note find routing daemon
atlas note sync
```

## Development

In a repo checkout:

```bash
git clone https://github.com/nshkrdotcom/atlas_once
cd atlas_once
uv sync --dev
uv run atlas
```

Quality gates:

```bash
uv run pytest
uv run ruff check .
uv run mypy src
```

## Docs

- [Install And Profiles](docs/install_and_profiles.md)
- [Architecture](docs/architecture.md)
- [CLI Reference](docs/cli_reference.md)
- [Human Onboarding](docs/human_onboarding.md)
- [Agent Onboarding](docs/agent_onboarding.md)
- [Ranked Contexts](docs/ranked_contexts.md)
- [Feature Checklist](docs/feature_checklist.md)
