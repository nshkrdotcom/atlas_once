from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from atlas_once.config import get_paths
from atlas_once.profiles import get_profile, profile_dict

from .tools import call_tool, tool_summaries


@dataclass(frozen=True)
class ResourceDefinition:
    uri: str
    name: str
    description: str
    mime_type: str = "text/markdown"
    read_only: bool = True


@dataclass(frozen=True)
class ResourceContent:
    uri: str
    text: str
    mime_type: str


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


RESOURCES: tuple[ResourceDefinition, ...] = (
    ResourceDefinition(
        "atlas://docs/install-and-profiles",
        "Install and profiles",
        "Atlas install, profile, and managed configuration guide.",
    ),
    ResourceDefinition(
        "atlas://docs/cli-reference",
        "CLI reference",
        "Atlas canonical CLI reference.",
    ),
    ResourceDefinition(
        "atlas://docs/agent-onboarding",
        "Agent onboarding",
        "Agent-oriented Atlas workflows and automation contract.",
    ),
    ResourceDefinition(
        "atlas://profiles/default",
        "Default profile",
        "Packaged generic default Atlas profile.",
        "application/json",
    ),
    ResourceDefinition(
        "atlas://profiles/nshkrdotcom",
        "nshkrdotcom profile",
        "Packaged installed default profile for this distribution.",
        "application/json",
    ),
    ResourceDefinition(
        "atlas://config/status",
        "Atlas status",
        "Generated current Atlas status.",
        "application/json",
    ),
    ResourceDefinition(
        "atlas://mcp/tools",
        "Atlas MCP tools",
        "Generated Atlas MCP tool registry.",
        "application/json",
    ),
)

DOC_RESOURCE_PATHS = {
    "atlas://docs/install-and-profiles": (
        "docs/install_and_profiles.md",
        "mcp_assets/docs/install_and_profiles.md",
    ),
    "atlas://docs/cli-reference": (
        "docs/cli_reference.md",
        "mcp_assets/docs/cli_reference.md",
    ),
    "atlas://docs/agent-onboarding": (
        "docs/agent_onboarding.md",
        "mcp_assets/docs/agent_onboarding.md",
    ),
}


def list_resources() -> tuple[ResourceDefinition, ...]:
    return RESOURCES


def _json_resource(uri: str, payload: dict[str, Any]) -> ResourceContent:
    return ResourceContent(
        uri,
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        "application/json",
    )


def _read_doc(checkout_path: str, package_path: str) -> str:
    path = _repo_root() / checkout_path
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return (files("atlas_once") / package_path).read_text(encoding="utf-8")


def read_resource(uri: str) -> ResourceContent:
    if uri in DOC_RESOURCE_PATHS:
        checkout_path, package_path = DOC_RESOURCE_PATHS[uri]
        return ResourceContent(uri, _read_doc(checkout_path, package_path), "text/markdown")
    if uri == "atlas://profiles/default":
        return _json_resource(uri, {"profile": profile_dict(get_profile("default"))})
    if uri == "atlas://profiles/nshkrdotcom":
        return _json_resource(uri, {"profile": profile_dict(get_profile("nshkrdotcom"))})
    if uri == "atlas://config/status":
        return _json_resource(uri, call_tool("atlas_status", {}))
    if uri == "atlas://mcp/tools":
        return _json_resource(
            uri,
            {
                "paths": {"config_home": str(get_paths().config_home)},
                "tools": tool_summaries(),
            },
        )
    raise KeyError(f"Unknown Atlas MCP resource: {uri}")
