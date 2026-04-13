# Ranked Contexts

`atlas context ranked` builds a multi-repo Elixir context bundle from a named config.

It is intended for the case where you want:

- a stable named group of repos
- `README.md` included automatically
- only the top ranked `lib/**.{ex,exs}` files per repo or nested Mix project
- per-project blacklist and graylist controls inside monorepos
- final bundle output in the form:

```text
# FILE: ./repo_name/path/to/file
...contents...
```

## Config File

Atlas reads ranked-context configs from:

```bash
~/.config/atlas_once/ranked_contexts.json
```

Edit it directly:

```bash
nano ~/.config/atlas_once/ranked_contexts.json
```

See the whole file:

```bash
cat ~/.config/atlas_once/ranked_contexts.json
```

List config names:

```bash
jq -r '.configs | keys[]' ~/.config/atlas_once/ranked_contexts.json
```

Inspect one config:

```bash
jq '.configs["ops-default"]' ~/.config/atlas_once/ranked_contexts.json
```

## Example

```json
{
  "version": 1,
  "defaults": {
    "dexterity_root": "/home/home/p/g/n/dexterity",
    "dexter_bin": "dexter",
    "include_readme": true,
    "top_files": 10,
    "overscan_limit": 200
  },
  "configs": {
    "ops-default": {
      "repos": [
        {
          "ref": "jido_integration",
          "projects": {
            "apps/example_app": {
              "exclude": true
            },
            "apps/worker_app": {
              "top_percent": 0.25
            }
          }
        },
        {
          "path": "/home/home/p/g/n/jido_action",
          "top_files": 5
        }
      ]
    }
  }
}
```

## Rules

- Each repo entry must set exactly one of `ref` or `path`.
- Nested Mix projects are discovered automatically from the repo.
- Nested projects cannot be allowlisted individually.
- Nested projects can only be:
  - excluded with `exclude: true`
  - graylisted by lowering `top_files`
  - graylisted by lowering `top_percent`
- `top_files` and `top_percent` are mutually exclusive at the same scope.
- Repo README files are included when `include_readme` is true.
- Project README files are also included when `include_readme` is true and the project has one.

## Command

```bash
atlas context ranked ops-default
atlas --json context ranked ops-default
```

`atlas context ranked` uses Dexterity as the ranked-file selector. Atlas first refreshes the Dexter index for each included Mix project, then queries ranked files with first-party filtering restricted to `lib/`.

If Dexterity returns no ranked lib files for a project, Atlas falls back to deterministic lexicographic `lib/**.{ex,exs}` order so the project does not disappear from the bundle.
