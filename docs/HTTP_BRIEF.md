# Brief: add streamable HTTP transport to the personal-context MCP server

You are Claude Code on Andre's server with shell access. Self-contained; you have
no memory of the conversation that produced this. Report format at the bottom.

Fourth brief for this project. The first added a PostgreSQL task backend, the
second added shared-secret auth, both done. A third (`ROUTING_BRIEF.md`) was
written and then **superseded by this one** — if you find it, ignore it except that
its `pa/db.py` replacement is carried forward here in section 4.

---

## 1. The diagnosis this is based on

claude.ai will not connect. Its request arrives and is refused. Evidence, all
gathered before writing this:

- Server log during a connect attempt: `160.79.106.173:0 - "POST /sse HTTP/1.1" 404`.
  **A POST, not a GET.** In the SSE transport `/sse` is GET-only; POSTs belong on
  `/messages/`. A POST to the server URL is what a *streamable HTTP* client does.
- `curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8765/sse` → **200**,
  `text/event-stream`. So auth works and SSE works.
- The same request as a POST → **405**. So the header is fine; routing refuses the
  verb.
- claude.ai also warns, in its own dialog, that SSE is deprecated and recommends
  streamable HTTP.

Conclusion: a transport mismatch, not an auth or network problem. The server speaks
legacy SSE; the client speaks streamable HTTP.

## 2. Goal

Serve **streamable HTTP at `/mcp`**, keep the existing SSE endpoints working
untouched, and keep both behind the existing auth middleware.

Additive, deliberately. Do not remove SSE. If streamable HTTP has a problem in
practice, Andre can point the connector back at `/sse` without another deploy.
Retiring SSE is a later decision once `/mcp` has proven itself.

## 3. Hard rules

1. **Do not turn Funnel on or off.** Out of scope.
2. **Back up `server.py`** to `server.py.bak-YYYYMMDD`. `.bak-20260916` and
   `.bak-20260917` already exist — do not overwrite either.
3. **Do not change `AuthMiddleware`.** It already does the right thing (see §5).
   Do not change the 404-not-401 behaviour, the fail-closed token check, or `.env`.
4. **Do not break `/sse` or `/messages/`.** They must still work at the end.
5. **Never print the auth token**, in the report or anywhere.
6. **Do not touch** `/home/dremon/.personal-context/context.json` or the
   migrations. The only file under `futile-pa/` you may replace is `pa/db.py`, per §4.
7. **Stop and report** if the lifespan wiring cannot be done without restructuring
   `run_sse()` more than described here.

## 4. Carried-forward change: replace `pa/db.py`

A revised `pa/db.py` accompanies this brief (same directory). Copy it over
`/home/dremon/futile-pa/pa/db.py`. **This is the one file under `futile-pa/` you
are authorised to replace.**

`Db.__init__` used to build the psycopg connection pool eagerly, starting worker
threads. `server.py` calls `set_db(Db(...))` during startup *before* it validates
`PA_AUTH_TOKEN`, so a refused start could not exit — those threads kept the
interpreter alive. The pool is now created on first use. No API change; the
63-check suite still passes.

## 5. Implementation

This shape was built and run end to end before this brief was written. The
handshake succeeded with the SDK's own `streamablehttp_client`, unauthenticated
requests got 404 with no `WWW-Authenticate`, and a wrong token got 404. Follow it
closely; deviate only where the existing file's structure demands it.

**Imports to add:**

```python
import contextlib
from collections.abc import AsyncIterator
from starlette.routing import Mount
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
```

**Inside `run_sse()`** — consider renaming it `run_http()`, updating the one call
site in `main()`; your judgement, but say which you did.

After `app_mcp = build_server(store)` and the existing `SseServerTransport` setup,
add:

```python
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
```

Then add the route and the lifespan to the Starlette app:

```python
    starlette_app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Route("/messages/", endpoint=handle_messages, methods=["POST"]),
            Mount("/mcp", app=handle_mcp),
        ],
        lifespan=lifespan,
    )
```

**The trap, and the reason this brief exists rather than a one-line patch:**
`session_manager.run()` creates the task group all request handling depends on.
Without wiring `lifespan`, `handle_request` raises *"Task group is not
initialized"* on the first request — and it is not obvious from the error that the
cause is a missing lifespan. Do not skip it. `StreamableHTTPSessionManager.run()`
can only be entered once per instance, so create exactly one manager and do not
attempt to restart it.

**Leave `asgi_app = AuthMiddleware(starlette_app, ...)` and the `uvicorn.run(asgi_app, ...)`
call exactly as they are.** The middleware already passes non-HTTP scopes straight
through, which is what lets the `lifespan` scope reach Starlette and start the
session manager. That was verified. If you "fix" the middleware to handle lifespan
specially, you will break it.

**One small extra, while you are in here.** Change the `/messages/` route to a
`Mount`, matching the SDK's own documented example:

```python
    # delete handle_messages() entirely
    Mount("/messages/", app=sse_transport.handle_post_message),
```

`SseServerTransport.handle_post_message` already sends its own `202` and returns
`None`; the current wrapper's `return Response()` is then sent as a second
response, which uvicorn rejects — one logged `RuntimeError` per POST. `Mount`
never looks for a return value. **Keep `handle_sse`'s `return Response()` as it
is** — the asymmetry is deliberate and the SDK docstring explains it.

Note this has a known, cosmetic consequence: a non-POST to `/messages/` currently
returns `405`; under `Mount` it returns `400 Could not parse message`. Fine.
`AuthMiddleware` still 404s unauthenticated requests to both before either is
reached.

## 6. Verification

Restart with `systemctl --user restart personal-context-mcp.service`, then, with
the token from `.env`:

```bash
TOKEN=$(grep '^PA_AUTH_TOKEN=' /home/dremon/tools/personal-context-mcp/.env | cut -d= -f2-)

# new endpoint, unauthenticated -> 404, no WWW-Authenticate
curl -si -X POST http://127.0.0.1:8765/mcp -H 'Content-Type: application/json' --data '{}' | head -8

# new endpoint, authenticated initialize -> 200
curl -si -X POST http://127.0.0.1:8765/mcp \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}' | head -20

# SSE must still work
curl -si --max-time 3 -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8765/sse | head -5
```

Then the real test — a full client handshake against `/mcp`, which is what
claude.ai will do. Write this to a scratch file, run it, delete it:

```python
import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def main():
    hdrs = {"Authorization": "Bearer " + TOKEN}   # read from env, do not hardcode
    async with streamablehttp_client("http://127.0.0.1:8765/mcp", headers=hdrs) as (r, w, _):
        async with ClientSession(r, w) as s:
            info = await s.initialize()
            tools = await s.list_tools()
            print(info.serverInfo.name, len(tools.tools), "tools")
            print(sorted(t.name for t in tools.tools))

asyncio.run(main())
```

**Expect 29 tools** — 4 context tools plus 25 `pa_*`. Then call one of each kind
(`list_categories` and `pa_vocabulary`) and confirm real data comes back.

Also confirm:

```bash
# double-send gone
journalctl --user -u personal-context-mcp.service --since "5 minutes ago" \
  | grep -c "after response already completed"     # expect 0

# task suite still passes after the db.py swap
createdb pa_scratch
PA_DATABASE_URL='postgresql://localhost/pa_scratch' \
  /home/dremon/tools/personal-context-mcp/.venv-new/bin/python \
  /home/dremon/futile-pa/tests/test_smoke.py       # expect 63 passed, 0 failed
dropdb pa_scratch
```

And that fail-closed now exits promptly: run `server.py --sse` directly with
`PA_AUTH_TOKEN` absent from the environment (do not edit `.env`) — it should exit
in a second or two rather than lingering.

## 7. Rollback

```bash
cd /home/dremon/tools/personal-context-mcp
cp server.py.bak-YYYYMMDD server.py
systemctl --user restart personal-context-mcp.service
```

Funnel is currently on but every path requires the token, so a failed state is not
an exposed state.

## 8. Report back

Write to `/home/dremon/http-brief-report.md` and print it. No token in it.

```
## Outcome
One line.

## server.py diff
Unified diff. Confirm AuthMiddleware, the fail-closed check, and handle_sse's
return Response() are unchanged. Say whether you renamed run_sse and why.

## Verification
- POST /mcp unauthenticated: status, and whether WWW-Authenticate is absent
- POST /mcp authenticated initialize: status, and the protocol version negotiated
- GET /sse authenticated: status and content-type (must still work)
- streamablehttp_client handshake: tool count and the sorted tool names
- one context tool and one pa_ tool called: did real data come back
- double-send grep count: before and after
- test_smoke.py result
- fail-closed exit timing: before and after

## What the client negotiated
Anything you learned about which protocol version and features the SDK client and
server agreed on. Useful for guessing what claude.ai will do.

## Observed vs inferred
Two short lists. Keep them apart.

## For Andre to do
Exact steps to repoint the connector, including the URL to use and which
Authentication and Transport options to choose.

## Anything surprising
```

---

Andre is a senior technology executive with a CS background. Write plainly. If
something here does not match the machine, trust the machine and say so — two
earlier predictions about this server turned out wrong, and both corrections came
from reports like this one.
