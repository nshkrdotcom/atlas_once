# Ranked Contexts

`atlas context ranked` is a two-step repo-group context flow:

- `atlas context ranked prepare <config-name>` computes and stores the selected file list
- `atlas context ranked <config-name>` renders the current contents of that prepared file list instantly

It is intended for the case where you want:

- a stable named group of repos
- default context coverage for repos that are not manually configured yet
- reusable per-repo variants that can be reused across multiple groups
- selector-driven groups such as "all owned Elixir repos"
- `README.md` included automatically
- top ranked `lib/**.{ex,exs}` files for Elixir repos
- deterministic default source selection for non-Elixir repos
- per-project blacklist and graylist controls inside monorepos
- final bundle output in the form:

```text
# FILE: ./repo_name/path/to/file
...contents...
```

## Config File

`atlas install` and `atlas config profile use <name>` seed a managed ranked-context config from the active profile.

Find the current path:

```bash
atlas config ranked path
```

Print the current config:

```bash
atlas config ranked show
```

Edit it directly:

```bash
nano "$(atlas config ranked path)"
```

See the whole file:

```bash
atlas config ranked show
```

List group names:

```bash
jq -r '.groups | keys[]' "$(atlas config ranked path)"
```

Inspect one group:

```bash
jq '.groups["ops-default"]' "$(atlas config ranked path)"
```

Inspect one repo definition and its reusable variants:

```bash
jq '.repos["jido_integration"]' "$(atlas config ranked path)"
```

Restore the shipped template for the active profile:

```bash
atlas config ranked install --force
```

## Example

```json
{
  "version": 2,
  "defaults": {
    "registry": {
      "self_owners": ["nshkrdotcom"]
    },
    "runtime": {
      "dexterity_root": "~/p/g/n/dexterity",
      "dexter_bin": "dexter"
    },
    "strategies": {
      "elixir_ranked_v1": {
        "include_readme": true,
        "top_files": 10,
        "overscan_limit": 200
      },
      "python_default_v1": {
        "include_readme": true,
        "top_files": 10
      }
    }
  },
  "repos": {
    "jido_integration": {
      "path": "~/p/g/n/jido_integration",
      "variants": {
        "ops-lite": {
          "top_files": 6,
          "projects": {
            "apps/example_app": {
              "exclude": true
            },
            "apps/worker_app": {
              "top_percent": 0.25
            }
          }
        }
      }
    }
  },
  "groups": {
    "ops-default": {
      "items": [
        {"ref": "jido_action", "variant": "default"},
        {"ref": "jido_integration", "variant": "ops-lite"}
      ]
    },
    "owned-elixir-all": {
      "selectors": [
        {"owner_scope": "self", "has_language": "elixir", "variant": "default"}
      ]
    }
  }
}
```

## Rules

- Group items must set exactly one of `ref` or `path`.
- Repo definitions may set one of `ref` or `path`. If omitted, the repo key is treated as `ref`.
- Repos not listed under `repos` still get an implicit generated `default` variant.
- Selector-driven groups resolve against the current registry during `prepare`.
- Nested Mix projects are discovered automatically from the repo.
- Nested projects cannot be allowlisted individually.
- Nested projects can only be:
  - excluded with `exclude: true`
  - graylisted by lowering `top_files`
  - graylisted by lowering `top_percent`
- `top_files` and `top_percent` are mutually exclusive at the same scope.
- Repo README files are included when `include_readme` is true.
- Project README files are also included when `include_readme` is true and the project has one.
- Per-repo prepared manifests are cached and reused across group prepares.

## Commands

```bash
atlas context ranked prepare ops-default
atlas --json context ranked prepare ops-default
atlas context ranked status ops-default
atlas --json context ranked status ops-default
atlas context ranked ops-default
atlas --json context ranked ops-default
```

For Elixir repos, `prepare` uses Dexterity as the ranked-file selector. Atlas refreshes the Dexter index for each included Mix project, then queries ranked files with first-party filtering restricted to `lib/`.

`prepare` is intentionally explicit and chatty:

- it prints repo and project progress to stderr
- it stores a prepared manifest with the selected file list
- it reuses cached per-repo variant manifests when they are still valid
- it is the slow step

`status` returns that prepared manifest so you can inspect the exact file list without rerunning ranking.

`atlas context ranked <config-name>` is the fast step:

- it loads the prepared manifest
- it reads the current contents of those files
- it emits `# FILE: ./repo_name/path/to/file`
- it does not rerun Dexterity

If the ranked config changes after prepare, Atlas refuses to render from a stale prepared manifest and tells you to rerun `prepare`.

If Dexterity returns no ranked lib files for a project, Atlas falls back to deterministic lexicographic `lib/**.{ex,exs}` order so the project does not disappear from the bundle.

For Python, Rust, Node, and generic repos, Atlas uses deterministic source selection instead of Dexterity.
