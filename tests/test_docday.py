from __future__ import annotations

import os
from pathlib import Path

from atlas_once.nav import main


def test_docday_numeric_creates_docs_dir(atlas_env: Path, capsys) -> None:
    assert main(["1"]) == 0
    out = capsys.readouterr().out
    assert out.endswith("/jb/docs/" + __import__("datetime").date.today().strftime("%Y%m%d"))


def test_docday_letter_uses_recent_code_dirs(atlas_env: Path, capsys) -> None:
    code_root = atlas_env / "code"
    newest = code_root / "newest"
    older = code_root / "older"
    newest.mkdir()
    older.mkdir()
    os.utime(older, (1, 1))
    os.utime(newest, (2, 2))

    assert main(["a"]) == 0
    out = capsys.readouterr().out
    assert out.endswith("/code/newest")
