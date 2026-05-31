from __future__ import annotations

import json
from pathlib import Path

from atlas_once.config import get_paths
from atlas_once.ranked_context import ranked_group_repo_summaries
from atlas_once.registry import ProjectRecord, save_registry


def _write_ranked_config(atlas_env: Path) -> None:
    config_path = atlas_env / "config" / "atlas_once" / "ranked_contexts.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "version": 3,
                "defaults": {
                    "registry": {"self_owners": ["nshkrdotcom"]},
                    "runtime": {"dexterity_root": str(atlas_env / "dexterity")},
                    "strategies": {"elixir_ranked_v1": {"top_files": 3}},
                },
                "repos": {},
                "groups": {
                    "owned": {
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
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _record(name: str, path: Path, aliases: list[str] | None = None) -> ProjectRecord:
    return ProjectRecord(
        name=name,
        slug=name.lower().replace("_", "-"),
        path=str(path),
        root=str(path.parent),
        aliases=aliases if aliases is not None else [name.lower()],
        manual_aliases=[],
        markers=["mix.exs"],
        last_scanned="test",
        languages=["elixir"],
        primary_language="elixir",
        owner_scope="self",
        relation="primary",
        capabilities={"elixir_ranked_v1": True},
    )


def test_selector_resolution_uses_selected_record_path_not_ambiguous_name(
    atlas_env: Path,
) -> None:
    paths = get_paths()
    lower = atlas_env / "code" / "elixir_scope"
    upper = atlas_env / "code" / "ElixirScope"
    lower.mkdir(parents=True)
    upper.mkdir(parents=True)
    _write_ranked_config(atlas_env)
    save_registry(
        paths,
        [
            _record("elixir_scope", lower, aliases=["elixir_scope"]),
            _record("ElixirScope", upper, aliases=["elixirscope", "elixir_scope"]),
        ],
    )

    repos = ranked_group_repo_summaries(paths, "owned")

    assert repos["repo_count"] == 2
    assert sorted(repo["repo_root"] for repo in repos["repos"]) == sorted(
        [str(lower), str(upper)]
    )
