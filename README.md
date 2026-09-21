# personal-context-mcp

An MCP (Model Context Protocol) server that exposes Andre's personal context —
work, tech stack, projects, goals, and so on — plus a task/project management
backend (`futile-pa`), as tools any MCP-compatible Claude client can call.

29 tools in total:

- **4 context tools** — `get_context`, `set_context`, `list_categories`,
  `delete_context` — backed by a JSON file on disk.
- **25 `pa_*` tools** — tasks, projects, goals, people, recurrence, daily
  briefs — backed by Postgres, implemented in a separate project,
  [futile-pa](#futile-pa), and re-exposed here.

## Transports

The server has one transport: **streamable HTTP**, mounted at `/mcp`. This is
what claude.ai (and any modern MCP client) speaks.

It can also run over **stdio**, for local use from Claude Code or the Claude
desktop app on this machine — no HTTP, no auth token, just a subprocess
talking JSON-RPC over stdin/stdout.

The legacy SSE transport (`/sse`) was removed — it was a deprecated fallback
nobody exercised once claude.ai settled on streamable HTTP.

## Configuration

Copy `.env.example` to `.env` and fill in:

| Variable | Purpose |
|---|---|
| `PGUSER`, `PGPASSWORD` | Postgres credentials, if not embedded in the DSN |
| `PA_DATABASE_URL` | Postgres connection string for the `pa_*` task tools. If unset, the server still starts and the 4 context tools work normally — only the `pa_*` tools fail (with a clear error) when called. |
| `PA_AUTH_TOKEN` | Shared bearer secret for the HTTP transport. Must be at least 32 characters or the server refuses to start in HTTP mode. **Never commit or print this.** |
| `PA_AUTH_ALLOW_PATH` | Set to `1` to also accept the token as a URL path segment (`/t/<token>/mcp`) instead of only the `Authorization: Bearer` header. Off by default; only turn on if a client can't send custom headers. |
| `FUTILE_PA_PATH` | Filesystem path to the `futile-pa` checkout (see below). Defaults to `/home/dremon/futile-pa`. |

The context store itself lives outside this repo, at
`~/.personal-context/context.json` (path overridable with `--store`), with
up to 3 rotating backups (`context.json.1`, `.2`, `.3`) next to it. Writes
are atomic (temp file + `fsync` + `os.replace`) so a crash mid-write can't
truncate it.

## Running it

**Locally, over stdio** (e.g. from Claude Code):

```bash
python server.py
```

**As the HTTP service** (what claude.ai actually connects to):

```bash
python server.py --http [--host 127.0.0.1] [--port 8765]
```

`--sse` still works as a hidden, deprecated alias for `--http` — kept only
so the systemd unit below doesn't need editing.

In production this runs under systemd (user service) and is reached from
claude.ai over Tailscale Funnel, which proxies
`https://futileassistant.cormorant-barb.ts.net` to `http://127.0.0.1:8765`.
Tailscale/Funnel configuration is out of scope for this repo — don't change
it here.

### Restarting the systemd unit

```bash
systemctl --user restart personal-context-mcp.service
systemctl --user status personal-context-mcp.service --no-pager
```

The unit file lives at
`~/.config/systemd/user/personal-context-mcp.service` and is not part of
this repo.

## futile-pa

The 25 `pa_*` tools are implemented in a sibling project, `futile-pa`
(default location `/home/dremon/futile-pa`, overridable via
`FUTILE_PA_PATH`), which this server imports at startup
(`pa.tools`, `pa.db`). `futile-pa` is not itself versioned as part of this
repo — it's a separate checkout on the same machine. Making it an
installable package, a git submodule, or its own deployed repo is a real
decision with tradeoffs, not yet made.

## Development

```bash
pip install -r requirements.txt
```

`requirements.txt` pins both the MCP stack (see the comment there about why
it's pinned tightly) and futile-pa's runtime dependencies (`psycopg`,
`psycopg_pool`, `python-dateutil`), since `server.py` imports `pa.db` at
module scope.

The task-backend test suite (`tests/test_smoke.py`) lives in the `futile-pa`
checkout, not here — run it from there against a migrated scratch database.
