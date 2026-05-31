"""Phase 9 — snapshot fast path is the default render path.

Confirms ATLAS_ONCE_RANKED_FAST_PATH is opt-OUT now (env unset = on)
and that the legacy escape hatch (=0 / false / no / off) still works
during the migration window.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from atlas_once.atlas import _ranked_fast_path_enabled
from atlas_once.atlas import main as atlas_main


def test_fast_path_enabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ATLAS_ONCE_RANKED_FAST_PATH", raising=False)
    assert _ranked_fast_path_enabled() is True


@pytest.mark.parametrize("disable_value", ["0", "false", "no", "off", "FALSE"])
def test_fast_path_disabled_explicitly(
    monkeypatch: pytest.MonkeyPatch, disable_value: str
) -> None:
    monkeypatch.setenv("ATLAS_ONCE_RANKED_FAST_PATH", disable_value)
    assert _ranked_fast_path_enabled() is False


def test_default_render_uses_snapshot_when_present(
    atlas_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No env override: the default CLI path should serve the snapshot
    and emit the new JSON sections."""
    from test_ranked_snapshot_fast_path import _seed_snapshot_on_disk

    monkeypatch.delenv("ATLAS_ONCE_RANKED_FAST_PATH", raising=False)
    snapshot = _seed_snapshot_on_disk(atlas_env, scope_id="fixture")

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
        raise AssertionError(
            "default render must use the snapshot fast path"
        )

    def boom_subprocess(*args: object, **kwargs: object) -> object:
        raise AssertionError("default render must not call Dexterity")

    monkeypatch.setattr(
        "atlas_once.ranked_context._build_prepared_manifest", boom_builder
    )
    monkeypatch.setattr(
        "atlas_once.ranked_context.subprocess.run", boom_subprocess
    )

    assert atlas_main(["--json", "context", "ranked", "fixture"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    data = payload["data"]
    assert data["ranked_snapshot"]["key"] == snapshot.snapshot_key
    assert data["ranked_snapshot"]["source"] == "snapshot_fast_path"
    assert data["render_view"]["selected_count_after_budget"] >= 1
    # Phase 9 compatibility shim — prepared_manifest is still in the JSON.
    assert data["prepared_manifest"]["source"] == "snapshot_fast_path"
    assert data["prepared_manifest"]["file_count"] == (
        data["render_view"]["selected_count_after_budget"]
    )


def test_legacy_render_when_disabled(
    atlas_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """With the escape hatch set, even with a snapshot present, the
    CLI must go through the legacy ensure_prepared_ranked_manifest
    flow (which would call subprocess.run / the builder). We only
    assert that the JSON does NOT contain the fast-path marker."""
    from test_ranked_snapshot_fast_path import _seed_snapshot_on_disk

    _seed_snapshot_on_disk(atlas_env, scope_id="fixture")
    monkeypatch.setenv("ATLAS_ONCE_RANKED_FAST_PATH", "0")

    # The legacy path will hit subprocess.run; stub it to return ok.
    import subprocess

    def stub_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["mix", "dexterity.query"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                json.dumps({"ok": True, "command": "ranked_files", "result": []}),
                "",
            )
        return subprocess.run(cmd, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("atlas_once.ranked_context.subprocess.run", stub_run)

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
                        "items": []
                    }
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    rc = atlas_main(["--json", "context", "ranked", "fixture"])
    # Whether it succeeds or fails, the JSON envelope must NOT carry
    # the fast-path marker.
    output = capsys.readouterr().out
    if rc == 0:
        payload = json.loads(output)
        data = payload.get("data", {})
        snapshot = data.get("ranked_snapshot")
        # Either absent, or sourced from legacy, never fast path.
        if isinstance(snapshot, dict):
            assert snapshot.get("source") != "snapshot_fast_path"
