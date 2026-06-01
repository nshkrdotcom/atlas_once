from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import jsonschema  # type: ignore[import-untyped]

from .adapter import (
    AtlasMcpCall,
    AtlasMcpError,
    adapter_failure,
    call_atlas,
    unknown_tool_failure,
    validation_failure,
)

Access = Literal["read", "write"]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    access: Access
    input_schema: dict[str, Any]

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "access": self.access,
            "read_only": self.access == "read",
            "write": self.access == "write",
            "input_schema": self.input_schema,
        }


def _schema(
    properties: dict[str, Any] | None = None,
    *,
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }


def _write_schema(
    properties: dict[str, Any] | None = None,
    *,
    required: list[str] | None = None,
) -> dict[str, Any]:
    merged = {
        **(properties or {}),
        "confirm_write": {
            "type": "boolean",
            "const": True,
            "description": "Must be true to confirm this controlled Atlas write.",
        },
    }
    return _schema(merged, required=[*(required or []), "confirm_write"])


STRING = {"type": "string", "minLength": 1}
OPTIONAL_STRING = {"type": ["string", "null"]}
STRING_LIST = {"type": "array", "items": {"type": "string"}}
BOOL = {"type": "boolean"}
INT = {"type": "integer", "minimum": 0}
PROFILE = {"type": "string", "enum": ["default", "nshkrdotcom"]}
NOTE_KIND = {"type": "string", "enum": ["note", "decision", "project", "topic", "person"]}


TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        "atlas_status",
        "Read Atlas install, profile, storage, registry, inbox, and index status.",
        "read",
        _schema(),
    ),
    ToolDefinition(
        "atlas_help",
        "Read Atlas help text, optionally scoped to a help topic.",
        "read",
        _schema({"topic": OPTIONAL_STRING}),
    ),
    ToolDefinition(
        "atlas_config_profile_list",
        "List packaged Atlas profiles and their metadata.",
        "read",
        _schema(),
    ),
    ToolDefinition(
        "atlas_config_profile_show",
        "Show packaged Atlas profile settings for default or nshkrdotcom.",
        "read",
        _schema({"profile": PROFILE}, required=["profile"]),
    ),
    ToolDefinition(
        "atlas_config_ranked_show",
        "Show the active managed ranked-context configuration.",
        "read",
        _schema(),
    ),
    ToolDefinition(
        "atlas_context_ranked_groups",
        "List configured ranked context groups without rendering context.",
        "read",
        _schema(),
    ),
    ToolDefinition(
        "atlas_context_ranked_repos",
        "List repos resolved by a ranked context group.",
        "read",
        _schema({"scope": STRING}, required=["scope"]),
    ),
    ToolDefinition(
        "atlas_context_ranked",
        "Render a ranked context bundle through the canonical Atlas CLI path.",
        "read",
        _schema(
            {
                "scope": STRING,
                "portion": INT,
                "max_tokens": INT,
                "max_bytes": INT,
                "fresh": BOOL,
            },
            required=["scope"],
        ),
    ),
    ToolDefinition(
        "atlas_context_ranked_tree",
        "Read a ranked context tree view for a configured scope.",
        "read",
        _schema(
            {
                "scope": STRING,
                "max_depth": INT,
                "include": STRING_LIST,
                "include_all": BOOL,
            },
            required=["scope"],
        ),
    ),
    ToolDefinition(
        "atlas_context_ranked_cache",
        "Inspect ranked context cache and selection-plan metadata.",
        "read",
        _schema({"scope": STRING}, required=["scope"]),
    ),
    ToolDefinition(
        "atlas_git_status",
        "Read fleet git-health status for selected repos.",
        "read",
        _schema(
            {
                "selectors": STRING_LIST,
                "refresh": BOOL,
                "include_clean": BOOL,
                "include_errors": BOOL,
                "order_by": {
                    "type": "string",
                    "enum": ["dirty", "ahead", "branch", "name", "stale"],
                },
            }
        ),
    ),
    ToolDefinition(
        "atlas_registry_scan",
        "Scan configured project roots and return structured registry data.",
        "read",
        _schema({"changed_only": BOOL}),
    ),
    ToolDefinition(
        "atlas_memory_find",
        "Search Atlas notes and memory text.",
        "read",
        _schema({"query": STRING}, required=["query"]),
    ),
    ToolDefinition(
        "atlas_notes_related",
        "Read Atlas related-note candidates for a managed note path.",
        "read",
        _schema({"path": STRING, "limit": INT}, required=["path"]),
    ),
    ToolDefinition(
        "atlas_inbox_review",
        "Read open Atlas inbox entries, optionally for a date stamp.",
        "read",
        _schema({"date": OPTIONAL_STRING}),
    ),
    ToolDefinition(
        "atlas_install",
        "Install Atlas through the canonical installer path.",
        "write",
        _write_schema({"profile": PROFILE, "force": BOOL}),
    ),
    ToolDefinition(
        "atlas_config_profile_use",
        "Switch the active packaged profile through Atlas config commands.",
        "write",
        _write_schema({"profile": PROFILE}, required=["profile"]),
    ),
    ToolDefinition(
        "atlas_config_ranked_install",
        "Install packaged ranked-context config through Atlas config commands.",
        "write",
        _write_schema({"profile": PROFILE, "force": BOOL}),
    ),
    ToolDefinition(
        "atlas_config_ranked_group_add",
        "Add a managed ranked context group through Atlas config commands.",
        "write",
        _write_schema(
            {
                "group": STRING,
                "refs": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "variant": STRING,
                "force": BOOL,
            },
            required=["group", "refs"],
        ),
    ),
    ToolDefinition(
        "atlas_memory_add",
        "Capture a managed Atlas inbox memory entry.",
        "write",
        _write_schema(
            {
                "text": STRING,
                "tags": STRING_LIST,
                "project": OPTIONAL_STRING,
                "kind": NOTE_KIND,
            },
            required=["text"],
        ),
    ),
    ToolDefinition(
        "atlas_note_create",
        "Create a managed Atlas note under Atlas-owned note roots.",
        "write",
        _write_schema(
            {
                "title": STRING,
                "kind": NOTE_KIND,
                "body": {"type": "string"},
                "tags": STRING_LIST,
                "project": OPTIONAL_STRING,
            },
            required=["title"],
        ),
    ),
    ToolDefinition(
        "atlas_inbox_promote",
        "Promote Atlas inbox entries through the managed promotion path.",
        "write",
        _write_schema(
            {
                "entry_id": OPTIONAL_STRING,
                "date": OPTIONAL_STRING,
                "kind": NOTE_KIND,
                "title": OPTIONAL_STRING,
                "project": OPTIONAL_STRING,
            }
        ),
    ),
    ToolDefinition(
        "atlas_context_ranked_warm",
        "Warm a ranked context snapshot through Atlas-managed caches.",
        "write",
        _write_schema({"scope": STRING, "wait": BOOL}, required=["scope"]),
    ),
    ToolDefinition(
        "atlas_mcp_config_install",
        "Install Atlas-owned MCP client configuration snippets.",
        "write",
        _write_schema(
            {
                "client": {"type": "string", "enum": ["codex", "generic"]},
                "profile": PROFILE,
            }
        ),
    ),
)

TOOLS_BY_NAME = {definition.name: definition for definition in TOOL_DEFINITIONS}


def iter_tool_definitions() -> tuple[ToolDefinition, ...]:
    return TOOL_DEFINITIONS


def get_tool_definition(name: str) -> ToolDefinition:
    return TOOLS_BY_NAME[name]


def tool_summaries() -> list[dict[str, Any]]:
    return [definition.summary() for definition in TOOL_DEFINITIONS]


def call_tool(name: str, arguments: dict[str, object]) -> dict[str, Any]:
    definition = TOOLS_BY_NAME.get(name)
    if definition is None:
        return unknown_tool_failure(name)

    try:
        jsonschema.validate(instance=arguments, schema=definition.input_schema)
    except jsonschema.ValidationError as exc:
        return validation_failure(name, exc.message)

    try:
        return call_atlas(
            AtlasMcpCall(
                command=name,
                args=arguments,
                write=definition.access == "write",
            )
        )
    except AtlasMcpError as exc:
        return adapter_failure(name, exc)
