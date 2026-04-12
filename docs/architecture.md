# Architecture

Atlas Once is a filesystem-first memory system with three layers:

1. `~/jb`
   User-facing notes, inbox files, session notes, project memory, and snapshots.
2. `~/.atlas_once`
   Persistent operational state such as settings, project registry data, presets, and note graph indexes.
3. `atlas`
   The primary CLI surface for humans and agents.

The core architectural choice is to keep all durable information in plain text files and make JSON state caches rebuildable.

## Major Components

- `atlas registry`
  Multi-root project discovery and alias resolution.
- `atlas context`
  Context bundling for notes, single repos, and multi-repo stacks.
- `atlas capture`, `atlas review`, `atlas promote`
  Structured inbox workflow.
- `atlas note`, `atlas related`, `atlas index`
  Durable note lifecycle and note graph maintenance.

## Compatibility

Legacy command names such as `ctx`, `mctx`, and `mcc` remain available, but the system is designed around `atlas` as the canonical interface.
