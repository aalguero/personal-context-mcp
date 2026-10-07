"""Adapter: register the pa tools on a FastMCP server.

If futileMCP is built on FastMCP (`from mcp.server.fastmcp import FastMCP`),
adding this system to it is one import and one call:

    from server_fastmcp import register
    register(mcp)            # mcp is your existing FastMCP instance

FastMCP normally derives a tool's schema from a Python signature, which does not
fit here: these schemas are hand-written so the descriptions can carry the
guidance a model needs. So each tool is registered with its explicit schema via
the low-level `add_tool` path where available, and falls back to a generated
wrapper otherwise.

If this file does not match your framework, ignore it — `pa.tools.dispatch` is
the whole integration surface, and wiring it by hand is a few lines.
"""

from __future__ import annotations

import json
from typing import Any

from pa.tools import TOOLS, dispatch


def register(mcp: Any) -> list[str]:
    """Attach every pa_* tool to a FastMCP instance. Returns the names added."""
    added: list[str] = []
    for spec in TOOLS:
        added.append(_register_one(mcp, spec))
    return added


def _register_one(mcp: Any, spec: dict) -> str:
    name = spec["name"]
    description = spec["description"]
    schema = spec["inputSchema"]

    async def handler(**kwargs: Any) -> str:
        # Strip unset optionals so defaults in repo functions apply rather than
        # being overridden with None.
        args = {k: v for k, v in kwargs.items() if v is not None}
        return json.dumps(dispatch(name, args), ensure_ascii=False, default=str)

    handler.__name__ = name
    handler.__doc__ = description

    # Preferred path: hand FastMCP the explicit schema.
    for attr in ("add_tool", "_add_tool"):
        fn = getattr(mcp, attr, None)
        if fn is None:
            continue
        try:
            fn(handler, name=name, description=description, input_schema=schema)
            return name
        except TypeError:
            try:
                fn(handler, name=name, description=description)
                return name
            except TypeError:
                pass

    # Fallback: decorator form.
    mcp.tool(name=name, description=description)(handler)
    return name


if __name__ == "__main__":
    # Standalone server, useful for testing the tools in isolation before
    # merging them into futileMCP.
    from mcp.server.fastmcp import FastMCP  # type: ignore

    app = FastMCP("futile-pa")
    print("registered:", ", ".join(register(app)))
    app.run()
