# Ranked Contexts

`atlas context ranked` is a two-step multi-repo Elixir context flow:

- `atlas context ranked prepare <config-name>` computes and stores the selected file list
- `atlas context ranked <config-name>` renders the current contents of that prepared file list instantly

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

List config names:

```bash
jq -r '.configs | keys[]' "$(atlas config ranked path)"
```

Inspect one config:

```bash
jq '.configs["ops-default"]' "$(atlas config ranked path)"
```

Restore the shipped template for the active profile:

```bash
atlas config ranked install --force
```

## Example

```json
{
  "version": 1,
  "defaults": {
    "dexterity_root": "~/p/g/n/dexterity",
    "dexter_bin": "dexter",
    "include_readme": true,
    "top_files": 10,
    "overscan_limit": 200
  },
  "configs": {
    "ops-default": {
      "repos": [
        {
          "path": "~/p/g/n/jido_integration",
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
          "path": "~/p/g/n/jido_action",
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

## Commands

```bash
atlas context ranked prepare ops-default
atlas --json context ranked prepare ops-default
atlas context ranked status ops-default
atlas --json context ranked status ops-default
atlas context ranked ops-default
atlas --json context ranked ops-default
```

`prepare` uses Dexterity as the ranked-file selector. Atlas refreshes the Dexter index for each included Mix project, then queries ranked files with first-party filtering restricted to `lib/`.

`prepare` is intentionally explicit and chatty:

- it prints repo and project progress to stderr
- it stores a prepared manifest with the selected file list
- it is the slow step

`status` returns that prepared manifest so you can inspect the exact file list without rerunning ranking.

`atlas context ranked <config-name>` is the fast step:

- it loads the prepared manifest
- it reads the current contents of those files
- it emits `# FILE: ./repo_name/path/to/file`
- it does not rerun Dexterity

If the ranked config changes after prepare, Atlas refuses to render from a stale prepared manifest and tells you to rerun `prepare`.

If Dexterity returns no ranked lib files for a project, Atlas falls back to deterministic lexicographic `lib/**.{ex,exs}` order so the project does not disappear from the bundle.
