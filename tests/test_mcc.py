from __future__ import annotations

from pathlib import Path

from atlas_once.multi_ctx import main


def test_mcc_dashboard_shows_presets(atlas_env: Path, capsys) -> None:
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "mcc: multi-repo mixctx runner" in out
    assert "No presets saved." in out


def test_mcc_remember_and_list(atlas_env: Path, capsys) -> None:
    repo = atlas_env / "repo"
    repo.mkdir()
    assert main(["--remember", str(repo)]) == 0
    assert capsys.readouterr().out.strip() == "1"
    assert main(["list"]) == 0
    out = capsys.readouterr().out
    assert "1. repo" in out


def test_mcc_uses_mixctx_binary_override(atlas_env: Path, monkeypatch, capsys) -> None:
    repo = atlas_env / "repo"
    repo.mkdir()
    fake = atlas_env / "fake-mixctx"
    fake.write_text('#!/usr/bin/env bash\necho "CTX:$*"\n', encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("ATLAS_ONCE_MIXCTX_BIN", str(fake))
    assert main([str(repo)]) == 0
    assert "CTX:" in capsys.readouterr().out
