# Ranked Snapshot Refactor — Implementation Notes

## Source Docset

`~/p/g/n/brainstorms/docs/20260529/atlas/`
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
4. **Genericity guard.** Phase 1 adds `tests/test_genericity.py` asserting that the host-specific strings (`~/p/g/n/`, `~/p/g/n/brainstorms`, `nshkrdotcom` as `self_owners` literal) appear only under `src/atlas_once/profiles/nshkrdotcom/` and `tests/`.
5. **No `atlas install` on the host.** All verification runs `pytest` with the existing `atlas_env`/`atlas_home` fixtures, which already point `ATLAS_ONCE_*` at `tmp_path`.

## TDD Log

Each phase records: tests added → failing baseline → implementation → tests pass.


---

## Phase 10 — Full Completion Report

### Summary of files changed

* `src/atlas_once/ranked_snapshot.py` **(new, 700 LOC)** — pure data
  model + persistence + atomic JSON I/O.
* `src/atlas_once/ranked_snapshot_bridge.py` **(new, ~340 LOC)** —
  bridge from the legacy `RankedPreparedManifest` to the new
  `RankedSnapshot` + freshness + fast-path render functions.
* `src/atlas_once/ranked_context_warmer.py` **(new, ~290 LOC)** —
  background dirty queue, per-scope lock, `tick()`,
  `status_section()`.
* `src/atlas_once/ranked_context.py` **(modified)** — `prepare_
  ranked_manifest()` now also writes a snapshot + latest pointer
  (only for full-universe prepares; sliced prepares skip the write
  to preserve the "full universe" invariant).
* `src/atlas_once/atlas.py` **(modified)** — snapshot fast path in
  the render branch and the cache / plan / tree branches; fast path
  is **default on** (`ATLAS_ONCE_RANKED_FAST_PATH=0` is the
  escape hatch). `--fresh-required` CLI flag.
* `src/atlas_once/index_watcher.py` **(modified)** — `status_payload()`
  surfaces `tasks.ranked_contexts` from the warmer.
* `src/atlas_once/workflows.py` **(modified)** — generic SDK path
  default + `ATLAS_ONCE_PROMPT_RUNNER_SDK_PATH` env override
  (genericity fix).

### Summary of new data model

| Type                  | Role                                              |
| --------------------- | ------------------------------------------------- |
| `RankScopeOptions`    | scope + resolved repo fingerprint                 |
| `RankUniverseOptions` | rank-affecting universe policy                    |
| `RankAlgorithmOptions`| algorithm version & priority tier                 |
| `RankSourceState`     | source / Dexterity / fallback fingerprints        |
| `RenderViewOptions`   | render-only knobs (portion, budget, no-budget)    |
| `OutputOptions`       | text/json/path/color presentation                 |
| `RankedItem`          | single ranked file + score + flags                |
| `RankedSnapshot`      | full ranked universe, snapshot key, metadata      |
| `LatestPointer`       | per-scope pointer: status, complete / fresh keys  |
| `BudgetSummary`       | candidate counts before / after portion / budget  |
| `RenderView`          | snapshot + selected items + budget summary        |
| `FastPathRender`      | rendered text + view + selected files             |
| `FreshnessOutcome`    | status, snapshot key, waited_ms, dirty, warming   |
| `DirtyQueue` / `DirtyScope` | warmer state                                |

### Summary of command behaviour changes

* `atlas context ranked <scope>` — by default loads the latest
  snapshot, slices via portion+budget, renders selected files; emits
  `ranked_snapshot`, `render_view`, `freshness_wait`, `manifest`,
  `prepared_manifest` (compatibility shim) in the JSON envelope.
  No Dexterity call, no legacy builder call, no pointer mutation.
* `atlas context ranked cache|tree|plan <scope>` — also use the fast
  path; emit `ranked_snapshot` + (`render_view` for cache/plan, `tree`
  for tree).
* `atlas context ranked prepare <scope>` — runs the legacy preparer
  AND writes a `RankedSnapshot` + `LatestPointer` if the call was
  full-universe.
* `--fresh-required` — fails fast (`SystemExit`) when the latest
  pointer is not marked `fresh`.
* `--wait-fresh-ms` — fast-path freshness loop polls the pointer
  up to that budget, returns early on fresh.
* `atlas index status --json` — `data.tasks.ranked_contexts` carries
  `enabled`, `dirty_count`, `dirty[]`.

### Snapshot key invariant proof

**Proven by tests** (`tests/test_phase10_sweeps.py`):

> Changing `--portion` (sweep: 1, 5, 10, 25, 50, 75, 100),
> `--max-tokens` (sweep: 10 000, 50 000, 100 000), `--max-bytes`,
> `--no-budget`, and any combination of the above
> **no longer changes the `ranked_snapshot.key`** and
> **does not trigger ranking rebuilds** when a valid snapshot
> exists.

Structural proof: `build_ranked_snapshot_key(scope, universe,
algorithm, source_state)` has no parameter for `RenderViewOptions`
— it is impossible for render-only options to leak into the key by
way of this helper. `tests/test_ranked_snapshot.py::
test_render_options_do_not_affect_ranked_snapshot_key` makes this
mechanical.

Behavioural proof: trip-wires on `_build_prepared_manifest` and
`subprocess.run` fire if the fast path leaks into the heavy code
(it doesn't — sweep tests pass with the trip-wires armed).

### Test commands run and results

```text
uv run ruff check .   → All checks passed!
uv run mypy src       → Success: no issues found in 36 source files
uv run pytest         → 207 passed
```

Focused suites called out by `PROMPT_IMPLEMENTATION.md`:

```text
uv run pytest tests/test_ranked_context.py          → 30 passed
uv run pytest tests/test_index_watcher.py           → 22 passed
uv run pytest tests/test_index_cli.py               →  9 passed
uv run pytest tests/test_code_intelligence.py       → 13 passed
uv run pytest tests/test_intelligence_service.py    →  5 passed
```

New refactor-specific test modules added:

* `tests/test_genericity.py`            — 4 tests (I1 guard)
* `tests/test_ranked_snapshot.py`       — 22 tests (Phase 1+2)
* `tests/test_ranked_snapshot_bridge.py` — 6 tests (Phase 3)
* `tests/test_ranked_snapshot_fast_path.py` — 11 tests (Phase 4)
* `tests/test_ranked_snapshot_cli_views.py` — 4 tests (Phase 5)
* `tests/test_ranked_snapshot_fallback.py` — 5 tests (Phase 6)
* `tests/test_ranked_snapshot_freshness.py` — 7 tests (Phase 7)
* `tests/test_ranked_context_warmer.py`  — 15 tests (Phase 8 + daemon integration)
* `tests/test_ranked_snapshot_default.py` — 8 tests (Phase 9)
* `tests/test_phase10_sweeps.py`         — 3 tests (Phase 10)

### Skipped / known issues

* Per-item Dexterity scores (`score`, `score_components`) are not yet
  populated — the legacy preparer doesn't expose them. Items carry
  rank/bytes/tokens; scores default to 0.0. A future Phase-6+
  enhancement can plumb them through `_query_ranked_files`.
* The background warmer is now attached to `atlas index watch --daemon`.
  Installer/profile/ranked-config/index-start flows enqueue configured
  groups; successful index refreshes mark configured groups dirty; each
  watcher cycle drains the ranked-context warmer queue with
  `ranked_context_warmer.tick(paths)`.
* The fast-path `prepared_manifest` JSON shim omits per-repo /
  per-project breakdowns (`source_roots`, `repo_manifest_paths`,
  `repos[]` are left empty lists). Automation that walks those
  arrays would need to fall back to a full prepare; reading the new
  `ranked_snapshot.key` + `render_view.*` covers all cases the
  in-tree tests exercised.

### Manual commands the user should run

None required by this refactor. On the next clean machine,
`uv tool install git+...atlas_once && atlas install` reproduces the
full installation with the new ranked-snapshot layout — no follow-up
steps, no manual `mkdir`, no manual config edits (per the I3
installer-only-reproducibility invariant).

### Explicit invariant statement

**Changing `--portion`, `--max-tokens`, and `--max-bytes` no longer
changes the ranked snapshot key or triggers ranking rebuilds when a
valid snapshot exists.** Proven by:

* structural unit test
  (`tests/test_ranked_snapshot.py::test_render_options_do_not_affect_ranked_snapshot_key`),
* bridge unit test
  (`tests/test_ranked_snapshot_bridge.py::test_render_only_options_do_not_change_snapshot_key`),
* CLI integration tests
  (`tests/test_ranked_snapshot_fast_path.py`,
  `tests/test_ranked_snapshot_cli_views.py`),
* full portion + budget sweep
  (`tests/test_phase10_sweeps.py`).
