# Architecture

Atlas Once is a filesystem-first memory system with three layers:

1. `~/jb`
   Durable human-facing notes, inbox files, sessions, project notes, decisions, people, topics, and snapshots.
2. `~/.atlas_once`
   Operational state: settings, registry data, presets, cache, indexes, locks, and events.
3. `atlas`
   The canonical CLI for humans and agents.

## Design Principles

- plain text for durable memory
- JSON for rebuildable state and machine contracts
- one top-level command surface
- deterministic outputs for automation
- explicit file ownership for generated sections

## Main Components

### Registry

`atlas registry` discovers projects across multiple roots, assigns aliases, and resolves refs such as `jsp`.

State:

- `registry/projects.json`
- `registry/meta.json`

### Context

`atlas context` builds LLM-ready bundles from:

- Markdown trees
- single repos
- multi-repo stacks

Bundles are cached under `~/.atlas_once/cache/bundles`.

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

User data:

```text
~/jb/
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

Operational state:

```text
~/.atlas_once/
  settings.json
  events.jsonl
  registry/
  indexes/
  presets/
  cache/
  locks/
```

## Compatibility

Legacy command names such as `ctx`, `mctx`, `mcc`, `today`, and `memadd` remain available, but `atlas` is the canonical interface and the focus of the current design.
