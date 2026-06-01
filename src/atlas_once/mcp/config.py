from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from atlas_once.config import AtlasPaths, ensure_state, get_paths, load_profile_state
from atlas_once.profiles import DEFAULT_INSTALL_PROFILE, get_profile

from .tools import iter_tool_definitions

SUPPORTED_CLIENTS = ("codex", "generic")
SERVER_NAME = "atlas-once"
SERVER_COMMAND = "atlas-mcp"


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


def codex_add_command(profile: str) -> list[str]:
    return [
        "codex",
        "mcp",
        "add",
        SERVER_NAME,
        "--env",
        f"ATLAS_ONCE_PROFILE={profile}",
        "--",
        SERVER_COMMAND,
    ]


def github_tool_install_command() -> str:
    return "uv tool install git+https://github.com/nshkrdotcom/atlas_once"


def local_checkout_reinstall_command() -> str:
    return "uv tool install --reinstall /path/to/atlas_once"


def mcp_config_path(paths: AtlasPaths, client: str = "codex") -> Path:
    if client not in SUPPORTED_CLIENTS:
        raise SystemExit(f"Unknown MCP client: {client}")
    return paths.mcp_root / f"{client}.mcp.json"


def codex_skill_install_path(paths: AtlasPaths) -> Path:
    return paths.mcp_root / "agent" / "atlas-codex"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _mcp_docs_path() -> str:
    checkout_path = _repo_root() / "docs" / "mcp.md"
    if checkout_path.is_file():
        return str(checkout_path)
    return str(files("atlas_once") / "mcp_assets" / "docs" / "mcp.md")


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
        "command": SERVER_COMMAND,
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
            SERVER_NAME: server_config(profile),
        },
    }


def _path_is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def executable_status(command: str = SERVER_COMMAND) -> dict[str, Any]:
    raw_path = shutil.which(command)
    if raw_path is None:
        return {
            "command": command,
            "available": False,
            "path": None,
            "source": "missing",
            "message": f"{command} was not found on PATH.",
        }

    command_path = Path(raw_path).resolve()
    checkout_venv = _repo_root() / ".venv"
    if _path_is_under(command_path, checkout_venv):
        return {
            "command": command,
            "available": False,
            "path": str(command_path),
            "source": "checkout_venv",
            "message": (
                f"{command} resolves to the checkout virtualenv. Codex starts outside this "
                "checkout and needs a globally installed tool executable."
            ),
        }

    return {
        "command": command,
        "available": True,
        "path": str(command_path),
        "source": "path",
        "message": f"{command} is available on PATH.",
    }


def codex_registration_status(profile: str) -> dict[str, Any]:
    add_command = codex_add_command(profile)
    codex_path = shutil.which("codex")
    if codex_path is None:
        return {
            "available": False,
            "registered": False,
            "command_path": None,
            "server_name": SERVER_NAME,
            "add_command": add_command,
            "get_command": ["codex", "mcp", "get", SERVER_NAME],
            "message": "Codex CLI was not found on PATH.",
        }

    get_command = [codex_path, "mcp", "get", SERVER_NAME]
    try:
        result = subprocess.run(
            get_command,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "available": True,
            "registered": False,
            "command_path": codex_path,
            "server_name": SERVER_NAME,
            "add_command": add_command,
            "get_command": ["codex", "mcp", "get", SERVER_NAME],
            "returncode": None,
            "stdout": "",
            "stderr": str(exc),
            "message": "Codex registration check failed.",
        }
    registered = (
        result.returncode == 0
        and SERVER_NAME in result.stdout
        and f"command: {SERVER_COMMAND}" in result.stdout
    )
    return {
        "available": True,
        "registered": registered,
        "command_path": codex_path,
        "server_name": SERVER_NAME,
        "add_command": add_command,
        "get_command": ["codex", "mcp", "get", SERVER_NAME],
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "message": (
            "Codex has an atlas-once MCP registration."
            if registered
            else "Codex does not have a usable atlas-once MCP registration."
        ),
    }


def profile_readiness(paths: AtlasPaths, selected_profile: str) -> dict[str, Any]:
    active = load_profile_state(paths)
    active_name = active.name if active is not None else None
    ranked_config_exists = paths.ranked_contexts_path.is_file()
    ready = active_name == selected_profile and ranked_config_exists
    return {
        "active": active_name,
        "install_default": DEFAULT_INSTALL_PROFILE,
        "selected": selected_profile,
        "ranked_config_exists": ranked_config_exists,
        "ranked_config_path": str(paths.ranked_contexts_path),
        "ready": ready,
    }


def next_steps_for_status(
    *,
    selected_profile: str,
    executable: dict[str, Any],
    profile_status: dict[str, Any],
    codex_status: dict[str, Any],
) -> list[dict[str, str]]:
    steps: list[dict[str, str]] = []
    if not bool(executable["available"]):
        steps.append(
            {
                "action": "install_atlas_tool",
                "command": github_tool_install_command(),
                "description": "Install Atlas commands, including atlas-mcp, onto PATH.",
            }
        )
    if not bool(profile_status["ready"]):
        steps.append(
            {
                "action": "install_profile",
                "command": f"atlas install --profile {selected_profile}",
                "description": "Install the selected Atlas profile and managed ranked config.",
            }
        )
    if not bool(codex_status["registered"]):
        steps.append(
            {
                "action": "register_codex_mcp",
                "command": " ".join(codex_add_command(selected_profile)),
                "description": "Register atlas-once as a Codex MCP server.",
            }
        )
    steps.append(
        {
            "action": "verify",
            "command": "atlas config mcp doctor --json",
            "description": "Verify executable, profile, and Codex registration readiness.",
        }
    )
    return steps


def readiness_data(
    paths: AtlasPaths,
    *,
    client: str,
    profile: str | None,
    check_codex: bool,
) -> dict[str, Any]:
    profile_name = active_or_default_profile(paths, profile)
    executable = executable_status()
    profile_status = profile_readiness(paths, profile_name)
    if client != "codex":
        codex_status = {
            "available": False,
            "registered": True,
            "server_name": SERVER_NAME,
            "add_command": [],
            "get_command": [],
            "message": "Generic client readiness is managed by the caller.",
        }
    elif check_codex:
        codex_status = codex_registration_status(profile_name)
    else:
        codex_status = {
            "available": None,
            "registered": False,
            "server_name": SERVER_NAME,
            "add_command": codex_add_command(profile_name),
            "get_command": ["codex", "mcp", "get", SERVER_NAME],
            "message": "Codex registration was not checked. Run atlas config mcp doctor.",
        }
    ready = (
        bool(executable["available"])
        and bool(profile_status["ready"])
        and bool(codex_status["registered"])
    )
    next_steps = next_steps_for_status(
        selected_profile=profile_name,
        executable=executable,
        profile_status=profile_status,
        codex_status=codex_status,
    )
    return {
        "ready": ready,
        "server": {
            "command": SERVER_COMMAND,
            "command_path": executable["path"] if executable["available"] else None,
            "executable": executable,
        },
        "profile": profile_status,
        "codex": codex_status,
        "next_steps": next_steps,
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
    readiness = readiness_data(
        resolved_paths,
        client=client,
        profile=profile_name,
        check_codex=False,
    )
    return {
        "server_name": SERVER_NAME,
        "client": client,
        "profile": profile_name,
        "path": str(path),
        "codex_skill_path": str(codex_skill_install_path(resolved_paths)),
        "server": config["mcpServers"][SERVER_NAME],
        "codex": {
            "add_command": codex_add_command(profile_name),
            "get_command": ["codex", "mcp", "get", SERVER_NAME],
            "registered": readiness["codex"]["registered"],
            "available": readiness["codex"]["available"],
        },
        "ready": readiness["ready"],
        "next_steps": readiness["next_steps"],
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
    readiness = readiness_data(
        resolved_paths,
        client=client,
        profile=profile,
        check_codex=True,
    )
    return {
        "ready": readiness["ready"],
        "mcp_available": True,
        "server": {
            **readiness["server"],
            "config": show["server"],
        },
        "client": client,
        "profile": readiness["profile"],
        "codex": readiness["codex"],
        "next_steps": readiness["next_steps"],
        "paths": {
            "config_home": str(resolved_paths.config_home),
            "state_home": str(resolved_paths.state_home),
            "data_home": str(resolved_paths.data_home),
            "mcp_config": show["path"],
            "codex_skill": show["codex_skill_path"],
            "docs": _mcp_docs_path(),
        },
        "tools": {
            "total": len(definitions),
            "read": len(read_tools),
            "write": len(write_tools),
            "names": [definition.name for definition in definitions],
        },
    }
