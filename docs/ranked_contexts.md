# Ranked Contexts

`atlas context ranked` is the prepared multi-repo code context pipeline.

It is designed for:

- selector-driven repo groups
- explicit repo groups
- per-repo reusable variants
- budget-first `lib/**.{ex,exs}` selection per Mix project
- deterministic prepared manifests
- auditability of repo and project selection

## Lifecycle

Optionally prewarm the selected file list:

```bash
atlas context ranked prepare <group>
atlas --json context ranked prepare <group>
```

Inspect the prepared manifest, auto-preparing if needed:

```bash
atlas context ranked status <group>
atlas --json context ranked status <group>
```

Render current file contents from the prepared manifest, auto-preparing if needed:

```bash
atlas context ranked <group>
atlas --json context ranked <group>
atlas --json context ranked <group> --wait-fresh-ms 1200
```

Inspect the monorepo-aware file tree for the same prepared repo set:

```bash
atlas context ranked tree <group>
atlas --json context ranked tree <group>
atlas context ranked tree <group> --include lib --include test --max-depth 3
```

`prepare` is the explicit prewarm step. `status`, render, and tree reuse the prepared state when it is current and automatically prepare when the manifest is missing, stale, or points at deleted files. All ranked JSON responses include `auto_prepared`, `auto_prepare_reason`, and `index_freshness`. By default Atlas uses `--wait-fresh-ms 0`, records whether the required Mix project indexes look fresh/stale/warming/error, and continues rendering. Use `--no-allow-stale --wait-fresh-ms <N>` when the caller wants stale indexes to fail the command instead of falling back. Freshness is source-snapshot based: if no relevant source file changed since the last successful index, the index stays fresh regardless of age.

For the packaged `nshkrdotcom` profile, the primary sample group is `gn-ten`. It is the opinionated ten-repo slice for:

- `app_kit`
- `extravaganza`
- `mezzanine`
- `outer_brain`
- `citadel`
- `jido_integration`
- `execution_plane`
- `ground_plane`
- `stack_lab`
- `AITrace`

The normal rebuild path is:

```bash
atlas registry scan
atlas index watch --once
atlas context ranked gn-ten
atlas context ranked tree gn-ten
```

Tree output defaults to implementation-first prefixes such as `lib`, `test`, `tests`, `src`, `config`, and `priv`, includes each non-excluded discovered project in monorepos such as `citadel` and `jido_integration`, and skips generated or dependency directories such as `_build`, `deps`, `.git`, and `node_modules`. Use repeated `--include <prefix>` arguments to narrow the tree, `--all` to show all non-skipped source paths, and `--max-depth N` to cap traversal.

During active editing, keep the Dexterity indexes warm with:

```bash
atlas index start
atlas index status
atlas index refresh --project app_kit
atlas index stop
```

For repeated semantic queries in a small active set, prewarm selected service workers:

```bash
atlas intelligence start
atlas intelligence warm app_kit citadel
atlas intelligence status
```

The warm command respects the service worker cap and LRU eviction. It is not intended to warm every registered repo.

## Config File

Find the managed config:

```bash
atlas config ranked path
```

Print it:

```bash
atlas config ranked show
```

Restore the shipped template:

```bash
atlas config ranked install --force
```

## Supported Schema

Only version `3` is supported.

Root shape:

- `version`
- `defaults`
- `repos`
- `groups`

`defaults` supports:

- `registry.self_owners`
- `runtime.dexterity_root`
- `runtime.dexter_bin`
- `runtime.shadow_root`
- `strategies`
- `project_discovery`

`groups` supports:

- `items`
- `selectors`

`selectors[]` supports:

- `owner_scope`
- `has_language`
- `primary_language`
- `relation`
- `exclude_forks`
- `roots`
- `variant`

Repo definitions support:

- `ref` or `path`
- `label`
- `strategy`
- `include_readme`
- `top_files`
- `top_percent`
- `overscan_limit`
- `max_bytes`
- `max_tokens`
- `priority_tier`
- `exclude_path_prefixes`
- `exclude_globs`
- `project_discovery`
- `projects`
- `variants`

Per-project overrides support:

- `exclude`
- `include_readme`
- `top_files`
- `top_percent`
- `overscan_limit`
- `max_bytes`
- `max_tokens`
- `priority_tier`
- `exclude_path_prefixes`
- `exclude_globs`

## Example

```json
{
  "version": 3,
  "defaults": {
    "registry": {
      "self_owners": ["nshkrdotcom"]
    },
    "runtime": {
      "dexterity_root": "~/p/g/n/dexterity",
      "dexter_bin": "dexter",
      "shadow_root": "~/.atlas_once/code/shadows"
    },
    "strategies": {
      "elixir_ranked_v1": {
        "include_readme": true,
        "top_files": 10,
        "overscan_limit": 50,
        "max_bytes": 60000,
        "max_tokens": 15000
      }
    },
    "project_discovery": {
      "exclude_path_prefixes": [
        "_legacy/",
        "test/",
        "tests/",
        "fixtures/",
        "examples/",
        "support/"
      ],
      "exclude_categories": [
        "legacy",
        "test",
        "fixture",
        "example",
        "support"
      ]
    }
  },
  "repos": {
    "jido_integration": {
      "ref": "jido_integration",
      "variants": {
        "gn-ten": {
          "top_files": 4,
          "max_bytes": 120000,
          "max_tokens": 30000,
          "projects": {
            "connectors/github": {
              "top_files": 6,
              "priority_tier": 1
            },
            "core/platform": {
              "top_files": 6,
              "priority_tier": 1
            },
            "apps/example_app": {
              "exclude": true
            }
          }
        }
      }
    }
  },
  "groups": {
    "owned-elixir-all": {
      "selectors": [
        {
          "owner_scope": "self",
          "primary_language": "elixir",
          "relation": "primary",
          "roots": ["~/p/g/n"],
          "variant": "default"
        }
      ]
    },
    "gn-ten": {
      "items": [
        {"ref": "app_kit", "variant": "gn-ten"},
        {"ref": "jido_integration", "variant": "gn-ten"},
        {"ref": "AITrace", "variant": "default"}
      ]
    }
  }
}
```

## Project Discovery

Elixir ranking is project-aware. Atlas discovers nested Mix projects and classifies them before selection.

Excluded by default:

- `_legacy/*`
- `test/*`
- `tests/*`
- `fixtures/*`
- `examples/*`
- `example/*`
- `support/*`
- `tmp/*`
- `dist/*`
- `deps/*`
- `doc/*`
- `docs/*`
- `bench/*`
- `vendor/*`

Override discovery rules with:

- `include_path_prefixes`
- `exclude_path_prefixes`
- `include_categories`
- `exclude_categories`

Repo-level defaults can be overridden per variant.

For the packaged `nshkrdotcom` template, the large monorepos are configured as Weld-aware variants. Atlas narrows project selection to the published artifact roots where the repo defines a Weld projection, then applies budget and priority controls inside that reduced set.

## Shadow Workspaces

Dexterity indexing and querying run against Atlas-managed shadow workspaces under:

```text
~/.atlas_once/code/shadows/
```

Each shadow workspace mirrors one Mix project and holds Dexterity state locally. Source repos stay clean:

- no `.dexter.db`
- no `.dexterity/*`

The realtime watcher, ranked prepare/render/tree path, and repo-local code-intelligence commands all use the same shadow workspace helper, so Dexterity state is isolated consistently whether indexing was triggered by `atlas index`, `atlas index refresh`, `atlas index watch`, `atlas context ranked prepare`, `atlas context ranked <group>`, `atlas context ranked tree <group>`, `atlas agent task`, `atlas symbols`, or `atlas ranked-files`. Dexterity access is serialized per shadow workspace, and query commands skip synchronous indexing when the indexed source snapshot still matches the current source snapshot.

Repo-local Elixir command examples:

```bash
atlas agent task "add streaming support"
atlas agent find Agent
atlas agent related lib/claude_agent_sdk/agent.ex
atlas agent impact lib/claude_agent_sdk/agent.ex
atlas index
atlas symbols Agent --limit 10
atlas files lib --limit 20
atlas def ClaudeAgentSDK.Agent
atlas ranked-files --active lib/claude_agent_sdk/agent.ex --limit 10
atlas impact lib/claude_agent_sdk/agent.ex --token-budget 5000
```

`ranked-files`, `ranked-symbols`, and `impact` hide stdlib, `_build`, `deps`, and vendored dependency paths from `data.result` by default. Use `--include-external` to keep backend output unfiltered.

`atlas agent task "<goal>"` adds a cheap implementation-first repo-structure scan before Dexterity enrichment. This is especially important for multi-Mix repos: the command can still return project layers, sampled modules, likely files, freshness, and next commands when a backend query times out. `atlas files <pattern>` falls back to the same implementation-first source scanner when Dexterity returns no file matches. Agent queries use the persistent intelligence service when it is running and use the backend service timeout by default; raise `ATLAS_ONCE_AGENT_QUERY_TIMEOUT_SECONDS` only when the backend health policy needs to change.

## Index Freshness

`index_freshness` contains:

- `ok`
- `ttl_ms`
- `fresh_projects`
- `stale_projects`
- `warming_projects`
- `error_projects`
- `index_wait_requested_ms`
- `index_waited_ms`
- `index_wait_outcome`
- per-project freshness rows

The freshness check is advisory unless `--no-allow-stale` is used. This keeps normal render latency stable while still giving agents a deterministic signal for when they should refresh or wait.

`ttl_ms` is retained in the payload for compatibility with callers that already pass it, but Atlas does not expire unchanged source snapshots by wall-clock time. `age_ms` reports how old the last successful refresh is; it does not by itself make a project stale.

## Prepared Manifests

Atlas stores:

- per-group prepared manifests under `~/.atlas_once/cache/ranked_contexts`
- per-repo prepared manifests under `~/.atlas_once/cache/ranked_contexts/repos`
- repo summaries with `selection_mode`, `selected_bytes`, `selected_tokens_estimate`, and `unmatched_project_overrides`

`atlas context ranked tree <group>` reads these prepared manifests and walks the current filesystem directly; it does not render file contents and does not shell out to the system `tree` command.

## Drift Handling

Repo layout drift is expected in active monorepos.

- Missing configured project overrides no longer abort `prepare`.
- Atlas emits a progress warning with `reason=unknown-project-override`.
- Atlas records the stale override names in repo summaries as `unmatched_project_overrides`.
- If a cached repo manifest points at files that no longer exist, `prepare` invalidates that repo cache and rebuilds it before writing the group manifest.
- Hard failures remain for real integrity problems such as unreadable repos, invalid manifests, or stale rendered bundles with missing files.

Prepared manifests include:

- selected files
- repo count
- project count
- selection mode
- consumed bytes
- consumed token estimate
- repo cache paths
- per-repo strategy and variant
- per-project category
- per-project exclusion reason
- selected file count
- selected byte count
- selected token estimate
- project priority tier
- fallback usage
- shadow workspace path

## Selection Rules

- `top_files` and `top_percent` are mutually exclusive at the same scope.
- Repos omitted from `repos` still get an implicit `default` variant.
- `projects` overrides only apply to the Elixir ranked strategy.
- Repo `README.md` is included when `include_readme` is true.
- Project `README.md` is included when `include_readme` is true and the project has one.
- Budget enforcement is additive. Atlas can first cap candidate production with `top_files` or `top_percent`, then trim by `max_bytes` and `max_tokens`.
- Lower `priority_tier` wins when repo-level budget pressure forces Atlas to drop lower-priority Mix projects.
- `exclude_path_prefixes` and `exclude_globs` suppress low-signal files before final budgeting.
- If Dexterity returns no ranked files, Atlas falls back to deterministic lexicographic `lib/**.{ex,exs}` order.
- Rendering uses the prepared manifest and current file contents; it does not rerun Dexterity.
