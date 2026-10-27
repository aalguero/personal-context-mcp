# Working agreement — three streams, kept apart

Andre, 2026-10-23: *"we need to separate task maintenance from the further
construction of the personal assistant application."*

He asked for the separation in both structure and process. This doc is the process
half; the structure half is live in the database.

---

## The three streams

| Stream | What it is | Where it is filed |
|---|---|---|
| **Life** | The actual point. Wood, garlic, Rosco, taxes, the job search, the family, the house. | Brook House, Career, Finances, Family & Connection, Food, Admin, Outdoors, Music |
| **Operations** | Running the assistant. Alert response, data hygiene, retagging, dedup, reviews, triaging the undated tail. | `Learning & AI / Personal Assistant Operations` — **area**, review cadence 7 days |
| **Build** | Changing the assistant. Schema, engine, MCP server, backup infrastructure, migrations. | `Learning & AI / Personal Assistant Build` — project |

**Household Admin 2026 is for the household**: the car registration, the gutters,
the YubiKey. It is not where the application's infrastructure lives. That mistake is
what made task 156 invisible.

## The rule that matters: capture, don't chase

**In a Life session, a system fault becomes a task and nothing more.** No debugging,
no SQL, no detour — write it to Build or Operations with enough detail to act on
later, say one sentence about it, and go back to what Andre was actually doing.

This is the behaviour the request was aimed at. In the 21–23 October sessions a
conversation about seven cords of firewood repeatedly became a conversation about
`v_actionable` predicates and `effort_minutes` semantics. Each detour was individually
justified and collectively a different conversation from the one he started.

The reverse holds too: **in a Build session, do not reorganise his life.** Capture the
life observation as a task and leave it.

## What each kind of session looks like

**Life session** — the default. Opens with the dashboard or the daily brief. Ends with
his tasks in a better state than it found them. Deliverables are decisions, dates and
completions. System faults found along the way leave as captured tasks.

**Operations session** — short and periodic, prompted by the 7-day review cadence or by
an alert. One sitting, usually SQL or retagging. Deliverable is the data being right.
Tasks 162 and 169 are the current queue.

**Build session** — explicitly invoked, uninterrupted, usually produces a brief for the
Claude CLI on the server (see the `server-brief` skill) and a report read back. Never
started inside a Life session.

## Where a new task goes

- Does it change code, schema or infrastructure? → **Build**
- Is it keeping the existing system's data or health honest? → **Operations**
- Is it Andre's actual life? → **the relevant life area**

The one that gets misfiled is the alert written automatically by a script. Those are
Operations, always — they are the system reporting on itself.

## Still owed, and only Andre can do it

**Add one line to the project instructions in claude.ai.** A doc is read when Claude
remembers to read it; the project instructions are in context for every session in
every surface, which is the difference between a convention and a wish. Suggested:

> Keep three streams apart: my life, running the assistant (Personal Assistant
> Operations), and building it (Personal Assistant Build). In a conversation about my
> life, capture system faults as tasks and keep going — do not start debugging.

## What changed in the data, 2026-10-23

Created `Personal Assistant Operations` (id 37), kind **area**, under Learning & AI,
importance 4, review cadence 7 days. An area because it never completes, and a review
cadence because an ongoing responsibility without one is the thing that rots unnoticed
— which is the whole lesson of task 156.

Moved into Operations: **156** (backup failure alert), **162** (SQL data cleanup,
8 items), **169** (context retagging).

Moved into Build from Household Admin: **123** (narrow the B2 key), **124** (nothing
notices a backup that never runs), **125** (three alert refinements).

Left in Household Admin deliberately: **77** (enroll the backup YubiKey) — Andre's
call. It is his identity, not the application's.

Already in Build: **114** (PiTower case code into git), **161** (the recurrence engine
has no nightly timer), **177** (suggestions offer parent tasks alongside their children).

**Unverified and worth checking in the next Operations session:** whether
`pa_daily_brief` actually surfaces `v_review_due`. The 7-day cadence on Operations is
only a mechanism if something reads it — and this project has produced six separate
cases of a change that controlled nothing. `pa_daily_brief` is implemented in Python in
the MCP server, so it cannot be inspected with `pa_sql`; it has to be run and read.
