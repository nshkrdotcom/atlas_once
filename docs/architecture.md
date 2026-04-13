# Architecture

Atlas Once is a filesystem-first memory system with three layers:

1. configurable data root
   Durable human-facing notes, inbox files, sessions, project notes, decisions, people, topics, and snapshots.
2. user config and runtime state
   Config lives under `~/.config/atlas_once`; runtime state lives under `~/.atlas_once` by default.
3. `atlas`
   The canonical CLI for humans and agents.

The shipped `nshkrdotcom` profile maps the data root to `~/p/g/j/jido_brainstorm/nshkrdotcom`, keeps `code_root` at `~/p/g/n`, and scans repos from both `~/p/g/j` and `~/p/g/n`, but Atlas Once no longer assumes that layout as a universal default.

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

Profile install and profile switches also seed the managed ranked-context config for that profile.

### Config

`atlas config` manages:

- effective settings
- profile selection
- project roots
- shell helper generation/installation

### Registry

`atlas registry` discovers repos across configured roots, assigns aliases, and resolves refs such as `jsp`.

Registry records now include local repo metadata such as:

- remote ownership and fork heuristics
- language inventory and primary language
- repo capabilities used for default context strategies
- nested Mix-project inventory for monorepos

State:

- `registry/repos.json`
- `registry/projects.json`
- `registry/meta.json`

`registry/repos.json` is the canonical enriched repo registry. `registry/projects.json` remains as a compatibility projection for older callers.

### Context

`atlas context` builds LLM-ready bundles from:

- Markdown trees
- single repos
- multi-repo stacks
- named repo groups with reusable per-repo variants

For Elixir repos, ranked selection is backed by Dexterity. For non-Elixir repos, Atlas uses deterministic per-language defaults.

Ranked repo-group context is a two-step flow:

- `atlas context ranked prepare <config-name>` computes and caches the selected file list
- `atlas context ranked status <config-name>` shows the prepared manifest and exact file list
- `atlas context ranked <config-name>` renders current file contents from that prepared manifest

Prepared ranked manifests are cached per group and per repo variant under the runtime state root.

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
  ranked_contexts.state.json
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
    bundles/
    ranked_contexts/
      repos/
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
