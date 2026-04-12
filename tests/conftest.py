from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def atlas_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "jb"
    config = tmp_path / "config"
    code = tmp_path / "code"
    home.mkdir()
    config.mkdir()
    code.mkdir()
    monkeypatch.setenv("ATLAS_ONCE_HOME", str(home))
    monkeypatch.setenv("ATLAS_ONCE_CONFIG_HOME", str(config))
    monkeypatch.setenv("ATLAS_ONCE_CODE_ROOT", str(code))
    return tmp_path
