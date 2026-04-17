from __future__ import annotations

import tomllib
from pathlib import Path


def test_legacy_helper_console_scripts_are_installed() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    scripts = payload["project"]["scripts"]

    assert scripts["ctx"] == "atlas_once.markdown_ctx:main"
    assert scripts["mixctx"] == "atlas_once.mix_ctx:main"
    assert scripts["mctx"] == "atlas_once.mix_ctx:main"
    assert scripts["mcc"] == "atlas_once.multi_ctx:main"
