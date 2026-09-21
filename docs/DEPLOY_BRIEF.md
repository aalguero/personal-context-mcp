# Deployment brief: add the `futile-pa` task backend to the personal-context MCP server

You are Claude Code running on Andre's server, with shell access. This brief is
your complete instruction set — you have no memory of the conversation that
produced it, and you should not assume anything beyond what is written here plus
what you discover on the machine.

A summary of the work is at the bottom under **Report back**. Andre will paste
that report into the Claude conversation that wrote this brief, so write it for
another Claude to read, not for a human to admire.

---

## 1. Goal

Two things must be true when you are finished:

1. The `futile-pa` package (a PostgreSQL-backed task/project system) is installed,
   its schema is created in Andre's PostgreSQL database, and its 63-check
   integration test passes on this machine.
2. The existing MCP server at `/home/dremon/tools/personal-context-mcp/server.py`
   advertises 25 new `pa_*` tools **in addition to** its existing context tools,
   and still works exactly as before for those existing tools.

## 2. What is known about this machine

Established already, but verify rather than trust:

- The MCP server lives at `/home/dremon/tools/personal-context-mcp/`, is a
  hand-written Python file using the **low-level** MCP SDK (`from mcp.server
  import Server`), and supports both stdio and SSE transports. It exposes four
  tools: `get_context`, `set_context`, `list_categories`, `delete_context`,
  backed by a JSON file on disk.
- **There is a broken virtualenv at
  `/home/dremon/tools/personal-context-mcp/.venv`.** Running its `pip` gives
  `cannot execute: required file not found`, which is the signature of a shebang
  pointing at a Python interpreter that no longer exists — typically after a
  system Python upgrade.
- PostgreSQL 18 is installed and running.
- The OS is Debian, and the system Python is PEP 668 "externally managed", so
  plain `pip install` into it is refused.
- The `futile-pa` code should already be on the machine (probably `~/futile-pa`,
  extracted from `futile-pa.tar.gz`). Find it. If it is missing, stop and say so.

## 3. Hard rules

Read these before starting. They exist because this server is in daily use.

1. **Do not delete or overwrite the existing `.venv` until a working replacement
   is proven.** Rename it if you must move it aside. Andre's currently-running
   server process may depend on it.
2. **Back up `server.py` before editing it**, to `server.py.bak-YYYYMMDD`. Verify
   the backup exists before making the first edit.
3. **Do not touch the JSON context store file** that the existing tools read and
   write. Not to migrate it, not to reformat it, not to back it up into a place
   that changes what the server reads. Locate it, note its path in your report,
   leave it alone.
4. **Do not run the migration against Andre's real database until the test suite
   has passed against a scratch database.** This ordering is the entire point of
   the test suite.
5. **Do not modify anything under `futile-pa/`** — not the migrations, not the
   tests, not the Python package. If a test fails, that is a real finding and the
   most valuable thing you can report. Report it verbatim; do not adjust the test
   or the schema to make it pass. Someone else will fix the cause.
6. **Stop and report** rather than improvising if you hit: a test failure, an
   ambiguous choice of database, a system package that needs `sudo` beyond
   installing `python3-venv`, or anything that would change how the existing four
   tools behave.
7. Do not commit anything to git, and do not create a repository. There is no repo
   here and that is intentional for now.

## 4. Phase 0 — Diagnose and record

Gather this before changing anything. It goes in your report.

```bash
V=/home/dremon/tools/personal-context-mcp/.venv
head -1 $V/bin/pip
cat $V/pyvenv.cfg
ls -l $V/bin/python*
python3 --version; which python3
ls -la /home/dremon/tools/personal-context-mcp/
ps aux | grep -i "server.py" | grep -v grep
systemctl list-units --all 2>/dev/null | grep -i -E "context|mcp"
systemctl --user list-units --all 2>/dev/null | grep -i -E "context|mcp"
psql -l
```

Answer these questions explicitly in the report:

- Does the interpreter named in `pyvenv.cfg` still exist on disk? What version
  was the venv built from, and what is the system Python now?
- Is the server currently running? Under what interpreter (check
  `/proc/<pid>/exe`)? Started by systemd, by hand, or by something else?
- Which database should `futile-pa` use? `psql -l` plus any DSN or config in the
  project directory should make it obvious. **If more than one candidate exists,
  stop and ask Andre.** Do not guess.

## 5. Phase 1 — Repair the Python environment

The chosen approach is **build a fresh venv alongside the broken one**, leaving
the existing one untouched as a fallback.

1. Determine the existing server's dependencies. Look for `requirements.txt`,
   `pyproject.toml`, or a lock file in the project directory. If none exists,
   derive them from the imports in `server.py` — expect at least `mcp`,
   `starlette`, `uvicorn`. If the old venv's `lib/python*/site-packages` is still
   readable, list it to catch anything the imports miss, and record the versions.
2. Create the new environment:
   ```bash
   cd /home/dremon/tools/personal-context-mcp
   python3 -m venv .venv-new
   ./.venv-new/bin/pip install --upgrade pip
   ```
   If `python3 -m venv` fails because `ensurepip` is missing, install
   `python3-venv` via apt — that is the one `sudo` you are authorised to use
   without asking.
3. Install the server's own dependencies into `.venv-new`, then the `futile-pa`
   requirements:
   ```bash
   ./.venv-new/bin/pip install mcp starlette uvicorn   # plus anything else found
   ./.venv-new/bin/pip install -r /path/to/futile-pa/requirements.txt
   ```
4. Prove it works before going further:
   ```bash
   ./.venv-new/bin/python -c "import mcp, psycopg, dateutil; print('ok')"
   ./.venv-new/bin/python -c "import sys; sys.path.insert(0,'/path/to/futile-pa'); import pa; print('pa', pa.__version__)"
   ```

Record the new venv's Python version and the installed versions of `mcp`,
`psycopg` and `python-dateutil`.

## 6. Phase 2 — Test against a scratch database

This is the step that matters most. The schema was developed and verified against
PostgreSQL **16**; this machine runs **18**. Nobody has yet run these 63 checks on
18, and that is exactly what you are here to establish.

```bash
cd /path/to/futile-pa
createdb pa_scratch
PA_DATABASE_URL='postgresql://localhost/pa_scratch' /home/dremon/tools/personal-context-mcp/.venv-new/bin/python scripts/migrate.py
PA_DATABASE_URL='postgresql://localhost/pa_scratch' /home/dremon/tools/personal-context-mcp/.venv-new/bin/python tests/test_smoke.py
```

Expected final line: `63 passed, 0 failed`.

- **If it passes**, record the fact and continue to Phase 3.
- **If anything fails**, stop. Copy the failing lines verbatim into your report,
  along with the full traceback if there is one and the PostgreSQL version from
  `select version()`. Do not attempt a fix. Then drop `pa_scratch` and leave the
  real database untouched.

Drop the scratch database once you are done with it: `dropdb pa_scratch`.

## 7. Phase 3 — Create the schema in the real database

Only after Phase 2 passed.

```bash
cd /path/to/futile-pa
PA_DATABASE_URL='postgresql://localhost/THE_REAL_DB' /home/dremon/tools/personal-context-mcp/.venv-new/bin/python scripts/migrate.py
PA_DATABASE_URL='postgresql://localhost/THE_REAL_DB' /home/dremon/tools/personal-context-mcp/.venv-new/bin/python scripts/migrate.py --status
```

Everything is created inside a dedicated schema named `pa`; nothing is created in
`public` and nothing existing is modified. Both migrations are transactional and
idempotent.

Sanity check afterwards and put the numbers in your report:

```sql
select count(*) from pa.project;    -- expect 24
select count(*) from pa.context;    -- expect 18
select count(*) from pa.situation;  -- expect 9
select count(*) from pa.person;     -- expect 6
select count(*) from pa.goal;       -- expect 9
```

## 8. Phase 4 — Patch `server.py`

Back it up first. Then make exactly these three additions. Match the file's
existing style, and change nothing else.

**4a. Imports**, alongside the existing ones:

```python
import sys
sys.path.insert(0, "/path/to/futile-pa")          # the real path you found
from pa.tools import TOOLS as PA_TOOLS, dispatch as pa_dispatch
from pa.db import Db, set_db
```

and, wherever the server does its startup configuration, once:

```python
set_db(Db(url="postgresql://localhost/THE_REAL_DB", actor="andre"))
```

If the file already has a config-loading section or reads environment variables,
prefer putting the DSN there and reading it, rather than hardcoding it inline.
`actor="andre"` is deliberate: the schema records who made each change, and this
marks writes that come through the server as his rather than as an agent's.

**4b. The `@server.list_tools()` handler.** It currently returns a list of `Tool`
objects. Append the new ones without disturbing the existing entries:

```python
    return [
        # ... every existing Tool(...) entry, unchanged ...
    ] + [
        Tool(name=t["name"], description=t["description"], inputSchema=t["inputSchema"])
        for t in PA_TOOLS
    ]
```

**4c. The `@server.call_tool()` handler.** Add one branch as the first thing it
does, leaving the existing dispatch logic untouched below it:

```python
    if name.startswith("pa_"):
        args = {k: v for k, v in (arguments or {}).items() if v is not None}
        return [TextContent(type="text", text=json.dumps(
            pa_dispatch(name, args), ensure_ascii=False, indent=2, default=str))]
```

Two things not to get wrong:

- The `None` filter is required. MCP clients send unset optional parameters as
  JSON null, and passing those through would override the Python defaults with
  `None` and break most calls.
- `json` must be imported in that module. It already is — confirm rather than
  assume.

## 9. Phase 5 — Verify, then restart

**First, verify without touching the running server.** There is a standalone
entry point that serves only the new tools:

```bash
cd /path/to/futile-pa
PA_DATABASE_URL='postgresql://localhost/THE_REAL_DB' timeout 5 \
  /home/dremon/tools/personal-context-mcp/.venv-new/bin/python server_lowlevel.py
```

It waits on stdin, so a clean 5-second timeout with no traceback means the module
and its tool registration are sound.

**Then check the patched file compiles and its handlers register.** Write a
throwaway script in `/tmp` that imports the patched `server.py`, invokes its
`list_tools` handler, and prints the tool count — expect 29 (4 existing + 25 new).
Do not leave that script behind.

**Then restart the server** the same way it is currently started — systemd unit if
there is one, otherwise however Andre runs it — using the new interpreter
`.venv-new/bin/python`. If a systemd unit or shell wrapper names the old
`.venv/bin/python`, update that reference and record exactly which file and line
you changed.

Confirm it came back up: check the process, and check the log output (the server
logs to stderr) for tracebacks.

## 10. Rollback

If the server will not start after patching:

```bash
cd /home/dremon/tools/personal-context-mcp
cp server.py.bak-YYYYMMDD server.py
# revert any systemd/wrapper interpreter change you made
# restart
```

The `pa` schema in the database is inert when nothing queries it, so it can be
left in place. `drop schema pa cascade;` removes it completely if wanted — but do
not run that on your own initiative.

## 11. Report back

Write your report to `/home/dremon/futile-pa-deploy-report.md` **and** print it to
the terminal so Andre can copy it. Keep it factual and complete; it is the only
thing the other Claude will see.

Use this structure:

```
## Outcome
One line: fully deployed / deployed but not restarted / stopped at phase N.

## Diagnosis of the broken venv
- pyvenv.cfg said Python X; that interpreter exists / does not exist
- system Python is now Y
- conclusion in one sentence

## Environment built
- new venv path, Python version
- mcp / psycopg / python-dateutil versions
- the old .venv: left untouched / renamed to what

## Test result on PostgreSQL 18
- exact final line of tests/test_smoke.py
- output of: select version()
- if failures: every failing line verbatim, plus tracebacks

## Real database
- database name used, and how you determined it
- migrate.py --status output
- the five row counts, with expected vs actual

## server.py changes
- backup path
- the three edits, as a unified diff
- any other file you changed (systemd unit, wrapper script) with the old and new line

## Server state
- running? under which interpreter and which start mechanism
- tool count from list_tools (expect 29)
- any stderr warnings or tracebacks

## Paths for the record
- futile-pa install path
- JSON context store path (untouched)
- systemd unit path, if any

## Anything surprising
Things a reader would want to know that these headings did not ask about.
Say so plainly if something is in a half-finished state.
```

---

## 12. Notes on judgement

Andre is a senior technology executive with a CS background — write plainly and do
not oversell. If you are unsure whether something is safe, that uncertainty is
worth more in the report than a guess is on the disk.

The point of the ordering in this brief is that each phase is reversible until
Phase 4. Keep it that way: do not reorder phases to save time, and do not run
Phase 3 or 4 on the strength of a test you skipped.
