from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def atlas_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    tmp_home = tmp_path / "home"
    home = tmp_path / "jb"
    config = tmp_path / "config"
    code = tmp_path / "code"
    tmp_home.mkdir()
    home.mkdir()
    config.mkdir()
    code.mkdir()
    monkeypatch.setenv("HOME", str(tmp_home))
    monkeypatch.setenv("ATLAS_ONCE_HOME", str(home))
    monkeypatch.setenv("ATLAS_ONCE_CONFIG_HOME", str(config))
    monkeypatch.setenv("ATLAS_ONCE_CODE_ROOT", str(code))
    return tmp_path


@pytest.fixture
def atlas_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in (
        "ATLAS_ONCE_HOME",
        "ATLAS_ONCE_CONFIG_HOME",
        "ATLAS_ONCE_STATE_HOME",
        "ATLAS_ONCE_CODE_ROOT",
        "ATLAS_ONCE_PROJECT_ROOTS",
    ):
        monkeypatch.delenv(name, raising=False)
    return home
