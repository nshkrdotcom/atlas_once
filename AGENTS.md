# AGENTS.md

Atlas Once is a filesystem-first memory and context system. The canonical installed interface is:

```bash
atlas ...
```

In a repo checkout, use:

```bash
uv run atlas ...
```

Compatibility commands such as `ctx`, `mctx`, `mcc`, `today`, and `memadd` still exist, but new automation should go through `atlas`.

## Agent Contract

- Prefer `atlas --json ...` for automation.
- In a repo checkout, use `uv run atlas --json ...`.
- JSON responses use a stable envelope:
  - `schema_version`
  - `ok`
  - `command`
  - `exit_code`
  - `data`
  - `errors`
- Atlas writes an append-only event log to `~/.atlas_once/events.jsonl`.
- Mutating commands take file locks under `~/.atlas_once/locks`.

## Storage Model

- Durable user-facing notes live under the configured data root.
- User config lives under `~/.config/atlas_once` by default.
- Persistent operational state lives under `~/.atlas_once`.
- Main state files:
  - `settings.json`
  - `profile.json`
  - `registry/projects.json`
  - `registry/meta.json`
  - `indexes/relationships.json`
  - `indexes/projects.json`
  - `indexes/tags.json`
  - `indexes/links.json`
  - `presets/mcc.json`
  - `cache/bundles/*.ctx`
  - `events.jsonl`

Environment overrides:

- `ATLAS_ONCE_HOME`
- `ATLAS_ONCE_STATE_HOME`
- `ATLAS_ONCE_CONFIG_HOME`
- `ATLAS_ONCE_CODE_ROOT`
- `ATLAS_ONCE_PROJECT_ROOTS`

## Profiles

Packaged profiles currently include:

- `default`
- `nshkrdotcom`

The installed default is `nshkrdotcom`, but users can switch or customize with:

```bash
atlas config profile list
atlas config profile use default
atlas config set data_home ~/atlas_once
atlas config set code_root ~/code
atlas config roots add ~/code
```

## Generated Content

Atlas owns these generated sections inside notes:

- `<!-- atlas:backlinks:start --> ... <!-- atlas:backlinks:end -->`
- `<!-- atlas:related:start --> ... <!-- atlas:related:end -->`

Do not hand-edit those blocks. Atlas will rewrite them during sync.

## Preferred Flows

Resolve project state first:

```bash
atlas --json status
atlas --json next
atlas --json resolve <ref>
```

Inspect configured project roots:

```bash
atlas --json config show
atlas --json registry list
```

The active root list is under `data.settings.project_roots`.

## Realtime Index Watcher

Atlas can keep Dexterity ranked indexes warm for Elixir Mix projects.

Control commands:

```bash
atlas --json index status
atlas --json index watch --once
atlas --json index watch --daemon
atlas --json index refresh --project <ref-or-path>
atlas --json index stop
atlas --json index stop --force
```

Behavior:

- `watch --once` performs one polling pass and exits.
- `watch --daemon` runs a foreground polling daemon until stopped.
- `index stop` writes the stop marker, requests clean shutdown, and escalates if the process tree does not exit.
- In JSON, `index stop` reports `signal_sent` separately from `stopped`; only `stopped: true` means the watcher process has exited.
- JSON `force_escalated: true` means the clean stop timed out and Atlas sent the hard-stop signal.
- `index stop --force` sends a hard stop and clears stale watcher state.
- A second `watch --daemon` does not start a duplicate if an active watcher PID is already recorded.
- Watcher state lives under `~/.atlas_once/index_watcher`.
- Dexterity state stays in Atlas shadow workspaces under `~/.atlas_once/code/shadows`.
- `atlas context ranked ... --json` includes `index_freshness`; default `--wait-fresh-ms 0` does not block.
- Freshness is source-snapshot based. Elapsed time alone must not make an unchanged repo stale.

## Elixir Code Intelligence

Inside a Mix repo, agents can use short commands without path boilerplate:

```bash
atlas --json index
atlas --json symbols Agent --limit 10
atlas --json def ClaudeAgentSDK.Agent
atlas --json def ClaudeAgentSDK.Agent new 1
atlas --json refs ClaudeAgentSDK.Agent
atlas --json ranked-files --active lib/claude_agent_sdk/agent.ex --limit 10
atlas --json impact lib/claude_agent_sdk/agent.ex --token-budget 5000
atlas --json repo-map --active lib/claude_agent_sdk/agent.ex --limit 10
atlas --json dexter lookup ClaudeAgentSDK.Agent
```

Use `--project <ref-or-path>` when not running from the target repo. These commands all index through Atlas shadow workspaces and must not create `.dexter.db`, `.dexterity`, or Atlas lock files under the source repo. Query commands skip synchronous indexing when the indexed source snapshot still matches the current source snapshot, and backend metadata records retry attempts. `ranked-files`, `ranked-symbols`, and `impact` default to repo-source results; add `--include-external` only when stdlib or dependency paths are intentionally relevant.

Build context:

```bash
atlas --json context repo <ref> current
atlas --json context stack 1 3 5
atlas --json context ranked <group>
atlas --json context notes <notes-dir>
```

Capture and promote:

```bash
atlas --json capture --project <ref> --kind decision --stdin
atlas --json review inbox
atlas --json promote auto
```

Notes:

```bash
atlas --json note new "Routing notes" --project <ref> --body-stdin
atlas --json note find routing daemon
atlas --json note sync
```

Optional shell helper install:

```bash
atlas config shell install
```

## Development Workflow

Install:

```bash
uv sync --dev
```

Quality gates:

```bash
uv run pytest
uv run ruff check .
uv run mypy src
```

## Documentation Rule

If you change the CLI surface, JSON contract, storage layout, or workflows, update:

- `README.md`
- `docs/install_and_profiles.md`
- `docs/architecture.md`
- `docs/cli_reference.md`
- `docs/human_onboarding.md`
- `docs/agent_onboarding.md`
- `docs/feature_checklist.md`
- the external design docs that track major atlas buildouts
