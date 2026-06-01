from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _extract_payload(result: Any) -> dict[str, Any]:
    structured = getattr(result, "structured_content", None) or getattr(
        result,
        "structuredContent",
        None,
    )
    if isinstance(structured, dict):
        return structured
    for content in getattr(result, "content", []):
        text = getattr(content, "text", None)
        if text:
            return json.loads(text)
    raise AssertionError(f"No JSON payload found in MCP result: {result!r}")


def test_mcp_stdio_server_lists_and_calls_tools(atlas_env: Path) -> None:
    async def exercise() -> None:
        env = {
            **os.environ,
            "HOME": str(atlas_env / "home"),
            "ATLAS_ONCE_HOME": str(atlas_env / "jb"),
            "ATLAS_ONCE_CONFIG_HOME": str(atlas_env / "config"),
            "ATLAS_ONCE_CODE_ROOT": str(atlas_env / "code"),
        }
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "atlas_once.mcp.server"],
            env=env,
        )
        async with (
            stdio_client(params) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()

            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            assert "atlas_status" in names
            assert "atlas_config_profile_list" in names
            assert "atlas_context_ranked_groups" in names

            status = _extract_payload(await session.call_tool("atlas_status", {}))
            assert status["ok"] is True

            profiles = _extract_payload(
                await session.call_tool("atlas_config_profile_list", {})
            )
            assert profiles["ok"] is True

            groups = _extract_payload(
                await session.call_tool("atlas_context_ranked_groups", {})
            )
            assert groups["ok"] is True

            written = _extract_payload(
                await session.call_tool(
                    "atlas_memory_add",
                    {"text": "stdio memory", "confirm_write": True},
                )
            )
            assert written["ok"] is True

    anyio.run(exercise)
