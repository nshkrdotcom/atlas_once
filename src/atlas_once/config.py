from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_PROJECT_ROOT_CANDIDATES = ("~/p/g/n", "~/p/g/North-Shore-AI")


def _resolve_env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).expanduser().resolve()


def _config_home() -> Path:
    if "ATLAS_ONCE_CONFIG_HOME" in os.environ:
        return _resolve_env_path("ATLAS_ONCE_CONFIG_HOME", "~/.config")
    return Path.home() / ".config"


@dataclass(frozen=True)
class AtlasSettings:
    project_roots: list[str]
    auto_sync_relationships: bool = True
    review_window_days: int = 7


@dataclass(frozen=True)
class AtlasPaths:
    data_home: Path
    state_home: Path
    code_root: Path
    legacy_config_home: Path

    @property
    def docs_root(self) -> Path:
        return self.data_home / "docs"

    @property
    def mem_root(self) -> Path:
        return self.data_home / "mem"

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
    def registry_root(self) -> Path:
        return self.state_home / "registry"

    @property
    def indexes_root(self) -> Path:
        return self.state_home / "indexes"

    @property
    def presets_root(self) -> Path:
        return self.state_home / "presets"

    @property
    def cache_root(self) -> Path:
        return self.state_home / "cache"

    @property
    def bundle_cache_root(self) -> Path:
        return self.cache_root / "bundles"

    @property
    def locks_root(self) -> Path:
        return self.state_home / "locks"

    @property
    def settings_path(self) -> Path:
        return self.state_home / "settings.json"

    @property
    def registry_path(self) -> Path:
        return self.registry_root / "projects.json"

    @property
    def registry_meta_path(self) -> Path:
        return self.registry_root / "meta.json"

    @property
    def relationships_path(self) -> Path:
        return self.indexes_root / "relationships.json"

    @property
    def events_path(self) -> Path:
        return self.state_home / "events.jsonl"

    @property
    def mcc_preset_path(self) -> Path:
        return self.presets_root / "mcc.json"

    @property
    def legacy_mcc_preset_path(self) -> Path:
        return self.legacy_config_home / "mcc" / "presets.json"

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
    if "ATLAS_ONCE_STATE_HOME" in os.environ:
        state_home = _resolve_env_path("ATLAS_ONCE_STATE_HOME", "~/.atlas_once")
    elif "ATLAS_ONCE_CONFIG_HOME" in os.environ:
        state_home = _resolve_env_path("ATLAS_ONCE_CONFIG_HOME", "~/.config") / "atlas_once"
    else:
        state_home = Path.home() / ".atlas_once"

    return AtlasPaths(
        data_home=_resolve_env_path("ATLAS_ONCE_HOME", "~/jb"),
        state_home=state_home.expanduser().resolve(),
        code_root=_resolve_env_path("ATLAS_ONCE_CODE_ROOT", "~/p/g/n"),
        legacy_config_home=_config_home(),
    )


def default_project_roots(paths: AtlasPaths) -> list[str]:
    raw_roots = os.environ.get("ATLAS_ONCE_PROJECT_ROOTS")
    if raw_roots:
        return [
            str(Path(item).expanduser().resolve())
            for item in raw_roots.split(os.pathsep)
            if item.strip()
        ]

    if "ATLAS_ONCE_CODE_ROOT" in os.environ:
        return [str(paths.code_root)]

    roots: list[str] = []
    for candidate in DEFAULT_PROJECT_ROOT_CANDIDATES:
        resolved = Path(candidate).expanduser().resolve()
        if resolved.is_dir():
            roots.append(str(resolved))
    return roots


def default_settings(paths: AtlasPaths) -> AtlasSettings:
    return AtlasSettings(project_roots=default_project_roots(paths))


def save_settings(paths: AtlasPaths, settings: AtlasSettings) -> None:
    paths.settings_path.parent.mkdir(parents=True, exist_ok=True)
    paths.settings_path.write_text(
        json.dumps(asdict(settings), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_settings(paths: AtlasPaths) -> AtlasSettings:
    if not paths.settings_path.is_file():
        return default_settings(paths)

    payload = json.loads(paths.settings_path.read_text(encoding="utf-8"))
    project_roots = [
        str(Path(item).expanduser().resolve())
        for item in payload.get("project_roots", [])
        if str(item).strip()
    ]
    return AtlasSettings(
        project_roots=project_roots,
        auto_sync_relationships=bool(payload.get("auto_sync_relationships", True)),
        review_window_days=int(payload.get("review_window_days", 7)),
    )


def ensure_state(paths: AtlasPaths) -> AtlasSettings:
    for directory in (
        paths.docs_root,
        paths.inbox_root,
        paths.sessions_root,
        paths.projects_root,
        paths.decisions_root,
        paths.people_root,
        paths.topics_root,
        paths.snapshots_root,
        paths.registry_root,
        paths.indexes_root,
        paths.presets_root,
        paths.cache_root,
        paths.bundle_cache_root,
        paths.locks_root,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    settings = load_settings(paths)

    if not paths.settings_path.exists():
        save_settings(paths, settings)

    if not paths.mcc_preset_path.exists() and paths.legacy_mcc_preset_path.exists():
        paths.mcc_preset_path.parent.mkdir(parents=True, exist_ok=True)
        paths.mcc_preset_path.write_text(
            paths.legacy_mcc_preset_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    return settings
