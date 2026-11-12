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
import shutil
import sys
import tempfile
from collections.abc import AsyncIterator
from datetime import datetime, timezone
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
        """Write atomically: a crash mid-write must not truncate the store.

        Write a temp file in the same directory, fsync it, then os.replace —
        which is atomic on POSIX. Same directory matters, because replace
        across filesystems is not atomic. Rotate up to 3 backups first, since
        this file has no other recovery path.
        """
        self._rotate_backups()
        payload = json.dumps(data, indent=2, ensure_ascii=False)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".context-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    def _rotate_backups(self, keep: int = 3) -> None:
        if not self.path.exists():
            return
        for i in range(keep - 1, 0, -1):
            src = self.path.with_suffix(self.path.suffix + f".{i}")
            dst = self.path.with_suffix(self.path.suffix + f".{i + 1}")
            if src.exists():
                os.replace(src, dst)
        shutil.copy2(self.path, self.path.with_suffix(self.path.suffix + ".1"))

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


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
            for t in PA_TOOLS + SHOP_TOOLS
        ]

    # ---- Tool handlers -----------------------------------------------------

    @app.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        arguments = arguments or {}

        if name.startswith("pa_"):
            args = {k: v for k, v in arguments.items() if v is not None}
            run = shop_dispatch if name in SHOP_TOOL_NAMES else pa_dispatch
            return [TextContent(type="text", text=json.dumps(
                run(name, args), ensure_ascii=False, indent=2, default=str))]

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
            refused = agent_keys.refusal(category, key)
            if refused:
                return [TextContent(type="text", text=refused)]
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
            refused = agent_keys.refusal(category, key, whole_category=key is None)
            if refused:
                return [TextContent(type="text", text=refused)]
            deleted  = store.delete(category, key)
            if not deleted:
                target = f"`{category}.{key}`" if key else f"category `{category}`"
                return [TextContent(type="text", text=f"Nothing found at {target}.")]
            target = f"`{category}.{key}`" if key else f"category `{category}` (and all its keys)"
            return [TextContent(type="text", text=f"✓ Deleted {target}.")]

        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    return app


# ---------------------------------------------------------------------------
# Entry point — stdio or streamable HTTP
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

    PATH_PREFIX = "/t/"

    def __init__(self, app, token: str, allow_path: bool):
        self.app = app
        self.token = token
        self.allow_path = allow_path

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

        if not authorized and self.allow_path and scope["path"].startswith(self.PATH_PREFIX):
            candidate, _, remainder = scope["path"][len(self.PATH_PREFIX):].partition("/")
            if hmac.compare_digest(candidate, self.token):
                authorized = True
                new_path = "/" + remainder
                scope = dict(scope)
                scope["path"] = new_path
                scope["raw_path"] = new_path.encode("utf-8")

        if not authorized:
            response = Response(status_code=404)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def run_http(store: ContextStore, host: str, port: int) -> None:
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

    import asyncio
    app_mcp = build_server(store)

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

    # No routes: this Starlette app exists only to own the lifespan above,
    # which is what starts the StreamableHTTPSessionManager's task group.
    # /mcp is dispatched directly to handle_mcp below, so it never looks like
    # it's "used" — don't delete it as dead code, or every request fails with
    # "Task group is not initialized".
    starlette_app = Starlette(routes=[], lifespan=lifespan)

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

    log.info("Streamable HTTP listening on http://%s:%d/mcp", host, port)
    if allow_path:
        log.info("Path-prefix auth fallback is ENABLED (PA_AUTH_ALLOW_PATH=1)")
    uvicorn.run(asgi_app, host=host, port=port, log_level="info")

def main() -> None:
    parser = argparse.ArgumentParser(description="Personal Context MCP Server")
    parser.add_argument("--http", dest="http_mode", action="store_true",
                         help="Run streamable-HTTP transport instead of stdio")
    parser.add_argument("--sse", dest="http_mode", action="store_true",
                         help=argparse.SUPPRESS)  # deprecated alias for --http; kept for the systemd unit
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="HTTP port (default: 8765)")
    parser.add_argument("--store", default=str(DEFAULT_STORE), help="Path to context.json file")
    args = parser.parse_args()

    store = ContextStore(Path(args.store))
    log.info("Context store: %s", store.path)

    dsn = os.environ.get("PA_DATABASE_URL")
    if dsn:
        set_db(Db(url=dsn, actor="andre"))
    else:
        log.warning(
            "PA_DATABASE_URL is not set — the pa_* task tools will fail. "
            "The context tools will work normally."
        )

    if args.http_mode:
        run_http(store, args.host, args.port)
    else:
        run_stdio(store)


if __name__ == "__main__":
    main()
