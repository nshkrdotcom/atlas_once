from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from atlas_once.config import AtlasPaths, ensure_state, get_paths, load_profile_state
from atlas_once.profiles import DEFAULT_INSTALL_PROFILE, get_profile

from .tools import iter_tool_definitions

SUPPORTED_CLIENTS = ("codex", "generic")


@dataclass(frozen=True)
class McpInstallResult:
    path: Path
    changed: bool
    config: dict[str, Any]
    codex_skill_path: Path
    codex_skill_changed: bool


def active_or_default_profile(paths: AtlasPaths, explicit: str | None = None) -> str:
    if explicit:
        get_profile(explicit)
        return explicit
    state = load_profile_state(paths)
    return state.name if state is not None else DEFAULT_INSTALL_PROFILE


def mcp_config_path(paths: AtlasPaths, client: str = "codex") -> Path:
    if client not in SUPPORTED_CLIENTS:
        raise SystemExit(f"Unknown MCP client: {client}")
    return paths.mcp_root / f"{client}.mcp.json"


def codex_skill_install_path(paths: AtlasPaths) -> Path:
    return paths.mcp_root / "agent" / "atlas-codex"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _skill_asset_text(relative_path: str) -> str:
    checkout_path = _repo_root() / "assets" / "agent" / "atlas-codex" / relative_path
    if checkout_path.is_file():
        return checkout_path.read_text(encoding="utf-8")
    package_path = files("atlas_once") / "mcp_assets" / "agent" / "atlas-codex" / relative_path
    return package_path.read_text(encoding="utf-8")


def install_codex_skill_asset(paths: AtlasPaths) -> tuple[Path, bool]:
    target_root = codex_skill_install_path(paths)
    changed = False
    for relative_path in ("SKILL.md", "agents/openai.yaml"):
        content = _skill_asset_text(relative_path)
        target = target_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        existing = target.read_text(encoding="utf-8") if target.is_file() else None
        if existing == content:
            continue
        target.write_text(content, encoding="utf-8")
        changed = True
    return target_root, changed


def server_config(profile: str) -> dict[str, Any]:
    return {
        "command": "atlas-mcp",
        "args": [],
        "env": {
            "ATLAS_ONCE_PROFILE": profile,
        },
    }


def mcp_client_config(*, profile: str, client: str = "codex") -> dict[str, Any]:
    get_profile(profile)
    if client not in SUPPORTED_CLIENTS:
        raise SystemExit(f"Unknown MCP client: {client}")
    return {
        "client": client,
        "mcpServers": {
            "atlas-once": server_config(profile),
        },
    }


def mcp_show_data(
    paths: AtlasPaths | None = None,
    *,
    client: str = "codex",
    profile: str | None = None,
) -> dict[str, Any]:
    resolved_paths = paths or get_paths()
    ensure_state(resolved_paths)
    profile_name = active_or_default_profile(resolved_paths, profile)
    config = mcp_client_config(profile=profile_name, client=client)
    path = mcp_config_path(resolved_paths, client)
    return {
        "server_name": "atlas-once",
        "client": client,
        "profile": profile_name,
        "path": str(path),
        "codex_skill_path": str(codex_skill_install_path(resolved_paths)),
        "server": config["mcpServers"]["atlas-once"],
        "config": config,
    }


def install_mcp_config(
    paths: AtlasPaths | None = None,
    *,
    client: str = "codex",
    profile: str | None = None,
) -> McpInstallResult:
    resolved_paths = paths or get_paths()
    ensure_state(resolved_paths)
    data = mcp_show_data(resolved_paths, client=client, profile=profile)
    config = data["config"]
    path = mcp_config_path(resolved_paths, client)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(config, indent=2, sort_keys=True) + "\n"
    existing = path.read_text(encoding="utf-8") if path.is_file() else None
    config_changed = existing != content
    if config_changed:
        path.write_text(content, encoding="utf-8")
    skill_path, skill_changed = install_codex_skill_asset(resolved_paths)
    return McpInstallResult(
        path=path,
        changed=config_changed or skill_changed,
        config=config,
        codex_skill_path=skill_path,
        codex_skill_changed=skill_changed,
    )


def doctor_data(
    paths: AtlasPaths | None = None,
    *,
    client: str = "codex",
    profile: str | None = None,
) -> dict[str, Any]:
    resolved_paths = paths or get_paths()
    ensure_state(resolved_paths)
    show = mcp_show_data(resolved_paths, client=client, profile=profile)
    definitions = list(iter_tool_definitions())
    read_tools = [definition for definition in definitions if definition.access == "read"]
    write_tools = [definition for definition in definitions if definition.access == "write"]
    active = load_profile_state(resolved_paths)
    command_path = shutil.which("atlas-mcp")
    return {
        "mcp_available": True,
        "server": {
            "command": "atlas-mcp",
            "command_path": command_path,
            "config": show["server"],
        },
        "client": client,
        "profile": {
            "active": active.name if active is not None else None,
            "install_default": DEFAULT_INSTALL_PROFILE,
            "selected": show["profile"],
        },
        "paths": {
            "config_home": str(resolved_paths.config_home),
            "state_home": str(resolved_paths.state_home),
            "data_home": str(resolved_paths.data_home),
            "mcp_config": show["path"],
            "codex_skill": show["codex_skill_path"],
            "docs": str(Path(__file__).resolve().parents[3] / "docs" / "mcp.md"),
        },
        "tools": {
            "total": len(definitions),
            "read": len(read_tools),
            "write": len(write_tools),
            "names": [definition.name for definition in definitions],
        },
    }
