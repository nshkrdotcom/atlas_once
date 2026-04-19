from __future__ import annotations

import json
from pathlib import Path

from atlas_once.atlas import main


def _write_ranked_runtime(atlas_env: Path) -> None:
    config_path = atlas_env / "config" / "atlas_once" / "ranked_contexts.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "version": 3,
                "defaults": {
                    "registry": {"self_owners": []},
                    "runtime": {"dexterity_root": str(atlas_env / "dexterity")},
                    "strategies": {},
                },
                "repos": {},
                "groups": {},
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def test_unknown_index_refresh_project_returns_structured_error(atlas_env: Path, capsys) -> None:
    _write_ranked_runtime(atlas_env)

    assert main(["--json", "index", "refresh", "--project", "missing-project"]) != 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is False
    assert payload["command"] == "index.refresh"
    assert payload["errors"]
    assert "traceback" not in json.dumps(payload).lower()
