# Human Onboarding

## First Run

Install Atlas Once and seed the active profile:

```bash
uv tool install git+https://github.com/nshkrdotcom/atlas_once
atlas install
```

Optional shell helper:

```bash
atlas config shell install
```

## Check Your Setup

```bash
atlas config show
atlas registry scan
atlas registry list
atlas status
atlas next
```

If your repos live outside the current roots:

```bash
atlas config roots add ~/code
atlas registry scan
```

## Ranked Code Context

Find the managed ranked config:

```bash
atlas config ranked path
atlas config ranked show
```

Prepare and render the packaged workspace group:

```bash
atlas context ranked prepare gn-ten
atlas --json context ranked status gn-ten
atlas context ranked gn-ten
```

Edit the config if you want a different group, different repo variants, or different per-project budget/priority overrides:

```bash
nano "$(atlas config ranked path)"
```

Key ranked behaviors:

- Elixir ranking works per Mix project.
- Default discovery excludes fixtures, tests, examples, support code, legacy trees, and temp trees.
- Budget-first fields are first class: `max_bytes`, `max_tokens`, and `priority_tier`.
- Dexterity state is kept under `~/.atlas_once/code/shadows`, not inside your repos.

## Memory Workflow

Capture:

```bash
atlas capture --project <ref> --kind decision --stdin
```

Review:

```bash
atlas review inbox
atlas review daily
```

Promote:

```bash
atlas promote auto
```

Notes:

```bash
atlas note new "Routing notes" --project <ref> --body-stdin
atlas note find routing daemon
atlas note sync
```

## Where Things Live

Config:

- `~/.config/atlas_once`

State:

- `~/.atlas_once`
- `~/.atlas_once/cache/ranked_contexts`
- `~/.atlas_once/code/shadows`
- `~/.atlas_once/events.jsonl`

Durable data:

- `~/atlas_once` by default, or the active profile data root
