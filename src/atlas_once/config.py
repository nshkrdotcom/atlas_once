from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _resolve_env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).expanduser().resolve()


@dataclass(frozen=True)
class AtlasPaths:
    home: Path
    config_home: Path
    code_root: Path

    @property
    def docs_root(self) -> Path:
        return self.home / "docs"

    @property
    def mem_root(self) -> Path:
        return self.home / "mem"

    @property
    def inbox_root(self) -> Path:
        return self.mem_root / "inbox"

    @property
    def sessions_root(self) -> Path:
        return self.mem_root / "sessions"

    @property
    def projects_root(self) -> Path:
        return self.mem_root / "projects"

    @property
    def decisions_root(self) -> Path:
        return self.mem_root / "decisions"

    @property
    def people_root(self) -> Path:
        return self.mem_root / "people"

    @property
    def topics_root(self) -> Path:
        return self.mem_root / "topics"

    @property
    def snapshots_root(self) -> Path:
        return self.mem_root / "snapshots"

    @property
    def indexes_root(self) -> Path:
        return self.mem_root / "indexes"

    @property
    def mcc_config_root(self) -> Path:
        return self.config_home / "mcc"

    @property
    def mcc_preset_path(self) -> Path:
        return self.mcc_config_root / "presets.json"

    @property
    def project_index_path(self) -> Path:
        return self.indexes_root / "projects.json"

    @property
    def tag_index_path(self) -> Path:
        return self.indexes_root / "tags.json"

    @property
    def link_index_path(self) -> Path:
        return self.indexes_root / "links.json"


def get_paths() -> AtlasPaths:
    return AtlasPaths(
        home=_resolve_env_path("ATLAS_ONCE_HOME", "~/jb"),
        config_home=_resolve_env_path("ATLAS_ONCE_CONFIG_HOME", "~/.config"),
        code_root=_resolve_env_path("ATLAS_ONCE_CODE_ROOT", "~/p/g/n"),
    )
