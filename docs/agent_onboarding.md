# Agent Onboarding

## Command Contract

Use `atlas --json ...` for automation whenever possible.

JSON responses use the stable envelope:

- `schema_version`
- `ok`
- `command`
- `exit_code`
- `data`
- `errors`

Atlas also writes an append-only event log at `~/.atlas_once/events.jsonl`.

## Recommended Startup Flow

Resolve environment state first:

```bash
atlas --json status
atlas --json next
atlas --json resolve <ref>
atlas --json registry scan
```

If you need repo discovery details:

```bash
atlas --json registry list
atlas --json registry show <ref>
```

## Context Flows

Single repo:

```bash
atlas --json context repo <ref> current
```

Explicit or remembered stack:

```bash
atlas --json context stack <item>...
```

Ranked multi-repo code context:

```bash
atlas --json registry scan
atlas --json context ranked prepare <group>
atlas --json context ranked status <group>
atlas --json context ranked <group>
```

For the packaged `nshkrdotcom` profile, default automation should treat `gn-ten` as the primary workspace group unless the user asks for a different slice.

Legacy helper commands remain installed:

- `ctx`
- `mixctx` / `mctx`
- `mcc`

Use `prepare` before `status` or render. Rendering refuses stale manifests after ranked config or registry changes.

## Ranked Config Model

Only ranked config version `3` is supported.

Top-level shape:

- `version`
- `defaults`
- `repos`
- `groups`

Relevant ranked fields for automation:

- `defaults.runtime.dexterity_root`
- `defaults.runtime.dexter_bin`
- `defaults.runtime.shadow_root`
- `defaults.project_discovery`
- `groups[].selectors[].roots`
- `repos[].variants`
- `repos[].variants[].projects`
- `max_bytes`
- `max_tokens`
- `priority_tier`
- `exclude_path_prefixes`
- `exclude_globs`

Prepared ranked manifests include:

- selected files
- source roots
- repo count
- project count
- selection mode
- consumed bytes
- consumed token estimate
- repo manifest paths
- per-repo summaries
- per-repo `unmatched_project_overrides` when configured project names no longer match the live repo layout
- per-project category, exclusion reason, selected count, selected bytes, selected token estimate, fallback usage, priority tier, and shadow root

## Ranking Behavior

- Elixir repos use Dexterity per Mix project.
- Ranking is limited to `lib/`.
- Default project discovery excludes `_legacy`, tests, fixtures, examples, support, tmp, dist, deps, docs, bench, and vendor trees.
- Budget enforcement happens after candidate production. `top_files` / `top_percent` can narrow the candidate pool, then `max_bytes` / `max_tokens` trim the final prepared selection.
- Lower `priority_tier` is higher priority under repo budget pressure.
- If Dexterity returns no ranked files, Atlas falls back to lexicographic `lib/**.{ex,exs}` order.
- Dexterity state lives in Atlas-managed shadow workspaces, not in source repos.
- Missing project overrides are warnings, not fatal prepare failures. Agents should inspect `unmatched_project_overrides` from `status` if drift matters for the task.
- Repo cache hits are rejected when a previously selected file disappeared, so a fresh `prepare` rebuilds that repo before render.

## Storage Paths

Config:

- `~/.config/atlas_once/ranked_contexts.json`
- `~/.config/atlas_once/ranked_contexts.state.json`

State:

- `~/.atlas_once/cache/ranked_contexts`
- `~/.atlas_once/cache/ranked_contexts/repos`
- `~/.atlas_once/code/shadows`
- `~/.atlas_once/registry/repos.json`
- `~/.atlas_once/registry/projects.json`
- `~/.atlas_once/events.jsonl`
