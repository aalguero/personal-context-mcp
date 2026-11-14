"""
Personal Context MCP Server
Exposes structured personal context to any MCP-compatible Claude client.
Supports stdio (Claude Code / desktop) and streamable HTTP (claude.ai remote MCP).

Usage:
  stdio:  python server.py
  HTTP:   python server.py --http [--host 127.0.0.1] [--port 8765]
"""

import argparse
import contextlib
import hmac
import json
import logging
import os
import re
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# MCP SDK imports  (pip install mcp)
# ---------------------------------------------------------------------------
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    TextContent,
    Tool,
)

# ---------------------------------------------------------------------------
# futile-pa (task/project backend, PostgreSQL-backed)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# futile-pa (task/project backend, PostgreSQL-backed)
# ---------------------------------------------------------------------------
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

# load_dotenv MUST come before this: FUTILE_PA_PATH is read from the
# environment, so reading it first silently ignores .env and always takes
# the default. That bug hid for weeks because the default happened to be
# correct, then broke the first restart after futile-pa moved into the repo.
FUTILE_PA_PATH = os.environ.get(
    "FUTILE_PA_PATH", str(Path(__file__).parent / "futile-pa")
)
if not (Path(FUTILE_PA_PATH) / "pa").is_dir():
    sys.exit(f"futile-pa not found at {FUTILE_PA_PATH} — check FUTILE_PA_PATH in .env")
sys.path.insert(0, FUTILE_PA_PATH)

from pa.tools import TOOLS as PA_TOOLS, dispatch as pa_dispatch
# Shopping tools are registered here and not in pa.tools, because the agent
# imports pa.tools.TOOLS wholesale and must not gain them until phase 2.
from pa.shop import SHOP_TOOLS, SHOP_TOOL_NAMES, dispatch as shop_dispatch
from pa.db import Db, set_db
from pa.profile import CHAT, ContextError, ContextService

# The check-in agent's code-owned task_agent keys (task 213). Loaded by file
# path, not imported as assistant.agent_keys: importing the assistant package
# runs its __init__, which calls set_db() and would replace this server's
# database handle. Fail closed, like the futile-pa check above: a server that
# silently lost this guard would let a chat session overwrite the record the
# 24-hour rule and three strikes run on, which is how prose got there before.
import importlib.util as _ilu
_AGENT_KEYS_PATH = Path(FUTILE_PA_PATH) / "assistant" / "agent_keys.py"
try:
    _spec = _ilu.spec_from_file_location("_agent_keys", _AGENT_KEYS_PATH)
    agent_keys = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(agent_keys)
    agent_keys.refusal  # noqa: B018 — fail here, not on first use
except Exception as _e:
    sys.exit(f"cannot load {_AGENT_KEYS_PATH} ({type(_e).__name__}) — the task_agent guard is required")

# Streamable HTTP transport (bundled with mcp >= 1.0)
try:
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from starlette.applications import Starlette
    from starlette.responses import Response
    import uvicorn
    HTTP_AVAILABLE = True
except ImportError:
    HTTP_AVAILABLE = False

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
log = logging.getLogger("personal-context-mcp")

# ---------------------------------------------------------------------------
# Storage: the context store is in Postgres since migration 009 (pa/profile.py,
# shared with the agent). Personal context is in schema `profile`, walled off
# from pa_sql; the agents' own state is in pa.agent_state. context.json is
# retired, and --store is accepted only so an old unit file still starts.
# ---------------------------------------------------------------------------
# Task 212 phase 4 (Andre, 2026-11-13): voice capture through Home Assistant
# gets its own token, PA_SHOP_TOKEN, which reaches ONLY these tools. HA sits
# behind a microphone anyone in the room can talk to; PA_AUTH_TOKEN reaches
# every tool, medical and financial tasks included. Least privilege is
# enforced structurally: the shop token is routed to a separate MCP server
# instance (its own session manager, so its session ids mean nothing to the
# full one) whose tool list and dispatcher only know these names.
SHOP_SCOPE_TOOLS = frozenset({"pa_shop_add", "pa_shop_list"})



def _ctx_json(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# MCP Server definition
# ---------------------------------------------------------------------------

def build_server(store: ContextService, allowed: frozenset[str] | None = None) -> Server:
    """The MCP server. `allowed` None is the full surface; a set restricts both
    the tool list and dispatch to those names (the shop-token server)."""
    app = Server("personal-context")

    # ---- Tool schemas ------------------------------------------------------

    @app.list_tools()
    async def list_tools() -> list[Tool]:
        tools = _all_tools()
        return tools if allowed is None else [t for t in tools if t.name in allowed]

    def _all_tools() -> list[Tool]:
        return [
            Tool(
                name="get_context",
                description=(
                    "Retrieve personal context about the user. Pass a category "
                    "(e.g. 'health', 'hobbies') to get that section. With no category, "
                    "returns every normal category plus an index of the sensitive ones "
                    "(health, money and the like), which must be asked for by name — "
                    "only fetch a sensitive category when the conversation needs it."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "description": "Optional category name to fetch (e.g. 'hobbies', 'health').",
                        }
                    },
                    "required": [],
                },
            ),
            Tool(
                name="set_context",
                description=(
                    "Store or update a piece of personal context. "
                    "Provide a category (e.g. 'hobbies'), a key (e.g. 'fly_fishing'), "
                    "and the value. Creates the category if it doesn't exist. The previous "
                    "value is kept in the history (context_history), so update in place "
                    "rather than adding dated keys like 'holdings_apr2026'."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "description": "Category: lowercase letters, digits, underscores (e.g. 'hobbies')."},
                        "key":      {"type": "string", "description": "Field name within the category."},
                        "value":    {"description": "The value to store (string, number, list, or object)."},
                    },
                    "required": ["category", "key", "value"],
                },
            ),
            Tool(
                name="list_categories",
                description="List every context category with its tier (normal / sensitive), description, key count and last update.",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            Tool(
                name="delete_context",
                description=(
                    "Delete a specific key from a category, or an entire category. "
                    "Omit 'key' to delete the whole category. Deleted values stay in the history."
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
            Tool(
                name="context_history",
                description=(
                    "Past values of a context category or one key, newest first: when, who "
                    "(chat, agent, code, import), and the old and new value. Use it to answer "
                    "'what did this say before' or to recover something overwritten or deleted."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "category": {"type": "string"},
                        "key":      {"type": "string", "description": "Optional: one key's history only."},
                        "limit":    {"type": "integer", "default": 20, "maximum": 200},
                    },
                    "required": ["category"],
                },
            ),
            Tool(
                name="context_category",
                description=(
                    "Describe a context category, or create one and set its description and "
                    "tier. Tier 'sensitive' keeps it out of an unscoped get_context and away "
                    "from the Signal check-in agent; it is scoping, not security. With only a "
                    "category, shows its current settings."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "category":    {"type": "string"},
                        "description": {"type": "string", "description": "What belongs in this category and what it is for."},
                        "tier":        {"type": "string", "enum": ["normal", "sensitive"]},
                    },
                    "required": ["category"],
                },
            ),
        ] + [
            Tool(name=t["name"], description=t["description"], inputSchema=t["inputSchema"])
            for t in PA_TOOLS + SHOP_TOOLS
        ]

    # ---- Tool handlers -----------------------------------------------------

    def _text(t: str) -> list[TextContent]:
        return [TextContent(type="text", text=t)]

    @app.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        arguments = arguments or {}

        # Same answer as a name that does not exist at all: a scoped client
        # learns nothing about what the full server holds.
        if allowed is not None and name not in allowed:
            return _text(f"Unknown tool: {name}")

        if name.startswith("pa_"):
            args = {k: v for k, v in arguments.items() if v is not None}
            run = shop_dispatch if name in SHOP_TOOL_NAMES else pa_dispatch
            return [TextContent(type="text", text=json.dumps(
                run(name, args), ensure_ascii=False, indent=2, default=str))]

        if name in CONTEXT_TOOL_NAMES:
            try:
                return _text(_context_tool(store, name, arguments))
            except ContextError as exc:      # worded for the caller, carries no value
                return _text(str(exc))
            except Exception as exc:         # class name only: hard rule 8
                log.error("context tool %s failed (%s)", name, type(exc).__name__)
                return _text(f"{name} failed ({type(exc).__name__}); nothing was changed.")

        return _text(f"Unknown tool: {name}")

    return app


CONTEXT_TOOL_NAMES = frozenset({"get_context", "set_context", "list_categories",
                                "delete_context", "context_history", "context_category"})


def _context_tool(store: ContextService, name: str, a: dict) -> str:
    if name == "get_context":
        category = a.get("category")
        if category:
            result = store.get(category)
            if not result:
                return f"No context found for category '{category}'."
            return f"**Context — {category}**\n\n```json\n{_ctx_json(result)}\n```"
        everything = store.get_all()
        if not everything["contexts"] and not everything["sensitive"]:
            return "No personal context has been stored yet."
        text = (f"**Personal context (normal categories)**\n\n"
                f"```json\n{_ctx_json(everything['contexts'])}\n```")
        if everything["sensitive"]:
            lines = "\n".join(f"• {s['category']}" + (f" — {s['description']}" if s["description"] else "")
                              for s in everything["sensitive"])
            text += ("\n\n**Sensitive categories, not included** (ask by name, only when the "
                     f"conversation needs one):\n{lines}")
        if everything["agent_state"]:
            text += ("\n\n**Agent state, not included** (ask by name): "
                     + ", ".join(everything["agent_state"]))
        return text

    if name == "set_context":
        category, key = a["category"], a["key"]
        store.set(category, key, a["value"])
        return f"✓ Saved `{category}.{key}`."

    if name == "list_categories":
        cats = store.list_categories()
        if not cats:
            return "No context categories stored yet."
        lines = []
        for c in cats:
            tier = "" if c.get("tier") == "normal" else f" [{c.get('tier')}]"
            desc = f" — {c['description']}" if c.get("description") else ""
            lines.append(f"• {c['category']}{tier} ({c.get('keys', 0)} keys, "
                         f"updated {c.get('updated_at', '?')}){desc}")
        return "**Stored categories**\n" + "\n".join(lines)

    if name == "delete_context":
        category, key = a["category"], a.get("key")
        if not store.delete(category, key):
            target = f"`{category}.{key}`" if key else f"category `{category}`"
            return f"Nothing found at {target}."
        target = f"`{category}.{key}`" if key else f"category `{category}` (and all its keys)"
        return f"✓ Deleted {target}. The old values are in context_history."

    if name == "context_history":
        rows = store.history(a["category"], a.get("key"), a.get("limit", 20))
        if not rows:
            return f"No history for `{a['category']}" + (f".{a['key']}`." if a.get("key") else "`.")
        return f"**History — {a['category']}" + (f".{a['key']}" if a.get("key") else "") + \
               f"** (newest first)\n\n```json\n{_ctx_json(rows)}\n```"

    if name == "context_category":
        r = store.describe(a["category"], a.get("description"), a.get("tier"))
        if not r:
            return f"No category `{a['category']}` (give a description or tier to create it)."
        return f"```json\n{_ctx_json(r)}\n```"

    raise ContextError(f"Unknown tool: {name}")


# ---------------------------------------------------------------------------
# Entry point — stdio or streamable HTTP
# ---------------------------------------------------------------------------

def run_stdio(store: ContextService) -> None:
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

    Each route is (token, app, allow_path): the request goes to the app whose
    token it presents, by Bearer header, or by a /t/<token>/ path prefix where
    that route allows it. Every token is compared on every request, so which
    route matched is not visible in timing.
    """

    PATH_PREFIX = "/t/"

    def __init__(self, routes: list[tuple[str, Any, bool]]):
        self.routes = routes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.routes[0][1](scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        auth_header = headers.get(b"authorization", b"").decode("latin-1")
        supplied = auth_header[len("Bearer "):] if auth_header.startswith("Bearer ") else ""
        candidate, remainder = None, ""
        if scope["path"].startswith(self.PATH_PREFIX):
            candidate, _, remainder = scope["path"][len(self.PATH_PREFIX):].partition("/")

        target, by_path = None, False
        for token, app, allow_path in self.routes:
            header_ok = hmac.compare_digest(supplied, token)
            path_ok = allow_path and candidate is not None and hmac.compare_digest(candidate, token)
            if target is None and (header_ok or path_ok):
                target, by_path = app, not header_ok

        if target is None:
            response = Response(status_code=404)
            await response(scope, receive, send)
            return

        if by_path:
            new_path = "/" + remainder
            scope = dict(scope)
            scope["path"] = new_path
            scope["raw_path"] = new_path.encode("utf-8")
        await target(scope, receive, send)


_TOKEN_PATH = re.compile(r"/t/[^/\s\"']+")


class RedactTokenPaths(logging.Filter):
    """Keep path tokens out of uvicorn's access log (hard rule 1).

    Uvicorn logs the path as the client sent it, before the middleware strips
    a /t/<token>/ prefix, so without this a path-authenticated request writes
    its token to the journal on every call."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(_TOKEN_PATH.sub("/t/<redacted>", a) if isinstance(a, str) else a
                                for a in record.args)
        if isinstance(record.msg, str):
            record.msg = _TOKEN_PATH.sub("/t/<redacted>", record.msg)
        return True


def run_http(store: ContextService, host: str, port: int) -> None:
    if not HTTP_AVAILABLE:
        log.error("The HTTP transport requires: pip install mcp[cli] starlette uvicorn")
        sys.exit(1)

    token = os.environ.get("PA_AUTH_TOKEN", "")
    if len(token) < 32:
        log.error(
            "PA_AUTH_TOKEN is unset or shorter than 32 characters; refusing to "
            "start the HTTP transport unauthenticated."
        )
        sys.exit(1)
    allow_path = os.environ.get("PA_AUTH_ALLOW_PATH", "0") == "1"

    # Optional second token, scoped to SHOP_SCOPE_TOOLS (task 212 phase 4).
    # Absent means the route does not exist. Present but weak, or equal to the
    # full token, is a misconfiguration and refuses to start, like the check
    # above: a scoped token that silently were the full one protects nothing.
    shop_token = os.environ.get("PA_SHOP_TOKEN", "")
    if shop_token and (len(shop_token) < 32 or hmac.compare_digest(shop_token, token)):
        log.error("PA_SHOP_TOKEN is shorter than 32 characters or equals PA_AUTH_TOKEN; refusing to start.")
        sys.exit(1)
    shop_allow_path = os.environ.get("PA_SHOP_ALLOW_PATH", "0") == "1"

    managers = [StreamableHTTPSessionManager(
        app=build_server(store),
        json_response=False,   # stream responses; the client negotiates
        stateless=False,       # keep session tracking
    )]
    if shop_token:
        managers.append(StreamableHTTPSessionManager(
            app=build_server(store, allowed=SHOP_SCOPE_TOOLS),
            json_response=False,
            stateless=False,
        ))

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        async with contextlib.AsyncExitStack() as stack:
            for m in managers:
                await stack.enter_async_context(m.run())
            yield

    # No routes: this Starlette app exists only to own the lifespan above,
    # which is what starts the StreamableHTTPSessionManager's task group.
    # /mcp is dispatched directly to handle_mcp below, so it never looks like
    # it's "used" — don't delete it as dead code, or every request fails with
    # "Task group is not initialized".
    starlette_app = Starlette(routes=[], lifespan=lifespan)

    def dispatcher(manager):
        async def dispatch(scope, receive, send):
            """Send /mcp (with or without a trailing slash) to this route's
            streamable HTTP handler; everything else to Starlette.

            The handler is a raw ASGI app, so it needs no routing. Mounting it
            created the trailing-slash requirement that Starlette's Mount imposes
            and that claude.ai's connector does not satisfy — this states the
            intent directly instead.
            """
            if scope["type"] == "http" and scope["path"].rstrip("/") == "/mcp":
                await manager.handle_request(scope, receive, send)
            else:
                await starlette_app(scope, receive, send)
        return dispatch

    routes = [(token, dispatcher(managers[0]), allow_path)]
    if shop_token:
        routes.append((shop_token, dispatcher(managers[1]), shop_allow_path))
    asgi_app = AuthMiddleware(routes)

    logging.getLogger("uvicorn.access").addFilter(RedactTokenPaths())
    log.info("Streamable HTTP listening on http://%s:%d/mcp", host, port)
    if allow_path:
        log.info("Path-prefix auth fallback is ENABLED (PA_AUTH_ALLOW_PATH=1)")
    if shop_token:
        log.info("Shop-scoped token route is ENABLED (%s)%s", ", ".join(sorted(SHOP_SCOPE_TOOLS)),
                 ", path-prefix allowed" if shop_allow_path else "")
    uvicorn.run(asgi_app, host=host, port=port, log_level="info")

def main() -> None:
    parser = argparse.ArgumentParser(description="Personal Context MCP Server")
    parser.add_argument("--http", dest="http_mode", action="store_true",
                         help="Run streamable-HTTP transport instead of stdio")
    parser.add_argument("--sse", dest="http_mode", action="store_true",
                         help=argparse.SUPPRESS)  # deprecated alias for --http; kept for the systemd unit
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="HTTP port (default: 8765)")
    parser.add_argument("--store", default=None, help=argparse.SUPPRESS)  # retired with context.json (009)
    args = parser.parse_args()

    if args.store:
        log.warning("--store is ignored: the context store is in Postgres since migration 009")

    dsn = os.environ.get("PA_DATABASE_URL")
    if not dsn:
        # Fail closed: since 009 the context tools need the database too, and a
        # server that started anyway would answer every get_context with an error.
        sys.exit("PA_DATABASE_URL is not set — the context and pa_* tools both need it")
    set_db(Db(url=dsn, actor="andre"))
    store = ContextService(CHAT, agent_keys)
    log.info("Context store: Postgres (schema profile, pa.agent_state)")

    if args.http_mode:
        run_http(store, args.host, args.port)
    else:
        run_stdio(store)


if __name__ == "__main__":
    main()
