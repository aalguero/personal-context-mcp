"""Adapter: the low-level MCP server API (`mcp.server.Server`) over stdio.

Use this if futileMCP is built on the low-level SDK — the shape where you
implement `list_tools()` and `call_tool()` yourself. Merging is a matter of
extending your two existing handlers:

    from pa.tools import TOOLS as PA_TOOLS, dispatch as pa_dispatch

    @server.list_tools()
    async def list_tools():
        return MY_EXISTING_TOOLS + [
            types.Tool(name=t["name"], description=t["description"],
                       inputSchema=t["inputSchema"]) for t in PA_TOOLS
        ]

    @server.call_tool()
    async def call_tool(name, arguments):
        if name.startswith("pa_"):
            result = pa_dispatch(name, arguments)
            return [types.TextContent(type="text",
                                      text=json.dumps(result, default=str))]
        ...your existing handling...

Run this file directly to serve the pa tools on their own, which is the quickest
way to exercise them from a client before touching the existing server.
"""

from __future__ import annotations

import asyncio
import json

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from pa.tools import TOOLS, dispatch

server = Server("futile-pa")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(name=t["name"], description=t["description"], inputSchema=t["inputSchema"])
        for t in TOOLS
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
    args = {k: v for k, v in (arguments or {}).items() if v is not None}
    result = dispatch(name, args)
    return [types.TextContent(
        type="text",
        text=json.dumps(result, ensure_ascii=False, indent=2, default=str),
    )]


async def main() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
