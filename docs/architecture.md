# Architecture

Atlas Once is a filesystem-first memory system with three layers:

1. configurable data root
   Durable human-facing notes, inbox files, sessions, project notes, decisions, people, topics, and snapshots.
2. user config and runtime state
   Config lives under `~/.config/atlas_once`; runtime state lives under `~/.atlas_once` by default.
3. `atlas`
   The canonical CLI for humans and agents.

The shipped `nshkrdotcom` profile maps the data root to `~/jb`, but Atlas Once no longer assumes that layout as a universal default.

## Design Principles

- install-first, alias-optional CLI
- plain text for durable memory
- JSON for rebuildable state and machine contracts
- named profiles for local path assumptions
- one top-level command surface
- deterministic outputs for automation
- explicit file ownership for generated sections

## Main Components

### Install And Profiles

`atlas install` applies a named profile and can optionally install a shell snippet. Packaged profiles currently include:

- `default`
- `nshkrdotcom`

### Config

`atlas config` manages:

- effective settings
- profile selection
- project roots
- shell helper generation/installation

### Registry

`atlas registry` discovers projects across configured roots, assigns aliases, and resolves refs such as `jsp`.

State:

- `registry/projects.json`
- `registry/meta.json`

### Context

`atlas context` builds LLM-ready bundles from:

- Markdown trees
- single repos
- multi-repo stacks
- named ranked Elixir repo groups backed by Dexterity

Bundles are cached under the runtime state root.

### Capture And Promotion

`atlas capture`, `atlas review`, and `atlas promote` implement a structured inbox workflow that moves loose notes into durable project, topic, decision, person, and session memory.

### Note Graph

`atlas note`, `atlas related`, and `atlas index` maintain:

- backlinks
- related-note suggestions
- project indexes
- tag indexes
- link indexes

Atlas updates graph data incrementally when possible.

### Agent Runtime

The top-level CLI provides:

- global `--json`
- stable exit codes
- append-only event logging
- mutation locks
- status and next-action helpers

## Storage Layout

Config:

```text
~/.config/atlas_once/
  settings.json
  profile.json
  ranked_contexts.json
  shell/
```

Runtime state:

```text
~/.atlas_once/
  events.jsonl
  registry/
  indexes/
  presets/
  cache/
  locks/
```

Data root layout:

```text
<data-root>/
  docs/
  mem/
    inbox/
    sessions/
    projects/
    decisions/
    people/
    topics/
    snapshots/
```

## Compatibility

Legacy command names such as `ctx`, `mctx`, `mcc`, `today`, and `memadd` remain available, but `atlas` is the canonical interface and the focus of the current design.
