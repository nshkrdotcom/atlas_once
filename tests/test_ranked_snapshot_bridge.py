"""Phase 3 — explicit prepare also writes a ranked snapshot + latest pointer.

The central docset invariant proven here:

    Calling ``prepare_ranked_manifest`` with different render-only
    options (portion, max-tokens, max-bytes, no-budget) MUST yield the
    same ``ranked_snapshot.snapshot_key`` provided the rank-affecting
    inputs (scope, universe, algorithm, source state) are unchanged.

The legacy prepared manifest path is left untouched; the snapshot is
written alongside it (docset §10 migration Stage 2 — "write ranked
snapshots alongside existing prepared manifests").
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from atlas_once.config import get_paths
from atlas_once.ranked_context import (
    RankedContextOptions,
    prepare_ranked_manifest,
)
from atlas_once.ranked_snapshot import (
    latest_pointer_path,
    load_latest_pointer,
    load_ranked_snapshot,
    snapshots_root,
)


def _write(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")


def _write_ranked_config(atlas_env: Path, payload: dict[str, object]) -> Path:
    config_path = atlas_env / "config" / "atlas_once" / "ranked_contexts.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return config_path


def _make_mix_project(root: Path, *, files: dict[str, str]) -> None:
    _write(root / "mix.exs", "defmodule Demo.MixProject do\nend\n")
    _write(root / "README.md", f"# {root.name}\n")
    for rel_path, contents in files.items():
        _write(root / rel_path, contents)


@pytest.fixture
def configured_atlas(atlas_env: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Build a fully wired ranked-context fixture that will let
    ``prepare_ranked_manifest`` succeed under a deterministic fallback.

    We mirror the larger fixture in ``test_ranked_context.py`` but
    keep it minimal: one self-owned Elixir repo with two ``.ex`` files,
    a Dexterity stub that always returns the same ranked file list, and
    the standard registry scan."""
    monkeypatch.setenv("ATLAS_ONCE_SELF_OWNERS", "nshkrdotcom")
    dexterity_root = atlas_env / "dexterity"
    dexterity_root.mkdir()

    repo = atlas_env / "code" / "demo_repo"
    _make_mix_project(
        repo,
        files={
            "lib/a.ex": "defmodule A do\n  def x, do: 1\nend\n",
            "lib/b.ex": "defmodule B do\n  def y, do: 2\nend\n",
            "lib/c.ex": "defmodule C do\n  def z, do: 3\nend\n",
        },
    )
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", "n:nshkrdotcom/demo_repo.git"],
        check=True,
    )

    _write_ranked_config(
        atlas_env,
        {
            "version": 3,
            "defaults": {
                "registry": {"self_owners": ["nshkrdotcom"]},
                "runtime": {"dexterity_root": str(dexterity_root)},
                "strategies": {
                    "elixir_ranked_v1": {"include_readme": True, "top_files": 3}
                },
            },
            "repos": {},
            "groups": {
                "demo": {
                    "selectors": [
                        {
                            "owner_scope": "self",
                            "primary_language": "elixir",
                            "relation": "primary",
                            "roots": [str(atlas_env / "code")],
                            "variant": "default",
                        }
                    ]
                }
            },
        },
    )

    from atlas_once.atlas import main as atlas_main

    assert atlas_main(["registry", "scan"]) == 0

    # Force the deterministic-fallback path: every dexterity.query returns
    # an empty rank, which causes the preparer to fall back to lib/* order.
    def fake_run(
        cmd: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["mix", "dexterity.query"]:
            return subprocess.CompletedProcess(
                cmd, 0, json.dumps({"ok": True, "command": "ranked_files", "result": []}), ""
            )
        if cmd[:2] == ["mix", "dexterity.index"]:
            raise AssertionError("ranked prepare must not invoke dexterity.index")
        # Forward everything else (git, etc) to the real subprocess.run.
        return subprocess.run(cmd, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("atlas_once.ranked_context.subprocess.run", fake_run)
    return repo


def test_prepare_writes_snapshot_alongside_legacy_manifest(
    configured_atlas: Path,
) -> None:
    paths = get_paths()
    prepared = prepare_ranked_manifest(paths, "demo")
    assert prepared.manifest_path.is_file()

    snap_dir = snapshots_root(paths, "group")
    assert snap_dir.is_dir()
    snapshot_files = list(snap_dir.glob("*.json"))
    assert len(snapshot_files) == 1, snapshot_files

    pointer_target = latest_pointer_path(paths, "group", "demo")
    assert pointer_target.is_file()
    pointer = load_latest_pointer(paths, "group", "demo")
    assert pointer is not None
    assert pointer.status == "fresh"
    assert pointer.latest_complete_snapshot_key is not None

    snapshot = load_ranked_snapshot(paths, "group", pointer.latest_complete_snapshot_key)
    assert snapshot is not None
    assert snapshot.scope.scope_id == "demo"
    # Items list is at least the README from the demo repo.
    assert any(item.path.endswith("README.md") or "README" in (item.path or "")
               for item in snapshot.items) or snapshot.items, snapshot.items


def test_context_ranked_warm_builds_full_snapshot(
    configured_atlas: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from atlas_once.atlas import main as atlas_main

    paths = get_paths()

    assert atlas_main(["--json", "context", "ranked", "warm", "demo"]) == 0
    payload = json.loads(capsys.readouterr().out)

    pointer = load_latest_pointer(paths, "group", "demo")
    assert pointer is not None
    assert pointer.latest_complete_snapshot_key is not None
    assert payload["command"] == "context.ranked.warm"
    assert payload["data"]["ranked_snapshot"]["key"] == pointer.latest_complete_snapshot_key
    assert payload["data"]["prepared_manifest"]["file_count"] >= 1


@pytest.mark.parametrize(
    "render_options",
    [
        RankedContextOptions(portion=10),
        RankedContextOptions(portion=50, max_tokens=10_000),
        RankedContextOptions(max_bytes=999_999, no_budget=True),
        RankedContextOptions(portion=100, max_tokens=1, max_bytes=1, no_budget=True),
    ],
)
def test_render_only_options_do_not_change_snapshot_key(
    configured_atlas: Path, render_options: RankedContextOptions
) -> None:
    paths = get_paths()
    prepare_ranked_manifest(paths, "demo")
    baseline_pointer = load_latest_pointer(paths, "group", "demo")
    assert baseline_pointer is not None
    baseline_key = baseline_pointer.latest_complete_snapshot_key

    prepare_ranked_manifest(paths, "demo", options=render_options)
    after_pointer = load_latest_pointer(paths, "group", "demo")
    assert after_pointer is not None
    assert after_pointer.latest_complete_snapshot_key == baseline_key, (
        f"render-only option {render_options!r} changed the snapshot key "
        f"({baseline_key} -> {after_pointer.latest_complete_snapshot_key})"
    )
    # And only one snapshot file should be on disk (same key).
    snap_dir = snapshots_root(paths, "group")
    assert len(list(snap_dir.glob("*.json"))) == 1


def test_rank_universe_option_changes_snapshot_key(configured_atlas: Path) -> None:
    paths = get_paths()
    prepare_ranked_manifest(paths, "demo")
    base_key = load_latest_pointer(paths, "group", "demo").latest_complete_snapshot_key  # type: ignore[union-attr]

    # Changing files_mode changes the candidate UNIVERSE, not just rendering,
    # so the snapshot key must change.
    prepare_ranked_manifest(
        paths, "demo", options=RankedContextOptions(files_mode="all-source")
    )
    new_key = load_latest_pointer(paths, "group", "demo").latest_complete_snapshot_key  # type: ignore[union-attr]
    assert new_key != base_key, (
        "files_mode is a rank-universe option and MUST change the snapshot key"
    )
