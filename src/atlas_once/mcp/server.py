from __future__ import annotations

import argparse
import json
from typing import Any

import anyio
from mcp import types
from mcp.server import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server
from pydantic import AnyUrl

from .prompts import get_prompt, list_prompts
from .resources import list_resources, read_resource
from .tools import call_tool, iter_tool_definitions, tool_summaries


def create_server() -> Server[object]:
    server: Server[object] = Server(
        "atlas-once",
        version="0.1.0",
        instructions=(
            "Atlas Once exposes schema-backed tools. Read tools are safe first; "
            "write tools require confirm_write=true and use Atlas installer/config paths."
        ),
    )

    @server.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
    async def handle_list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=definition.name,
                description=definition.description,
                inputSchema=definition.input_schema,
                annotations=types.ToolAnnotations(
                    readOnlyHint=definition.access == "read",
                    destructiveHint=definition.access == "write",
                    idempotentHint=definition.name
                    in {
                        "atlas_status",
                        "atlas_config_profile_list",
                        "atlas_config_profile_show",
                        "atlas_config_ranked_show",
                        "atlas_context_ranked_groups",
                        "atlas_install",
                        "atlas_config_ranked_install",
                        "atlas_mcp_config_install",
                    },
                    openWorldHint=False,
                ),
            )
            for definition in iter_tool_definitions()
        ]

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def handle_call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return call_tool(name, arguments)

    @server.list_resources()  # type: ignore[no-untyped-call, untyped-decorator]
    async def handle_list_resources() -> list[types.Resource]:
        return [
            types.Resource(
                uri=AnyUrl(resource.uri),
                name=resource.name,
                description=resource.description,
                mimeType=resource.mime_type,
            )
            for resource in list_resources()
        ]

    @server.read_resource()  # type: ignore[no-untyped-call, untyped-decorator]
    async def handle_read_resource(uri: Any) -> list[ReadResourceContents]:
        content = read_resource(str(uri))
        return [ReadResourceContents(content=content.text, mime_type=content.mime_type)]

    @server.list_prompts()  # type: ignore[no-untyped-call, untyped-decorator]
    async def handle_list_prompts() -> list[types.Prompt]:
        return [
            types.Prompt(name=prompt.name, description=prompt.description)
            for prompt in list_prompts()
        ]

    @server.get_prompt()  # type: ignore[no-untyped-call, untyped-decorator]
    async def handle_get_prompt(
        name: str,
        arguments: dict[str, str] | None,
    ) -> types.GetPromptResult:
        del arguments
        prompt = get_prompt(name)
        return types.GetPromptResult(
            description=prompt.description,
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(type="text", text=prompt.text),
                )
            ],
        )

    return server


async def run_stdio() -> None:
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="atlas-mcp",
        description="Run the Atlas Once MCP server over stdio.",
    )
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="Print the Atlas MCP tool registry and exit.",
    )
    args = parser.parse_args(argv)
    if args.list_tools:
        print(json.dumps({"tools": tool_summaries()}, indent=2, sort_keys=True))
        return 0
    anyio.run(run_stdio)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
