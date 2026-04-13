from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

GENERIC_DATA_HOME = "~/atlas_once"


def _resolve_env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).expanduser().resolve()


def _legacy_config_home() -> Path:
    if "ATLAS_ONCE_CONFIG_HOME" in os.environ:
        return _resolve_env_path("ATLAS_ONCE_CONFIG_HOME", "~/.config")
    return Path.home() / ".config"


def _config_home() -> Path:
    if "ATLAS_ONCE_CONFIG_HOME" in os.environ:
        return _resolve_env_path("ATLAS_ONCE_CONFIG_HOME", "~/.config") / "atlas_once"
    return (Path.home() / ".config" / "atlas_once").resolve()


def _state_home(config_home: Path) -> Path:
    if "ATLAS_ONCE_STATE_HOME" in os.environ:
        return _resolve_env_path("ATLAS_ONCE_STATE_HOME", "~/.atlas_once")
    if "ATLAS_ONCE_CONFIG_HOME" in os.environ:
        return config_home
    return (Path.home() / ".atlas_once").resolve()


def _normalize_path(value: str) -> str:
    return str(Path(value).expanduser().resolve())


def _normalize_optional_path(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return _normalize_path(text)


def _normalize_path_list(values: list[Any]) -> list[str]:
    return [_normalize_path(str(item).strip()) for item in values if str(item).strip()]


@dataclass(frozen=True)
class AtlasSettings:
    data_home: str
    code_root: str | None
    project_roots: list[str]
    auto_sync_relationships: bool = True
    review_window_days: int = 7


@dataclass(frozen=True)
class AtlasProfileState:
    name: str
    source: str = "packaged"
    customized: bool = False


@dataclass(frozen=True)
class AtlasPaths:
    config_home: Path
    state_home: Path
    data_home: Path
    code_root: Path | None
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
    def ranked_context_cache_root(self) -> Path:
        return self.cache_root / "ranked_contexts"

    @property
    def locks_root(self) -> Path:
        return self.state_home / "locks"

    @property
    def shell_root(self) -> Path:
        return self.config_home / "shell"

    @property
    def settings_path(self) -> Path:
        return self.config_home / "settings.json"

    @property
    def profile_state_path(self) -> Path:
        return self.config_home / "profile.json"

    @property
    def bash_shell_path(self) -> Path:
        return self.shell_root / "atlas_once.sh"

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

    @property
    def ranked_contexts_path(self) -> Path:
        return self.config_home / "ranked_contexts.json"

    @property
    def ranked_contexts_state_path(self) -> Path:
        return self.config_home / "ranked_contexts.state.json"


def default_settings() -> AtlasSettings:
    raw_roots = os.environ.get("ATLAS_ONCE_PROJECT_ROOTS")
    if raw_roots:
        project_roots = _normalize_path_list(
            [item for item in raw_roots.split(os.pathsep) if item.strip()]
        )
    else:
        project_roots = []

    code_root = _normalize_optional_path(os.environ.get("ATLAS_ONCE_CODE_ROOT"))
    if code_root is not None and not project_roots:
        project_roots = [code_root]

    data_home = _normalize_path(os.environ.get("ATLAS_ONCE_HOME", GENERIC_DATA_HOME))
    return AtlasSettings(
        data_home=data_home,
        code_root=code_root,
        project_roots=project_roots,
        auto_sync_relationships=True,
        review_window_days=7,
    )


def save_settings(paths: AtlasPaths, settings: AtlasSettings) -> None:
    normalized = AtlasSettings(
        data_home=_normalize_path(settings.data_home),
        code_root=_normalize_optional_path(settings.code_root),
        project_roots=_normalize_path_list(settings.project_roots),
        auto_sync_relationships=settings.auto_sync_relationships,
        review_window_days=settings.review_window_days,
    )
    paths.settings_path.parent.mkdir(parents=True, exist_ok=True)
    paths.settings_path.write_text(
        json.dumps(asdict(normalized), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_settings(paths: AtlasPaths) -> AtlasSettings:
    defaults = default_settings()
    if not paths.settings_path.is_file():
        return defaults

    payload = json.loads(paths.settings_path.read_text(encoding="utf-8"))
    return AtlasSettings(
        data_home=_normalize_path(str(payload.get("data_home", defaults.data_home))),
        code_root=_normalize_optional_path(payload.get("code_root", defaults.code_root)),
        project_roots=_normalize_path_list(payload.get("project_roots", defaults.project_roots)),
        auto_sync_relationships=bool(
            payload.get("auto_sync_relationships", defaults.auto_sync_relationships)
        ),
        review_window_days=int(payload.get("review_window_days", defaults.review_window_days)),
    )


def save_profile_state(paths: AtlasPaths, state: AtlasProfileState) -> None:
    paths.profile_state_path.parent.mkdir(parents=True, exist_ok=True)
    paths.profile_state_path.write_text(
        json.dumps(asdict(state), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_profile_state(paths: AtlasPaths) -> AtlasProfileState | None:
    if not paths.profile_state_path.is_file():
        return None
    payload = json.loads(paths.profile_state_path.read_text(encoding="utf-8"))
    return AtlasProfileState(
        name=str(payload["name"]),
        source=str(payload.get("source", "packaged")),
        customized=bool(payload.get("customized", False)),
    )


def mark_profile_customized(paths: AtlasPaths, customized: bool = True) -> AtlasProfileState | None:
    state = load_profile_state(paths)
    if state is None:
        return None
    updated = AtlasProfileState(name=state.name, source=state.source, customized=customized)
    save_profile_state(paths, updated)
    return updated


def get_paths() -> AtlasPaths:
    config_home = _config_home()
    state_home = _state_home(config_home)
    legacy_config_home = _legacy_config_home()

    # Load persisted settings if available, then layer env overrides on top.
    settings_path = config_home / "settings.json"
    settings = default_settings()
    if settings_path.is_file():
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
        settings = AtlasSettings(
            data_home=_normalize_path(str(payload.get("data_home", settings.data_home))),
            code_root=_normalize_optional_path(payload.get("code_root", settings.code_root)),
            project_roots=_normalize_path_list(
                payload.get("project_roots", settings.project_roots)
            ),
            auto_sync_relationships=bool(
                payload.get("auto_sync_relationships", settings.auto_sync_relationships)
            ),
            review_window_days=int(payload.get("review_window_days", settings.review_window_days)),
        )

    data_home = Path(os.environ.get("ATLAS_ONCE_HOME", settings.data_home)).expanduser().resolve()
    code_root_text = os.environ.get("ATLAS_ONCE_CODE_ROOT")
    code_root = (
        Path(code_root_text).expanduser().resolve()
        if code_root_text
        else (Path(settings.code_root).expanduser().resolve() if settings.code_root else None)
    )

    return AtlasPaths(
        config_home=config_home,
        state_home=state_home,
        data_home=data_home,
        code_root=code_root,
        legacy_config_home=legacy_config_home,
    )


def ensure_state(paths: AtlasPaths) -> AtlasSettings:
    for directory in (
        paths.config_home,
        paths.shell_root,
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
        paths.ranked_context_cache_root,
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
