# Install And Profiles

## Recommended Install

```bash
uv tool install git+https://github.com/nshkrdotcom/atlas_once
atlas install
```

Use the CLI directly after install:

```bash
atlas
```

Optional shell helper setup:

```bash
atlas config shell install
```

That installs the managed `d` helper for `docday`.

## Packaged Profiles

Atlas Once currently ships:

- `default`
- `nshkrdotcom`

Inspect them:

```bash
atlas config profile list
atlas config profile show default
atlas config profile show nshkrdotcom
```

Install with a specific profile:

```bash
atlas install --profile default
atlas install --profile nshkrdotcom
```

Switch later:

```bash
atlas config profile use default
```

The installed default remains `nshkrdotcom`.

## Ranked Config Seeding

`atlas install` and `atlas config profile use <name>` both seed the managed ranked-context config for the active profile.
The shipped config is repo-owned. If the packaged defaults change, reapply them with `atlas config ranked install --force` instead of hand-copying local edits.

Useful commands:

```bash
atlas config ranked path
atlas config ranked show
atlas config ranked install --force
```

The shipped `nshkrdotcom` template now seeds:

- `owned-elixir-all` for self-owned primary Elixir repos under `~/p/g/n`
- `gn-ten` for the opinionated ten-repo workspace slice

The ranked defaults are budget-first:

- byte budget via `max_bytes`
- estimated token budget via `max_tokens`
- project ordering via `priority_tier`

Typical ranked flow after install:

```bash
atlas context ranked prepare gn-ten
atlas --json context ranked status gn-ten
atlas context ranked gn-ten
```

If you want to inspect or customize the managed config:

```bash
nano "$(atlas config ranked path)"
```

## Shell Setup

Atlas installs normal commands on `PATH`. The shell snippet is only for `d`, because changing directories must happen in the current shell process.

Show the snippet:

```bash
atlas config shell show
```

Install it:

```bash
atlas config shell install
```

## Customize Paths

Show effective settings:

```bash
atlas config show
```

Adjust roots and storage:

```bash
atlas config set data_home ~/atlas_once
atlas config set code_root ~/code
atlas config roots add ~/code
atlas config roots remove ~/code
```

## Dev Checkout Mode

For local development:

```bash
git clone https://github.com/nshkrdotcom/atlas_once
cd atlas_once
uv sync --dev
uv run atlas
```
