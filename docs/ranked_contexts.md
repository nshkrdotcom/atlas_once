# Ranked Contexts

`atlas context ranked` is the prepared multi-repo code context pipeline.

It is designed for:

- selector-driven repo groups
- explicit repo groups
- per-repo reusable variants
- top `lib/**.{ex,exs}` selection per Mix project
- deterministic prepared manifests
- auditability of repo and project selection

## Lifecycle

Prepare the selected file list:

```bash
atlas context ranked prepare <group>
atlas --json context ranked prepare <group>
```

Inspect the prepared manifest:

```bash
atlas context ranked status <group>
atlas --json context ranked status <group>
```

Render current file contents from that manifest:

```bash
atlas context ranked <group>
atlas --json context ranked <group>
```

`prepare` is the slow step. `status` and render reuse the prepared state until the config or registry changes.

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
- `project_discovery`
- `projects`
- `variants`

Per-project overrides support:

- `exclude`
- `include_readme`
- `top_files`
- `top_percent`
- `overscan_limit`

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
        "ops-lite": {
          "projects": {
            "apps/devops_incident_response": {
              "top_files": 4
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
    "workspace-ten": {
      "items": [
        {"ref": "jido_action", "variant": "default"},
        {"ref": "jido_integration", "variant": "ops-lite"}
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

## Shadow Workspaces

Dexterity indexing and querying run against Atlas-managed shadow workspaces under:

```text
~/.atlas_once/code/shadows/
```

Each shadow workspace mirrors one Mix project and holds Dexterity state locally. Source repos stay clean:

- no `.dexter.db`
- no `.dexterity/*`

## Prepared Manifests

Atlas stores:

- per-group prepared manifests under `~/.atlas_once/cache/ranked_contexts/`
- per-repo manifests under `~/.atlas_once/cache/ranked_contexts/repos/`

Prepared manifests include:

- selected files
- repo count
- project count
- repo cache paths
- per-repo strategy and variant
- per-project category
- per-project exclusion reason
- selected file count
- fallback usage
- shadow workspace path

## Selection Rules

- `top_files` and `top_percent` are mutually exclusive at the same scope.
- Repos omitted from `repos` still get an implicit `default` variant.
- `projects` overrides only apply to the Elixir ranked strategy.
- Repo `README.md` is included when `include_readme` is true.
- Project `README.md` is included when `include_readme` is true and the project has one.
- If Dexterity returns no ranked files, Atlas falls back to deterministic lexicographic `lib/**.{ex,exs}` order.
- Rendering uses the prepared manifest and current file contents; it does not rerun Dexterity.
