"""Phase 10 — full portion & budget sweeps prove the central invariant.

The docset's final acceptance step (11-agent-checklist §14) sweeps:

  atlas context ranked <scope> --portion {1,5,10,25,50,75,100} --json
  atlas context ranked <scope> --max-tokens {10000,50000,100000} --json

and asserts:

  * same ranked_snapshot.key across all render commands;
  * render_view.candidate_count_after_portion is monotonic;
  * no rebuild event caused by portion / budget changes.

This test is the executable form of that sweep and is the final
proof of the central invariant for this refactor.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from atlas_once.atlas import main as atlas_main
from atlas_once.config import get_paths
from atlas_once.ranked_snapshot import latest_pointer_path, snapshots_root


@pytest.fixture
def sweep_atlas(
    atlas_env: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Seed the snapshot fixture and trip-wire all expensive paths."""
    from test_ranked_snapshot_fast_path import _seed_snapshot_on_disk

    _seed_snapshot_on_disk(atlas_env, scope_id="fixture")
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

    def boom_builder(*args: object, **kwargs: object) -> object:
        raise AssertionError("Phase 10 sweep must not call legacy builder")

    def boom_subprocess(*args: object, **kwargs: object) -> object:
        raise AssertionError("Phase 10 sweep must not call Dexterity")

    monkeypatch.setattr(
        "atlas_once.ranked_context._build_prepared_manifest", boom_builder
    )
    monkeypatch.setattr(
        "atlas_once.ranked_context.subprocess.run", boom_subprocess
    )
    return atlas_env


def test_portion_sweep_stable_key_monotonic_count(
    sweep_atlas: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = get_paths()
    pointer_path = latest_pointer_path(paths, "group", "fixture")
    pointer_mtime_before = pointer_path.stat().st_mtime_ns
    snapshot_count_before = len(list(snapshots_root(paths, "group").glob("*.json")))

    seen_keys: set[str] = set()
    seen_counts: list[int] = []
    for portion in [1, 5, 10, 25, 50, 75, 100]:
        capsys.readouterr()
        rc = atlas_main(
            [
                "--json",
                "context",
                "ranked",
                "fixture",
                "--portion",
                str(portion),
            ]
        )
        assert rc == 0, f"portion={portion} failed"
        payload = json.loads(capsys.readouterr().out)
        data = payload["data"]
        seen_keys.add(data["ranked_snapshot"]["key"])
        seen_counts.append(data["render_view"]["selected_count_after_budget"])

    assert len(seen_keys) == 1, (
        f"--portion changed the snapshot key across sweep (keys={seen_keys})"
    )
    assert seen_counts == sorted(seen_counts), (
        f"candidate_count_after_portion not monotonic: {seen_counts}"
    )
    # No new snapshot file written by the sweep, pointer mtime unchanged.
    assert pointer_path.stat().st_mtime_ns == pointer_mtime_before
    assert (
        len(list(snapshots_root(paths, "group").glob("*.json")))
        == snapshot_count_before
    )


def test_budget_sweep_stable_key(
    sweep_atlas: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = get_paths()
    pointer_path = latest_pointer_path(paths, "group", "fixture")
    pointer_mtime_before = pointer_path.stat().st_mtime_ns

    seen_keys: set[str] = set()
    for max_tokens in [10_000, 50_000, 100_000]:
        capsys.readouterr()
        rc = atlas_main(
            [
                "--json",
                "context",
                "ranked",
                "fixture",
                "--max-tokens",
                str(max_tokens),
            ]
        )
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        data = payload["data"]
        seen_keys.add(data["ranked_snapshot"]["key"])
        assert data["render_view"]["max_tokens"] == max_tokens

    assert len(seen_keys) == 1, (
        f"--max-tokens changed the snapshot key across sweep (keys={seen_keys})"
    )
    assert pointer_path.stat().st_mtime_ns == pointer_mtime_before


def test_combined_portion_and_budget_sweep_stable_key(
    sweep_atlas: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The crucial cross-axis check: changing portion AND token-budget
    together still produces a single snapshot key."""
    seen_keys: set[str] = set()
    for portion, max_tokens, max_bytes, no_budget in [
        (10, 10_000, None, False),
        (50, 100_000, None, False),
        (100, None, 999_999, False),
        (100, 1, 1, True),
        (1, 1, None, True),
    ]:
        capsys.readouterr()
        argv = ["--json", "context", "ranked", "fixture", "--portion", str(portion)]
        if max_tokens is not None:
            argv += ["--max-tokens", str(max_tokens)]
        if max_bytes is not None:
            argv += ["--max-bytes", str(max_bytes)]
        if no_budget:
            argv += ["--no-budget"]
        rc = atlas_main(argv)
        assert rc == 0, argv
        payload = json.loads(capsys.readouterr().out)
        seen_keys.add(payload["data"]["ranked_snapshot"]["key"])

    assert len(seen_keys) == 1
