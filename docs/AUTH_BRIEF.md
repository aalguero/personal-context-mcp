# Brief: require a shared secret on the personal-context MCP server

You are Claude Code running on Andre's server with shell access. This brief is
your complete instruction set — you have no memory of the conversation that
produced it. A report format is at the bottom; Andre will paste that report back
into the conversation that wrote this.

This is the second brief for this project. The first one (`DEPLOY_BRIEF.md`) added
a PostgreSQL-backed task system, and is already done. Read this one as a separate,
smaller job.

---

## 1. Why this exists

The server was reachable at `https://futileassistant.cormorant-barb.ts.net` through
Tailscale Funnel — that is **public internet ingress**, not tailnet-private — with
**no authentication of any kind**. Verified on 2026-09-17: a request from
Anthropic's cloud infrastructure reached it with no credentials.

That endpoint exposes `get_context` (Andre's medications, family details,
investment positions, tax planning, income), `set_context` and `delete_context`
(which can rewrite or destroy that store), plus 25 `pa_*` tools over his task
database. Tailscale provisions a Let's Encrypt certificate for the hostname, so it
appears in public certificate transparency logs and is discoverable.

**Andre has already run `tailscale funnel off`.** The exposure is closed right now,
and the server is reachable only inside the tailnet. Your job is to make it safe to
turn Funnel back on.

## 2. Goal

No request reaches any tool handler without presenting a shared secret. Both
transports and every HTTP path must be covered. Local stdio use must keep working
without a token.

## 3. Hard rules

1. **Do not turn Funnel back on.** Not to test, not at the end. That is Andre's
   decision and he will make it after reviewing your report. `tailscale funnel`
   and `tailscale serve` configuration is out of scope entirely.
2. **Back up `server.py`** to `server.py.bak-YYYYMMDD` and verify the backup before
   the first edit. Note that `server.py.bak-20260916` already exists from the
   previous brief — do not overwrite it.
3. **Never log, echo, or write the token into your report.** Not truncated, not
   partially masked. Write it only into the `.env` file. If you generate it, print
   nothing but "generated, 32 bytes".
4. **Do not modify anything under `/home/dremon/futile-pa/`** except dropping in
   the `requirements-server.txt` file named in section 7. If something there looks
   wrong, report it rather than fixing it.
5. **Do not touch the JSON context store** at `/home/dremon/.personal-context/context.json`.
6. **Stop and report** rather than improvising if the SSE wiring does not admit a
   clean middleware insertion point, or if anything would change how the four
   existing context tools behave for an authorised caller.
7. Test against `http://127.0.0.1:8765` only. That is sufficient — the middleware
   runs identically regardless of how the request arrived.

## 4. Design

**A shared secret, checked in ASGI middleware.** Not per-tool checks, not a check
inside `call_tool`. Middleware, so coverage cannot be forgotten on a new route.

Generate the secret, and store it in
`/home/dremon/tools/personal-context-mcp/.env` as `PA_AUTH_TOKEN`:

```bash
openssl rand -hex 32
```

Four requirements, each for a reason:

**(a) Protect every path, not just `/sse`.** The SSE transport uses two endpoints:
`GET /sse` to open the stream and `POST /messages/` to send requests. Protecting
only the first leaves every tool call reachable. Middleware wrapping the whole
Starlette app is the way to be sure.

**(b) Reject with `404`, not `401`.** This is the important detail. A `401`,
especially with a `WWW-Authenticate` header, makes claude.ai's connector conclude
that OAuth is required and attempt dynamic client registration — which fails
against this server and produces the error "Couldn't register with sign-in
service". That is a known issue and it already cost a day. So: an unauthorised
request gets a bare `404 Not Found`, no `WWW-Authenticate` header, no body hinting
at authentication. An anonymous scanner should learn nothing.

**(c) Keep `/.well-known/*` returning 404.** It already does, correctly. Do not
let the middleware turn those into anything else, and do not add OAuth metadata.

**(d) Constant-time comparison.** Use `hmac.compare_digest`, not `==`. A plain
string comparison leaks length and prefix information through timing.

**Accept the token two ways**, because which one is usable depends on a claude.ai
beta feature Andre may not have:

- **Header (preferred):** `Authorization: Bearer <token>`.
- **Path prefix (fallback):** the whole app mounted under `/t/<token>/`, so the
  connector URL becomes `https://host/t/<token>/sse`. Enable only when
  `PA_AUTH_ALLOW_PATH=1` is set in `.env`; default off. It is weaker — secrets in
  URLs end up in logs and referrers — but it works with no special connector
  features. Implement both; leave the path form disabled by default.

**stdio must not require a token.** When the server runs without `--sse`, there is
no HTTP layer and the caller is already a local process. Gate the middleware on the
SSE path only.

**Fail closed on misconfiguration.** If `--sse` is requested and `PA_AUTH_TOKEN` is
unset or shorter than 32 characters, log an error and refuse to start. A server that
silently starts unauthenticated is how this situation arose.

## 5. Implementation notes

You have seen the file; match its conventions. It builds a Starlette app in its SSE
path (`run_sse`), using `SseServerTransport`, `Starlette`, `Route`, and `uvicorn`.
Insert the check as either a `BaseHTTPMiddleware` subclass or a pure ASGI
middleware wrapping the app before it is handed to uvicorn — your judgement, but
prefer pure ASGI if `BaseHTTPMiddleware` interferes with the streaming response, as
it sometimes does with SSE. **If it does interfere, that is worth knowing and
reporting** — a broken SSE stream under middleware is a real trap.

Read config with the `python-dotenv` loading already added in the previous change.

## 6. Verification

All against `127.0.0.1:8765`, with the server restarted under the user systemd unit
(`systemctl --user restart personal-context-mcp.service`).

```bash
# 1. No token -> 404, and no WWW-Authenticate header anywhere in the response
curl -si http://127.0.0.1:8765/sse | head -20
curl -si -X POST http://127.0.0.1:8765/messages/ | head -20

# 2. Wrong token -> 404
curl -si -H "Authorization: Bearer wrongvalue" http://127.0.0.1:8765/sse | head -5

# 3. Correct token -> the SSE stream opens (200, text/event-stream)
curl -si --max-time 3 -H "Authorization: Bearer $PA_AUTH_TOKEN" http://127.0.0.1:8765/sse | head -10

# 4. .well-known still 404
curl -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8765/.well-known/oauth-protected-resource

# 5. stdio still works with no token
cd /home/dremon/tools/personal-context-mcp
./.venv-new/bin/python server.py --help
```

Then confirm a real MCP handshake still completes over authenticated SSE and still
lists **29** tools (4 context + 25 `pa_*`). Use the MCP Inspector or a short client
script; delete any scratch script afterwards.

Also confirm the service is `active (running)` and that the log shows no new
tracebacks. Note: there is a **pre-existing, unrelated** `RuntimeError: Unexpected
ASGI message 'http.response.start' sent, after response already completed` in the
`/messages/` path. It predates this work. Report whether your middleware changed its
frequency or character, but do not try to fix it here — and take care that your
middleware is not mistaken for its cause.

## 7. Also do this

Copy `requirements-server.txt` (Andre has the file; ask him for it if it is not at
`/home/dremon/futile-pa/requirements-server.txt`) into
`/home/dremon/futile-pa/`. No install needed — the pinned versions already match
what is installed. It exists so a future `pip install mcp` cannot pull 2.x and
break the low-level `Server` decorators, which nearly happened during the last
deployment.

## 8. Rollback

```bash
cd /home/dremon/tools/personal-context-mcp
cp server.py.bak-YYYYMMDD server.py
systemctl --user restart personal-context-mcp.service
```

Funnel is already off, so a failed state is not an exposed state.

## 9. Report back

Write to `/home/dremon/auth-brief-report.md` and print it. **The token must not
appear anywhere in it.**

```
## Outcome
One line.

## What was changed
- unified diff of server.py
- where the middleware sits in the ASGI stack, and why there
- BaseHTTPMiddleware or pure ASGI, and whether SSE streaming was affected
- which .env keys were added (names only, no values)

## Verification
Each of the six checks in section 6, with the actual observed result:
- no token -> status, and confirmation that no WWW-Authenticate header is present
- wrong token -> status
- correct token -> status and content-type
- .well-known -> status
- stdio -> works
- MCP handshake over authenticated SSE -> tool count

## Fail-closed behaviour
What happens on `--sse` with PA_AUTH_TOKEN unset. Show the log line.

## Path-prefix fallback
Implemented? Disabled by default? The connector URL shape it would produce,
with the token shown as <token> rather than its value.

## Pre-existing SSE RuntimeError
Frequency and character before vs after. Confirm it is not caused by the middleware.

## Server state
Running, interpreter, start mechanism, any stderr warnings.

## For Andre to do
The exact steps he needs to take, in order, to get the connector working again:
turning Funnel back on, and what to enter in the claude.ai add-connector dialog
(URL, which Authentication option, which request header name and where the value
comes from — without the value itself).

## Anything surprising
Including anything about this server you think the other Claude has wrong.
```

---

## 10. On judgement

Andre is a senior technology executive with a CS background. Write plainly.

Two predictions from the other Claude have already been wrong about this machine —
a venv break blamed on a Python upgrade that turned out to be a typo'd path baked
into pip's console scripts, and a Funnel process assumed dead that was running
correctly. So: if something here does not match what you find, trust the machine
and say so in the report. That correction is worth more than a smooth execution.
