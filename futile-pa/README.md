# futile-pa — Phase 1

A Postgres backend and MCP tool layer for the personal assistant, built to drop
into the existing futileMCP server.

Phase 1 is the spine: tasks, the project/program/area hierarchy, contexts and
situations, recurrence, people and contact cadence, goals, an event log, and a
daily brief. Everything later — meals, groceries, bills, the finance component,
the family sub-projects — hangs off these tables rather than needing new ones.

Verified end to end against PostgreSQL 16 before delivery: 63 integration checks,
covering the behaviours that are easy to get wrong and hard to notice. Also
driven through the real MCP SDK (1.27.0) using the low-level `Server` API — the
same shape as your existing server — confirming all 25 tool schemas are accepted,
that calls round-trip, and that your existing `get_context` still routes.

**Your Postgres is 18; I could only test on 16** — the package repository for 18
is blocked from my sandbox. Nothing here uses a feature that changed between 16
and 18: no expression-generated columns (the one relevant PG 18 change, where
`GENERATED ... AS (expr)` now defaults to `VIRTUAL`), no `MERGE`, no temporal
constraints, and every function fully qualifies its object names so search-path
changes cannot bite. The migrations are transactional, so a failure rolls back
cleanly. Still, run `tests/test_smoke.py` on your box against a scratch database
before trusting it — that is the definitive answer for PG 18, and it takes a
minute. See section 1.

```
migrations/001_core.sql          schema, triggers, views, query functions
migrations/002_reference_data.sql contexts, situations, your areas/people/goals
pa/db.py                         connections, transactions, JSON coercion
pa/repo.py                       queries and commands — the behaviour
pa/recurrence.py                 RRULE expansion and materialisation
pa/tools.py                      25 MCP tool schemas + dispatch()
pa/importers/google_tasks.py     Takeout / API import, idempotent
server_fastmcp.py                adapter if futileMCP uses FastMCP
server_lowlevel.py               adapter if it uses mcp.server.Server
scripts/migrate.py               migration runner with a ledger
tests/test_smoke.py              the 63 checks
```

---

## 1. Install

On the server, beside the existing futileMCP files:

```bash
tar xzf futile-pa.tar.gz            # creates futile-pa/
cd futile-pa
pip install -r requirements.txt     # psycopg[binary], psycopg_pool, python-dateutil

export PA_DATABASE_URL='postgresql://USER:PASS@localhost:5432/YOURDB'
python3 scripts/migrate.py          # creates schema pa, applies both migrations
python3 scripts/migrate.py --status
```

Both migrations are idempotent and transactional — a failure leaves nothing half
applied. The runner records what ran in `pa.schema_migration` and refuses to
re-run a file whose contents changed after it was applied, which is the failure
mode that quietly corrupts hand-managed SQL.

Everything lives in schema `pa`, so it coexists with whatever else is in that
database. Nothing touches `public`.

Then check it:

```bash
python3 tests/test_smoke.py         # needs an empty-ish database; see the note below
```

The smoke test writes its own sample data, so point it at a scratch database
rather than the live one:

```bash
createdb futile_test
PA_DATABASE_URL='postgresql://USER@localhost/futile_test' python3 scripts/migrate.py
PA_DATABASE_URL='postgresql://USER@localhost/futile_test' python3 tests/test_smoke.py
```

## 2. Wire it into your server

Your `server.py` uses the **low-level SDK** (`from mcp.server import Server`,
`stdio_server`, `mcp.types.Tool` / `TextContent`), so this is your case. Three
edits, and nothing existing changes.

`pa.tools.dispatch(name, args)` is the whole integration surface: a plain function
taking a tool name and a dict, returning JSON-able Python.

**Edit 1 — imports, beside your existing ones:**

```python
from pa.tools import TOOLS as PA_TOOLS, dispatch as pa_dispatch
```

Either drop the `pa/` package next to `server.py`, or keep this directory
separate and add it to the path:

```python
import sys; sys.path.insert(0, "/path/to/futile-pa")
```

**Edit 2 — your `@server.list_tools()` handler.** Append to whatever it returns:

```python
@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        # ... your existing get_context / set_context / list_categories / delete_context
    ] + [
        Tool(name=t["name"], description=t["description"], inputSchema=t["inputSchema"])
        for t in PA_TOOLS
    ]
```

**Edit 3 — your `@server.call_tool()` handler.** One branch at the top:

```python
@server.call_tool()
async def call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    if name.startswith("pa_"):
        args = {k: v for k, v in (arguments or {}).items() if v is not None}
        return [TextContent(type="text",
                            text=json.dumps(pa_dispatch(name, args),
                                            ensure_ascii=False, indent=2, default=str))]
    # ... your existing dispatch, untouched
```

Stripping `None` values matters: the MCP client sends unset optional parameters
as null, and passing those through would override the defaults in the repo
functions with `None`.

Every tool is prefixed `pa_`, so nothing collides with `get_context`,
`set_context`, `list_categories` or `delete_context`. This exact arrangement was
driven through MCP SDK 1.27.0 here — 26 tools advertised, calls round-tripping,
the existing handler still reached.

`dispatch` is synchronous. It does blocking DB I/O inside your async handler,
which is fine for a single-user server (queries run in single-digit
milliseconds); if it ever bothers you, wrap it in
`await asyncio.to_thread(pa_dispatch, name, args)`.

**Config.** Set `PA_DATABASE_URL` in the server's environment, or — since your
file already parses args and loads config — call this once at startup:

```python
from pa.db import Db, set_db
set_db(Db(url=YOUR_DSN, actor="andre"))
```

The `actor` string is what the event log records, so writes made by you through
the server are distinguishable from writes Claude makes. If you leave it as the
default it records `claude`.

**SSE mode.** Your server already supports SSE for claude.ai, which is what makes
the live dashboard possible later — a published page can call these tools through
the connector with your credentials. Nothing extra to do now.

**Sanity check first, without touching `server.py`:**

```bash
PA_DATABASE_URL=... python3 server_lowlevel.py    # serves only the pa_ tools on stdio
```

## 3. Cron

Two jobs. Neither is required for the tools to work, but recurrence does nothing
useful unattended without the first.

```cron
# Materialise recurring tasks that are now due to exist. Idempotent.
15 5 * * *  cd /path/to/futile-pa && PA_DATABASE_URL=... python3 -c \
            "from pa import recurrence; print(recurrence.run())" >> /var/log/futile-pa.log 2>&1
```

The brief itself is better triggered from the Claude side (a scheduled task that
calls `pa_daily_brief` and writes it up) than from cron, since the value is in
the narration, not the JSON.

## 4. Importing Google Tasks

No Google Tasks connector is attached to this session, so the export has to come
from you. The easiest route:

1. <https://takeout.google.com> → Deselect all → select **Tasks** → export.
2. The archive contains `Tasks/Tasks.json`.

Then, on the server:

```bash
# See what would happen first — no writes.
python3 -m pa.importers.google_tasks Tasks.json --dry-run

# Route lists you already have projects for; everything else gets its own
# project under a new 'Imported' area.
python3 -m pa.importers.google_tasks Tasks.json \
    --map "Garden=native-garden" --map "Money=finances" --map "House=farmhouse"
```

Three things it does deliberately:

- **Open tasks land in `inbox`, not `next`.** They have not been triaged in this
  system. Several hundred untriaged items in the actionable pool would make the
  first fortnight of suggestions useless, and the brief will keep showing the
  inbox count until it is cleared. That is the curation pass you asked for.
- **Due dates are not timezone-converted.** Google writes a due date as
  `2026-09-20T00:00:00.000Z` and means *the 20th*. Parsing it as an instant and
  converting to America/New_York moves every due date back one day — a bug you
  would not notice for weeks and then could not explain. The date is taken from
  the string directly.
- **Re-running is safe.** Keyed on the Google task id, so a second import updates
  instead of duplicating.

Google does not export recurrence rules, so anything that repeats comes across
as a single task. Recreate those with `pa_recurrence_add`, where fixed vs
after-completion can be stated properly — see below.

---

## 5. Why it is built this way

### Areas never complete; projects do

`pa.project` holds four kinds in one self-referencing table:

| kind | meaning |
|---|---|
| `area` | an ongoing standard. House & Land, Health, Finances. Reviewed, never finished. |
| `program` | multi-year, multi-project, but does end. Native Plant Garden, Farmhouse Projects. |
| `project` | has a definable done state. |
| `person_plan` | one family member's container. |

A database constraint refuses to mark an area done, because an area that can be
completed is a project that has been mislabelled, and areas leaking into
"overdue" is what makes people stop trusting a system.

One table rather than three because the queries you actually want —
*everything under the garden*, *which active thing has no next step* — are the
same query at every level, and one recursive CTE serves them all.

### Four date columns, not one

| column | question it answers |
|---|---|
| `due_on` | the deadline day |
| `due_at` | deadline with a time, when that matters |
| `defer_until` | do not show me this before then |
| `scheduled_for` | the day I *intend* to do it |

Collapsing these is the single most common reason task systems stop being
trusted: an intention and a deadline look identical, everything turns pink, and
overdue stops meaning anything. `hard_deadline` separates a real external
constraint (a filing date, a nursery pickup window) from a preference.

### Two kinds of recurrence

This is where Google Tasks fails you.

- `mode='fixed'` with an RRULE — occurrences fall on calendar dates regardless of
  when you last did it. Mortgage on the 1st, quarterly estimates, annual physical.
  Missing one does not move the next.
- `mode='after_completion'` with `interval_days` — the next is N days after you
  actually finished. Furnace filter, gutters, chimney sweep. Doing it three weeks
  late legitimately shifts the whole future series.

Modelling the second kind as the first is what generates a pile of false overdue
items, and a list that lies to you gets ignored. `pa.complete_task()` re-anchors
after-completion series to the real finish date in the same transaction as the
completion.

RRULE expansion runs in Python via `dateutil`, not SQL — reimplementing RFC 5545
in plpgsql would be a bad trade. `pa_recurrence_preview` shows the next six dates
without creating anything, which is the fastest way to catch an RRULE that does
not mean what you intended.

### Situations: the EV charger problem

The feature you described — *when I'm at a station charging, that's a good time to
make phone calls* — is modelled properly rather than as a tag convention.

A **context** is a condition a task requires: `@phone`, `@field`, `@handtools`,
`@dry`, `@focus`, `@lowenergy`. A **situation** is a named recurring circumstance
that *supplies* a set of contexts plus a time budget and an energy level:

| situation | supplies | minutes | energy |
|---|---|---|---|
| `ev_charging` | phone, computer, online, lowenergy | 35 | medium |
| `dog_walk` | phone, field, dry, daylight | 45 | medium |
| `desk_block` | computer, online, home, focus | 90 | high |
| `land_day` | field, barn, handtools, dry, warm, daylight | 300 | high |
| `town_trip` | errand, town, car, phone | 90 | medium |
| `rainy_day` | home, computer, handtools, barn, focus | 240 | medium |
| `evening_low` | home, phone, online, lowenergy | 40 | low |
| `waiting_room` | phone, online, lowenergy | 20 | low |
| `morning_coffee` | phone, computer, online, home | 30 | low |

`pa_suggest_now(situation='ev_charging')` then returns tasks where the situation
supplies **every** context the task requires (not merely one — a task needing
`@phone` *and* `@quiet` is no good in a waiting room), the effort fits the time,
and the energy demanded does not exceed what is on offer. Ranked by score.

Against the test data that produces, at a charger with 40 minutes:

```
[137] Pay the mortgage                              10 min; low; due 2026-09-01
[ 99] Book Rosco's senior bloodwork                  8 min; low; due 2026-09-12
[ 78] Ask sister to confirm the custodian change     15 min; low; due 2026-09-18
[ 71] Call Wing and a Prayer about June pickup       10 min; low; due 2026-09-17
[ 65] Recruiter follow-ups — three outstanding       25 min; medium
```

and at a land day, the sheet-mulching — which the charger correctly never offers.

### The score, and why you can see the arithmetic

`pa.v_scored` exposes every component as its own column, so a ranking can be
explained rather than asserted:

```
score = importance + urgency + neglect + age + quick_win
```

| term | range | how |
|---|---|---|
| importance | 10–50 | your 1–5 rating × 10 |
| urgency | 0–90 | step function of days-to-due, × 1.5 if `hard_deadline` |
| neglect | 0–15 | 3 per deferral, capped |
| age | 0–5 | 1 per fortnight in the system |
| quick win | 0–6 | ≤10 min: 6, ≤20 min: 3 |

Three deliberate choices in that:

**Importance is the largest single term.** What matters should outrank what merely
has a date attached. Urgency-only ranking is how a week disappears into other
people's deadlines.

**Urgency is a step function, not a curve,** because "due in 9 days" and "due in
11 days" genuinely feel the same, while "today" and "tomorrow" do not. Smooth
decay models a precision that is not there.

**Overdue urgency is capped** at 20 days past due. Without the cap, one thing you
failed to do six months ago outranks everything else forever and the list becomes
a monument to a single failure.

Deferrals *raise* the score rather than burying the task. Something pushed five
times either matters and keeps losing to noise, or should be admitted as never
happening — both are worth being shown. Three or more deferrals puts it in the
brief's `neglected` section.

### The event log

`pa.event` is append-only and written by trigger, so nothing can change without
leaving a trace. State tables answer *what is true*; the log answers *what has
been happening*, which is usually the more useful question: how long the barn has
been untouched, how often the resume slipped, whether the garden actually moved
last month. `pa_activity` reads it, and the weekly-review queries depend on it.

`SET LOCAL pa.actor` makes the log distinguish your actions from Claude's.

### Stalled projects

`pa.v_stalled_projects` finds active projects and programs with zero actionable
tasks. This is the highest-value query in the system: it catches the silent
failure where something you care about has no physically next step and has
therefore quietly stopped moving, without ever appearing as overdue. Areas are
excluded, since an area with no open task is often just fine.

### The read-only SQL tool

`pa_sql` runs one SELECT. Guarded three ways: a **read-only transaction** (so
Postgres itself refuses writes, whatever the statement text looks like), a
statement timeout, and a row cap. Statement-text blacklisting is deliberately
*not* one of the guards — keyword filtering is easy to fool and a read-only
transaction is not.

It exists because a personal ontology changes faster than a fixed tool surface,
and the alternative is me adding a tool every time you want a cross-cutting
count. If you would rather not have it, delete the `"sql"` entry from `HANDLERS`
and the matching `_t("sql", ...)` block in `pa/tools.py`. Migration 001 also
creates a `pa_reader` role (`nologin`, SELECT only) if you would prefer to point
it at a separate credential.

---

## 6. The 25 tools

| tool | for |
|---|---|
| `pa_daily_brief` | the whole morning picture in one call |
| `pa_suggest_now` | what to do given situation / minutes / energy |
| `pa_task_add` · `pa_task_list` · `pa_task_update` | capture and query |
| `pa_task_complete` · `pa_task_defer` · `pa_task_drop` | close out, with recurrence rollover and deferral logging |
| `pa_task_history` | one task's event log |
| `pa_project_tree` · `pa_project_add` · `pa_project_update` · `pa_project_review` | hierarchy and health |
| `pa_person_list` · `pa_person_upsert` · `pa_log_contact` | people and contact cadence |
| `pa_goal_list` · `pa_goal_add` | outcomes, and what serves them |
| `pa_recurrence_add` · `_list` · `_run` · `_preview` | repeating work |
| `pa_vocabulary` | available contexts and situations |
| `pa_activity` | recent events across everything |
| `pa_sql` | read-only escape hatch |

Tool *descriptions* carry the guidance a model needs about when to reach for
each one — they are the only thing it reads when choosing. They are worth
reading yourself in `pa/tools.py`; they encode most of the intended behaviour.

---

## 7. Seeded structure — check it

Migration 002 creates 10 areas, 10 programs/projects, 4 person plans, 6 people
and 9 goals, derived from your context store. All of it carries
`metadata->>'seeded' = 'true'`, so it is obvious what was inferred rather than
stated by you. Two things need your attention:

- **The family records are placeholders.** The context store has relationships
  but no first names, so they are literally "Daughter", "Son", "Sister",
  "Father". Fix with `pa_person_upsert`.
- **The contact cadences are my proposals**, read off "kids monthly to
  bi-monthly", "close with sister", "very close with a senior neighbor": 30, 30,
  14, 21, 60, 10 days. They drive the connection list, so they are worth setting
  deliberately rather than inheriting.

## 8. Not in Phase 1

Deliberately deferred, in the order they probably matter:

- **The dashboard.** Needs a decision about how it reaches the data — your
  Postgres is not reachable from Claude's side, so either a page regenerated and
  republished each morning from a scheduled task (fresh as of the last run,
  works from your phone), or something served on the server itself reading
  Postgres live (truly live, only on your LAN).
- **Calendar and Gmail joins.** The brief currently knows nothing about your
  actual day. Connectors for both are available in session; wiring conflict
  detection ("you've scheduled 4 hours of land work on a day with two calls")
  means putting calendar events alongside `scheduled_for`.
- **Bills.** A recurrence with `hard_deadline` and an amount gets most of the
  way; a proper payables table with amounts and confirmation is a small
  migration.
- **Meals and groceries.** Wants its own tables (recipe, meal plan, pantry,
  shopping list) with a vitamin-K field on ingredients, given the INR
  constraint. That constraint is exactly the kind of thing a system should
  enforce rather than leave to memory.
- **Finance.** Connected but separate, as you framed it. The account and
  holdings detail currently in the context store wants real tables once the
  Mercer transition settles.
