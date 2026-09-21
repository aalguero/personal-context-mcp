# Patch: serve `/mcp` with or without a trailing slash

Claude Code on Andre's server. Small job — one function, one changed line.
Report format at the end.

Fifth brief for this project. The fourth (`HTTP_BRIEF.md`) added streamable HTTP at
`/mcp` and is working. This fixes the one thing it left open.

---

## 1. The problem, with evidence

From the live server log during a claude.ai connect attempt:

```
160.79.106.169:0 - "POST /mcp HTTP/1.1" 307 Temporary Redirect
```

`Mount("/mcp", app=handle_mcp)` compiles to `/mcp/{path:path}` and only matches
**with** the trailing slash. Starlette's router answers the bare path with a `307`
to `/mcp/`. The connector URL was configured *with* a trailing slash and the
request still arrived without one, so claude.ai normalises it away — and it did not
follow the redirect. There was no second request.

Two things follow, and both matter:

- **Auth is working.** A `307` is only reachable after `AuthMiddleware` passes; an
  unauthenticated request gets `404`. Do not go looking for an auth problem.
- **The redirect has to be removed, not relied upon.**

## 2. The fix

`handle_mcp` is already a raw ASGI app. It does not need Starlette's routing at
all, and the mismatch only exists because it was put behind a `Mount`. So take it
out of the router and dispatch it explicitly.

Replace the `Mount("/mcp", app=handle_mcp)` entry in the `Starlette(routes=[...])`
list — delete that line — and add a dispatch function after `starlette_app` is
built:

```python
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
```

Then change:

```python
    asgi_app = AuthMiddleware(starlette_app, token=token, allow_path=allow_path)
```

to:

```python
    asgi_app = AuthMiddleware(dispatch, token=token, allow_path=allow_path)
```

**Order is deliberate: auth stays outermost.** Dispatch happens only after the
token check, so an unauthenticated request is refused before any path handling.
Do not reverse them.

`/sse` and `/messages/` stay exactly as they are, still routed by Starlette. The
`lifespan` stays on `starlette_app` — it still runs, because uvicorn hands the
lifespan scope to `AuthMiddleware`, which passes non-HTTP scopes through, and
`dispatch` forwards anything that is not `/mcp` to Starlette. Verified.

## 3. Verified before writing this

Run end to end on this exact shape:

| check | result |
|---|---|
| `POST /mcp` authenticated | 200, `mcp-session-id` present |
| `POST /mcp/` authenticated | 200, `mcp-session-id` present |
| `POST /mcp` unauthenticated | 404 |
| `GET /.well-known/oauth-protected-resource` authenticated | **404** — not swallowed by the MCP handler |
| `GET /sse` | 200 — Starlette route still reached |
| SDK `streamablehttp_client` on `/mcp` and on `/mcp/` | both complete a handshake |

That fourth row is the one worth keeping an eye on. `/.well-known/` must keep
returning 404, because a 401 or anything OAuth-shaped there makes claude.ai attempt
dynamic client registration, which fails against this server. That is a failure
this project already spent a day on.

## 4. Hard rules

1. **Do not modify `AuthMiddleware`.** Only what it wraps.
2. Back up `server.py` first — `.bak-20260916`, `.bak-20260917` and
   `.bak-20260917-1732` exist; use a fresh suffix.
3. Leave `/sse`, `/messages/`, the fail-closed check, `.env`, and everything under
   `futile-pa/` untouched.
4. Do not touch Tailscale or Funnel.
5. Never print the auth token.

## 5. Verification

Restart, then:

```bash
TOKEN=$(grep '^PA_AUTH_TOKEN=' /home/dremon/tools/personal-context-mcp/.env | cut -d= -f2-)
INIT='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
AUTH="Authorization: Bearer $TOKEN"; CT='Content-Type: application/json'
AC='Accept: application/json, text/event-stream'

curl -si -X POST http://127.0.0.1:8765/mcp  -H "$AUTH" -H "$CT" -H "$AC" --data "$INIT" | head -12
curl -si -X POST http://127.0.0.1:8765/mcp/ -H "$AUTH" -H "$CT" -H "$AC" --data "$INIT" | head -6
curl -si -X POST http://127.0.0.1:8765/mcp  -H "$CT" -H "$AC" --data "$INIT" | head -8
curl -o /dev/null -w "well-known: %{http_code}\n" -H "$AUTH" \
  http://127.0.0.1:8765/.well-known/oauth-protected-resource
curl -si --max-time 3 -H "$AUTH" http://127.0.0.1:8765/sse | head -5
```

Then an SDK handshake against the **bare** path — expect 29 tools. Scratch script,
delete it after.

If the bare path is anything other than 200 with a session id, or if
`/.well-known/` returns anything other than 404, stop and report rather than trying
another approach.

## 6. Rollback

```bash
cp server.py.bak-<suffix> server.py
systemctl --user restart personal-context-mcp.service
```

## 7. Report back

Write to `/home/dremon/slash-patch-report.md` and print it. No token in it.

```
## Outcome
One line.

## Diff
Unified diff. Confirm AuthMiddleware unchanged, dispatch inside it, lifespan intact.

## Verification
Each of the five curls with actual status codes, plus the SDK handshake tool count.
Call out the /.well-known/ result explicitly.

## Anything surprising
Including anything suggesting this is not the whole fix.
```

---

Write plainly. If the machine disagrees with this brief, trust the machine.
