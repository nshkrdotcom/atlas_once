"""Phase 5 — cache/tree/plan are cheap snapshot views.

Docset requirement: with the fast path enabled and a snapshot on disk,

    atlas context ranked cache <scope>
    atlas context ranked tree  <scope>
    atlas context ranked plan  <scope>

must not call the legacy ``_build_prepared_manifest`` or
``subprocess.run`` (Dexterity), must not mutate the latest pointer,
and must include ``ranked_snapshot`` + ``render_view`` (or just
``ranked_snapshot``) in their JSON envelope.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_ranked_snapshot_fast_path import _seed_snapshot_on_disk

from atlas_once.atlas import main as atlas_main
from atlas_once.config import get_paths
from atlas_once.ranked_snapshot import latest_pointer_path


def _seed_minimal_config(atlas_env: Path) -> None:
    cfg_dir = atlas_env / "config" / "atlas_once"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "ranked_contexts.json").write_text(
        json.dumps(
            {
                "version": 3,
                "defaults": {
                    "registry": {"self_owners": ["nshkrdotcom"]},
                    "runtime": {"dexterity_root": str(atlas_env / "dx")},
                    "strategies": {"elixir_ranked_v1": {"top_files": 3}},
                },
                "repos": {},
                "groups": {
                    "fixture": {
                        "items": [{"ref": "placeholder", "variant": "default"}]
                    }
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )


@pytest.fixture
def fast_path_atlas(
    atlas_env: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    _seed_snapshot_on_disk(atlas_env, scope_id="fixture")
    _seed_minimal_config(atlas_env)
    monkeypatch.setenv("ATLAS_ONCE_RANKED_FAST_PATH", "1")

    def boom_builder(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "Phase 5 fast-path views must not call _build_prepared_manifest"
        )

    def boom_subprocess(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "Phase 5 fast-path views must not call subprocess.run (Dexterity)"
        )

    monkeypatch.setattr(
        "atlas_once.ranked_context._build_prepared_manifest", boom_builder
    )
    monkeypatch.setattr(
        "atlas_once.ranked_context.subprocess.run", boom_subprocess
    )
    return atlas_env


def test_cache_fast_path(
    fast_path_atlas: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert atlas_main(["--json", "context", "ranked", "cache", "fixture"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["command"] == "context.ranked.cache"
    data = payload["data"]
    assert data["ranked_snapshot"]["source"] == "snapshot_fast_path"
    assert "render_view" in data
    assert data["render_view"]["candidate_count_before_portion"] >= 1


def test_plan_fast_path(
    fast_path_atlas: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert atlas_main(
        ["--json", "context", "ranked", "plan", "fixture", "--portion", "50"]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "context.ranked.plan"
    data = payload["data"]
    assert data["render_view"]["portion"] == 50
    assert data["selection_plan"]["files"] >= 1
    assert data["ranked_snapshot"]["source"] == "snapshot_fast_path"


def test_tree_fast_path(
    fast_path_atlas: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert atlas_main(["--json", "context", "ranked", "tree", "fixture"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "context.ranked.tree"
    data = payload["data"]
    assert data["ranked_snapshot"]["source"] == "snapshot_fast_path"
    repos = data["tree"]["repos"]
    assert "demo" in repos
    # Files are grouped under the first path segment (e.g. "lib").
    project_block = repos["demo"]
    assert "lib" in project_block
    assert any(p.endswith("a.ex") for p in project_block["lib"])


def test_cache_plan_tree_do_not_advance_pointer(
    fast_path_atlas: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = get_paths()
    pointer_path = latest_pointer_path(paths, "group", "fixture")
    mtime_before = pointer_path.stat().st_mtime_ns

    for cmd in [
        ["--json", "context", "ranked", "cache", "fixture"],
        ["--json", "context", "ranked", "plan", "fixture", "--portion", "10"],
        ["--json", "context", "ranked", "plan", "fixture", "--portion", "100"],
        ["--json", "context", "ranked", "tree", "fixture"],
    ]:
        capsys.readouterr()
        assert atlas_main(cmd) == 0

    assert pointer_path.stat().st_mtime_ns == mtime_before
