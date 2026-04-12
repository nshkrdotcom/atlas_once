# Human Onboarding

## Install

Recommended:

```bash
uv tool install git+https://github.com/nshkrdotcom/atlas_once
atlas install
```

The installer defaults to the shipped `nshkrdotcom` sample profile. If you want neutral defaults instead:

```bash
atlas install --profile default
```

Optional shell helper setup:

```bash
atlas config shell install
```

## First Run

```bash
atlas config show
atlas status
atlas next
```

## Daily Flow

```bash
atlas status
atlas today
atlas capture "A loose thought to revisit later"
atlas review daily
atlas next
```

## Project Flow

```bash
atlas resolve <ref>
atlas context repo <ref> current
atlas note new "Daemon notes" --project <ref> --tag daemon
```

## Review And Promotion

```bash
atlas review inbox
atlas promote auto
```

## Adjust The Layout

```bash
atlas config profile list
atlas config profile use default
atlas config set data_home ~/atlas_once
atlas config set code_root ~/code
atlas config roots add ~/code
```

## Useful State Paths

- user config: `~/.config/atlas_once`
- runtime state: `~/.atlas_once`
- bundle cache: `~/.atlas_once/cache/bundles`
- event log: `~/.atlas_once/events.jsonl`
- data root: profile/config controlled

## Contributor Quality Checks

```bash
pytest
ruff check .
mypy src
```
