# Architecture

Atlas Once has three storage layers and one canonical CLI.

## Layers

1. Data root
   Durable human-facing notes, inbox files, sessions, projects, decisions, people, topics, and snapshots.
2. Config root
   User config under `~/.config/atlas_once` by default.
3. State root
   Rebuildable operational state under `~/.atlas_once` by default.
4. CLI
   `atlas` is the primary interface for both humans and agents.

## Design Principles

- filesystem-first storage
- deterministic output for automation
- explicit prepared state for expensive context generation
- clean separation between durable notes and rebuildable indexes
- no source-repo pollution from ranking/indexing state

## Main Subsystems

### Profiles And Config

`atlas install` applies a packaged profile and seeds ranked-context config for that profile.

Packaged profiles currently include:

- `default`
- `nshkrdotcom`

Config is managed through `atlas config ...`.

For the packaged `nshkrdotcom` profile, the repo-owned ranked template seeds `gn-ten` as the primary personal workspace slice and `owned-elixir-all` as the broader selector-driven group. Reapplying that template is an explicit import step via `atlas config ranked install --force`.

### Registry

`atlas registry` scans project roots and records:

- repo path
- aliases
- owner scope
- fork relation
- language inventory
- primary language
- strategy capabilities
- nested Mix project inventory

Registry state is written under:

```text
~/.atlas_once/registry/
  repos.json
  projects.json
  meta.json
```

### Context

`atlas context` builds bundles from:

- notes trees
- a single repo
- a saved or explicit stack of repos
- ranked repo groups

Ranked context is the multi-stage code-intelligence pipeline:

1. Resolve a named group into repo variants.
2. Discover rankable Mix projects or deterministic source files.
3. For Elixir repos, index each included Mix project with Dexterity.
4. Run ranking against an Atlas-managed shadow workspace.
5. Persist per-repo and per-group prepared manifests.
6. Render current file contents from the prepared manifest.

Prepared repo summaries preserve config drift explicitly. If a configured Mix project override no longer matches the live repo layout, Atlas keeps preparing the rest of the group and records the stale names as `unmatched_project_overrides`.

### Shadow Workspaces

Dexterity state is isolated under:

```text
~/.atlas_once/code/shadows/
```

Each shadow workspace mirrors one Mix project and owns its local `.dexter.db` and `.dexterity/*` state. Source repos remain clean.

### Capture, Review, And Promotion

`atlas capture`, `atlas review`, and `atlas promote` implement the inbox-to-memory flow.

### Note Graph

`atlas note`, `atlas related`, and index rebuild commands maintain:

- backlinks
- related-note suggestions
- project indexes
- tag indexes
- link indexes

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

State:

```text
~/.atlas_once/
  events.jsonl
  code/
    shadows/
  registry/
    repos.json
    projects.json
    meta.json
  indexes/
    relationships.json
    projects.json
    tags.json
    links.json
  presets/
    context_stack.json
  cache/
    bundles/
    ranked_contexts/
      repos/
  locks/
```

Data root:

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

## Ranked Config Model

Only ranked schema version `3` is supported.

The root object contains:

- `version`
- `defaults`
- `repos`
- `groups`

The most important ranked controls are:

- `defaults.runtime.shadow_root`
- `defaults.project_discovery`
- `groups[].selectors[].roots`
- repo `variants`
- per-project `top_files`, `top_percent`, `max_bytes`, `max_tokens`, `priority_tier`, and `exclude`
- per-repo `unmatched_project_overrides` in prepared summaries when repo layout drifts past packaged config

Installed helper commands such as `ctx`, `mixctx` / `mctx`, and `mcc` remain available for existing workflows, even though `atlas` is the canonical interface.
