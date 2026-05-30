# Ranked Snapshot Refactor — Implementation Notes

## Source Docset

`~/p/g/j/jido_brainstorm/nshkrdotcom/docs/20260529/atlas/`
(commit `3956958` adds §0 implementation boundaries to 00/02/10/11.)

## Phase 0 — Inspection Summary

### Current ranked-context entrypoints

* CLI dispatcher: `src/atlas_once/atlas.py` (large file; ranked subcommands routed through `cmd_context_ranked*` helpers).
* Primary module: `src/atlas_once/ranked_context.py` (4007 lines).
* Adapter: `src/atlas_once/code_intelligence.py` (1241 lines).
* Optional service: `src/atlas_once/intelligence_service.py` (790 lines).
* Watcher: `src/atlas_once/index_watcher.py` (1220 lines).
* Shadow workspace: `src/atlas_once/shadow_workspace.py`.
* Runtime helpers: `src/atlas_once/runtime.py`.

### Current expensive cache identity (problem area)

`_ranked_manifest_cache_key(config_name, options)` (ranked_context.py:3908) returns:

* `config_name` if options are default, else
* `f"{config_name}::{option_fragment}"` where `option_fragment` includes `portion`, `amount`, `select_mode`, `projects_mode`, `no_budget` and a 12-char sha256 of the full `ranked_context_options_dict(options)`.

That means **`--portion`, `--max-tokens`, `--max-bytes`, `--no-budget` all participate in the cache key**, exactly the failure mode the refactor must remove.

`_ranked_config_hash` (ranked_context.py:3951) also folds `effective_options` into `relevant["effective_options"]` when options are non-default — same coupling.

### Current render option fields

`RankedContextOptions` (ranked_context.py:124): `portion, amount, projects_mode, files_mode, select_mode, max_tokens, max_bytes, no_budget, include_projects, exclude_projects, current_path`.

Of these, **render-only** per I1/I2 of `00-executive-summary.md`: `portion, max_tokens, max_bytes, no_budget` (and any output path / json flag handled at CLI layer).

**Rank-affecting**: `amount` only insofar as `_amount_alias_options` sets `select_mode`/`projects_mode`; `projects_mode`, `files_mode`, `select_mode`, `include_projects`, `exclude_projects`, `current_path` change the candidate universe.

### Current prepared manifest schema

`RankedPreparedManifest` (ranked_context.py:163) is serialized by `prepared_manifest_dict` (~line 998) into:

```
config_name, manifest_path, config_hash, prepared_at, files[],
source_roots, repo_count, project_count, selection_mode,
consumed_bytes, consumed_tokens_estimate, budget_max_bytes,
budget_max_tokens, repo_manifest_paths, repos[], options
```

`files[]` is the **already portion-and-budget sliced** selection (see `_build_prepared_manifest`, ~line 1414). The full ranked universe is **not** persisted at group level — each per-repo manifest produced by `_prepare_repo_variant_manifest` carries its own per-repo selection.

### Whether current manifest stores full ranked list

**No.** The current `RankedPreparedManifest.files` is the slice after portion/budget. This is the second core bug the refactor must fix: persist the full pre-slice ranked universe so foreground render can do cheap re-slicing.

### Current status JSON shape (index watcher)

`atlas index status --json` exposes `data.tasks.dexterity_index`, `data.tasks.git_health`. There is **no** `data.tasks.ranked_contexts` section yet — needs to be added in Phase 8.

### Current testing gaps

* No test asserts that `--portion` does *not* change the prepared-manifest cache key (it actually does; the test suite expects portion-keyed caches in places — see `test_context_ranked_accepts_ad_hoc_path_and_portion` and `test_ranked_context_default_and_explicit_option_repo_caches_do_not_churn`).
* No test asserts the foreground render avoids calling Dexterity when a snapshot exists.
* No test asserts that the prepared manifest stores the full ranked universe.
* No genericity guard test.

### Baseline test result

```
uv run pytest          → 129 passed in 3.93s
uv run ruff check .    → All checks passed!
uv run mypy src        → Success: no issues found in 33 source files
```

## Phase 0 — Adaptation Decisions

1. **Additive data model.** Phase 1 introduces a new `RankedSnapshot` / `RankedItem` / `LatestPointer` / `RenderView` set of dataclasses *next to* the existing `RankedPreparedManifest`. The legacy manifest stays as a side-channel during Stages 2–5 of the migration plan.
2. **Snapshot key payload separated from options.** A new `_ranked_snapshot_key_payload(...)` excludes render-only options. The existing `_ranked_manifest_cache_key` is left in place for legacy callers only; new callers route through `build_ranked_snapshot_key`.
3. **Snapshot storage paths** live under `paths.ranked_context_cache_root / "snapshots" / <scope_kind> / <key>.json` and `paths.ranked_context_cache_root / "latest" / <scope_kind> / <scope_id>.json`. Directories are created lazily by writers; `ensure_state` is **not** extended to pre-create them on existing installs (Installer-Only Reproducibility — but harmless to leave on-demand).
4. **Genericity guard.** Phase 1 adds `tests/test_genericity.py` asserting that the host-specific strings (`~/p/g/n/`, `~/p/g/j/jido_brainstorm/nshkrdotcom`, `nshkrdotcom` as `self_owners` literal) appear only under `src/atlas_once/profiles/nshkrdotcom/` and `tests/`.
5. **No `atlas install` on the host.** All verification runs `pytest` with the existing `atlas_env`/`atlas_home` fixtures, which already point `ATLAS_ONCE_*` at `tmp_path`.

## TDD Log

Each phase records: tests added → failing baseline → implementation → tests pass.

