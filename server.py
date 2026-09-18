"""
Personal Context MCP Server
Exposes structured personal context to any MCP-compatible Claude client.
Supports both stdio (Claude Code / desktop) and SSE/HTTP (claude.ai remote MCP).

Usage:
  stdio:   python server.py
  SSE:     python server.py --sse [--host 127.0.0.1] [--port 8765]
"""

import argparse
import contextlib
import hmac
import json
import logging
import os
import sys
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# MCP SDK imports  (pip install mcp)
# ---------------------------------------------------------------------------
from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import (
    TextContent,
    Tool,
)

# ---------------------------------------------------------------------------
# futile-pa (task/project backend, PostgreSQL-backed)
# ---------------------------------------------------------------------------
sys.path.insert(0, "/home/dremon/futile-pa")
from pa.tools import TOOLS as PA_TOOLS, dispatch as pa_dispatch
from pa.db import Db, set_db

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

# SSE transport (bundled with mcp >= 1.0)
try:
    from mcp.server.sse import SseServerTransport
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from starlette.applications import Starlette
    from starlette.routing import Route, Mount
    from starlette.requests import Request
    from starlette.responses import Response
    import uvicorn
    SSE_AVAILABLE = True
except ImportError:
    SSE_AVAILABLE = False

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
log = logging.getLogger("personal-context-mcp")

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
DEFAULT_STORE = Path.home() / ".personal-context" / "context.json"


class ContextStore:
    """
    JSON file-backed key/value store with optional category namespacing.

    Structure on disk:
    {
      "meta": { "updated_at": "...", "version": 1 },
      "contexts": {
        "work": { "role": "...", "company": "..." },
        "tech": { "stack": "...", "editor": "..." },
        ...
      }
    }
    """

    VERSION = 1

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({"meta": {"version": self.VERSION, "updated_at": self._now()}, "contexts": {}})

    # ---- public API --------------------------------------------------------

    def get(self, category: str | None = None) -> dict:
        data = self._read()
        ctx = data["contexts"]
        if category:
            return ctx.get(category, {})
        return ctx

    def set(self, category: str, key: str, value: Any) -> None:
        data = self._read()
        data["contexts"].setdefault(category, {})[key] = value
        data["meta"]["updated_at"] = self._now()
        self._write(data)

    def delete(self, category: str, key: str | None = None) -> bool:
        data = self._read()
        ctx = data["contexts"]
        if category not in ctx:
            return False
        if key is None:
            del ctx[category]
        else:
            if key not in ctx[category]:
                return False
            del ctx[category][key]
            if not ctx[category]:          # prune empty category
                del ctx[category]
        data["meta"]["updated_at"] = self._now()
        self._write(data)
        return True

    def list_categories(self) -> list[str]:
        return list(self._read()["contexts"].keys())

    def meta(self) -> dict:
        return self._read()["meta"]

    # ---- internals ---------------------------------------------------------

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.error("Could not read context store: %s", exc)
            return {"meta": {"version": self.VERSION, "updated_at": self._now()}, "contexts": {}}

    def _write(self, data: dict) -> None:
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _now() -> str:
        return datetime.utcnow().isoformat(timespec="seconds") + "Z"


# ---------------------------------------------------------------------------
# MCP Server definition
# ---------------------------------------------------------------------------

def build_server(store: ContextStore) -> Server:
    app = Server("personal-context")

    # ---- Tool schemas ------------------------------------------------------

    @app.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="get_context",
                description=(
                    "Retrieve personal context about the user. "
                    "Call with no arguments to get everything, or pass a category "
                    "(e.g. 'work', 'tech', 'projects') to get only that section."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "description": "Optional category name to fetch (e.g. 'work', 'tech').",
                        }
                    },
                    "required": [],
                },
            ),
            Tool(
                name="set_context",
                description=(
                    "Store or update a piece of personal context. "
                    "Provide a category (e.g. 'work'), a key (e.g. 'role'), "
                    "and the value. Creates the category if it doesn't exist."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "description": "Category bucket (e.g. 'work', 'tech', 'projects')."},
                        "key":      {"type": "string", "description": "Field name within the category."},
                        "value":    {"description": "The value to store (string, number, list, or object)."},
                    },
                    "required": ["category", "key", "value"],
                },
            ),
            Tool(
                name="list_categories",
                description="List all context categories that have been stored.",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            Tool(
                name="delete_context",
                description=(
                    "Delete a specific key from a category, or an entire category. "
                    "Omit 'key' to delete the whole category."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "description": "Category to target."},
                        "key":      {"type": "string", "description": "Specific key to remove (omit to remove entire category)."},
                    },
                    "required": ["category"],
                },
            ),
        ] + [
            Tool(name=t["name"], description=t["description"], inputSchema=t["inputSchema"])
            for t in PA_TOOLS
        ]

    # ---- Tool handlers -----------------------------------------------------

    @app.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:

        if name.startswith("pa_"):
            args = {k: v for k, v in (arguments or {}).items() if v is not None}
            return [TextContent(type="text", text=json.dumps(
                pa_dispatch(name, args), ensure_ascii=False, indent=2, default=str))]

        if name == "get_context":
            category = arguments.get("category")
            result = store.get(category)
            if not result:
                msg = (
                    f"No context found for category '{category}'."
                    if category
                    else "No personal context has been stored yet."
                )
                return [TextContent(type="text", text=msg)]
            label = f"Context — {category}" if category else "Personal context (all categories)"
            text = f"**{label}**\n\n```json\n{json.dumps(result, indent=2, ensure_ascii=False)}\n```"
            return [TextContent(type="text", text=text)]

        if name == "set_context":
            category = arguments["category"]
            key      = arguments["key"]
            value    = arguments["value"]
            store.set(category, key, value)
            return [TextContent(type="text", text=f"✓ Saved `{category}.{key}`.")]

        if name == "list_categories":
            cats = store.list_categories()
            meta = store.meta()
            if not cats:
                return [TextContent(type="text", text="No context categories stored yet.")]
            lines = "\n".join(f"• {c}" for c in sorted(cats))
            text = f"**Stored categories** (last updated {meta.get('updated_at', '?')}):\n{lines}"
            return [TextContent(type="text", text=text)]

        if name == "delete_context":
            category = arguments["category"]
            key      = arguments.get("key")
            deleted  = store.delete(category, key)
            if not deleted:
                target = f"`{category}.{key}`" if key else f"category `{category}`"
                return [TextContent(type="text", text=f"Nothing found at {target}.")]
            target = f"`{category}.{key}`" if key else f"category `{category}` (and all its keys)"
            return [TextContent(type="text", text=f"✓ Deleted {target}.")]

        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    return app


# ---------------------------------------------------------------------------
# Entry point — stdio or SSE
# ---------------------------------------------------------------------------

def run_stdio(store: ContextStore) -> None:
    import asyncio

    async def _main():
        app = build_server(store)
        opts = app.create_initialization_options()
        async with stdio_server() as (read, write):
            await app.run(read, write, opts)

    asyncio.run(_main())

class AuthMiddleware:
    """
    Pure ASGI middleware gating every HTTP path behind a shared secret.

    Unauthorised requests get a bare 404 — never 401 — so an anonymous
    scanner (or claude.ai's connector probing for OAuth) learns nothing
    about what this server is or that authentication exists at all.
    """

    def __init__(self, app, token: str, allow_path: bool):
        self.app = app
        self.token = token
        self.allow_path = allow_path
        self.path_prefix = f"/t/{token}/"

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        authorized = False

        headers = dict(scope.get("headers") or [])
        auth_header = headers.get(b"authorization", b"").decode("latin-1")
        if auth_header.startswith("Bearer "):
            supplied = auth_header[len("Bearer "):]
            if hmac.compare_digest(supplied, self.token):
                authorized = True

        if not authorized and self.allow_path and scope["path"].startswith(self.path_prefix):
            authorized = True
            new_path = "/" + scope["path"][len(self.path_prefix):]
            scope = dict(scope)
            scope["path"] = new_path
            scope["raw_path"] = new_path.encode("utf-8")

        if not authorized:
            response = Response(status_code=404)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def run_http(store: ContextStore, host: str, port: int) -> None:
    if not SSE_AVAILABLE:
        log.error("HTTP transports require: pip install mcp[cli] starlette uvicorn")
        sys.exit(1)

    token = os.environ.get("PA_AUTH_TOKEN", "")
    if len(token) < 32:
        log.error(
            "PA_AUTH_TOKEN is unset or shorter than 32 characters; refusing to "
            "start the HTTP transports unauthenticated."
        )
        sys.exit(1)
    allow_path = os.environ.get("PA_AUTH_ALLOW_PATH", "0") == "1"

    import asyncio
    app_mcp = build_server(store)
    sse_transport = SseServerTransport("/messages/")

    async def handle_sse(request: Request) -> Response:
        async with sse_transport.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await app_mcp.run(
                streams[0], streams[1], app_mcp.create_initialization_options()
            )
        return Response()

    session_manager = StreamableHTTPSessionManager(
        app=app_mcp,
        json_response=False,   # stream responses; the client negotiates
        stateless=False,       # keep session tracking
    )

    async def handle_mcp(scope, receive, send):
        await session_manager.handle_request(scope, receive, send)

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        async with session_manager.run():
            yield

    starlette_app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse_transport.handle_post_message),
        ],
        lifespan=lifespan,
    )

    async def dispatch(scope, receive, send):
        """Send /mcp (with or without a trailing slash) to the streamable HTTP
        handler; everything else to Starlette.

        handle_mcp is a raw ASGI app, so it needs no routing. Mounting it created
        the trailing-slash requirement that Starlette's Mount imposes and that
        claude.ai's connector does not satisfy — this states the intent directly
        instead.
        """
        if scope["type"] == "http" and scope["path"].rstrip("/") == "/mcp":
            await handle_mcp(scope, receive, send)
        else:
            await starlette_app(scope, receive, send)

    asgi_app = AuthMiddleware(dispatch, token=token, allow_path=allow_path)

    log.info("SSE server listening on http://%s:%d/sse", host, port)
    log.info("Streamable HTTP listening on http://%s:%d/mcp", host, port)
    if allow_path:
        log.info("Path-prefix auth fallback is ENABLED (PA_AUTH_ALLOW_PATH=1)")
    uvicorn.run(asgi_app, host=host, port=port, log_level="info")

def main() -> None:
    parser = argparse.ArgumentParser(description="Personal Context MCP Server")
    parser.add_argument("--sse", action="store_true", help="Run SSE/HTTP transport instead of stdio")
    parser.add_argument("--host", default="127.0.0.1", help="SSE bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="SSE port (default: 8765)")
    parser.add_argument("--store", default=str(DEFAULT_STORE), help="Path to context.json file")
    args = parser.parse_args()

    store = ContextStore(Path(args.store))
    log.info("Context store: %s", store.path)

    set_db(Db(url=os.environ["PA_DATABASE_URL"], actor="andre"))

    if args.sse:
        run_http(store, args.host, args.port)
    else:
        run_stdio(store)


if __name__ == "__main__":
    main()
