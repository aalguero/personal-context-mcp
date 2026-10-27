# Personal Assistant — Phase 1 architecture and state

Last updated: 2026-09-15

## Decisions made

- **Backend**: PostgreSQL, dedicated schema `pa`, in Andre's existing database on
  the server that hosts futileMCP. Not the `public` schema, so it coexists with
  anything already there.
- **Deployment**: futileMCP is loose Python files on a separate server Andre
  SSHes into (no repo, not the machine this session links to). Code is therefore
  built and tested in the cloud workspace and delivered as a tarball
  (`futile-pa.tar.gz`) for him to scp across and run.
- **Context store**: currently a JSON file on disk beside the server. Left alone
  in Phase 1. Open question whether it migrates into Postgres later — arguments
  for: one backup regime, one source of truth, joinable with task data.
- **Surface**: MCP tools + a live dashboard. Tools are done; dashboard design
  settled (see below), build blocked on deployment.
- **Google Tasks**: import everything, then curate. Importer written; open tasks
  land in `inbox` status so the curation pass is explicit.
- **Phase 1 scope**: tasks/projects core plus the daily brief. Meals, groceries,
  bills, calendar joins and finance deliberately deferred.

## What exists (delivered 2026-09-15)

`futile-pa.tar.gz` — ~3,900 lines, verified against PostgreSQL 16 with 63
integration checks passing from a clean extract.

```
migrations/001_core.sql           schema, triggers, views, query functions
migrations/002_reference_data.sql contexts, situations, seeded areas/people/goals
pa/db.py                          connections, transactions, JSON coercion
pa/repo.py                        queries and commands
pa/recurrence.py                  RRULE expansion (dateutil) and materialisation
pa/tools.py                       24 MCP tool schemas + framework-agnostic dispatch()
pa/importers/google_tasks.py      Takeout/API import, idempotent
server_fastmcp.py / server_lowlevel.py   adapters for either MCP framework
scripts/migrate.py                migration runner with a checksum ledger
tests/test_smoke.py               the 63 checks
```

### Schema shape

- `pa.project` — one self-referencing table, `kind` ∈ area / program / project /
  person_plan. Areas never complete (enforced by constraint). Programs are
  multi-year but end. Depth unlimited.
- `pa.task` — four distinct date columns (`due_on`, `due_at`, `defer_until`,
  `scheduled_for`) plus `hard_deadline` to separate real constraints from
  preferences. `importance` 1–5, `effort_minutes`, `energy`.
- `pa.context` / `pa.situation` / `pa.situation_context` — the EV-charger model.
  A context is a condition a task requires; a situation supplies a set of
  contexts plus time and energy. `pa.suggest_tasks()` intersects them.
- `pa.recurrence` — two modes: `fixed` (RRULE, calendar-anchored) and
  `after_completion` (interval from actual finish). The second is what Google
  Tasks gets wrong and what generates false overdue items.
- `pa.person` — with `contact_cadence_days` driving `v_connection_due`.
- `pa.goal` + `goal_project` — outcomes separate from delivery vehicles.
- `pa.event` — append-only, trigger-written. `SET LOCAL pa.actor` distinguishes
  Andre's actions from Claude's.
- Views: `v_task`, `v_actionable`, `v_scored`, `v_today`, `v_overdue`,
  `v_waiting`, `v_project_health`, `v_stalled_projects`, `v_review_due`,
  `v_connection_due`.

### Scoring formula (exposed component by component in `v_scored`)

`score = importance(10–50) + urgency(0–90) + neglect(0–15) + age(0–5) + quick_win(0–6)`

- importance is the largest single term, so what matters outranks what merely has
  a date
- urgency is a step function, not a curve, and overdue is capped at 20 days past
  due so one old failure cannot dominate forever
- deferrals raise the score rather than burying the task; ≥3 deferrals surfaces
  in the brief's `neglected` section

### Seeded structure (all tagged `metadata->>'seeded' = 'true'`)

10 areas (House & Land, Finances, Health, Career, Family & Connection, Food,
Admin, Learning & AI, Music, Outdoors), 10 programs/projects (Native Plant
Garden, Farmhouse Projects, Wealth Transition, Tax Planning 2026–27, Job Search,
LP Collection Catalog, Agentic AI Track, Personal Assistant Build, Rosco Senior
Care, Household Admin 2026), 4 person plans, 6 people, 9 goals.

**Needs Andre's attention**: family person records are placeholders ("Daughter",
"Son", "Sister", "Father") because the context store holds relationships but no
first names. Contact cadences (30/30/14/21/60/10 days) are inferred proposals.

## Dashboard architecture — settled 2026-09-15

An earlier version of this doc claimed Andre's Postgres was unreachable from
Claude's side. **That was wrong.** Andre caught it. Recording the correction
because the distinction matters and a future session would otherwise inherit the
error:

- futileMCP is a **remote** MCP server. Claude connects to it over the network
  from whatever environment Claude runs in — cloud session, phone, browser, a
  scheduled task at 6am. No device-local dependency, and no difference between
  desktop and phone.
- The real boundary is narrower: a **published Artifact page is JavaScript in a
  browser sandbox**, so *the page's own code* cannot open a Postgres connection —
  no driver, no credentials, and the sandbox blocks outbound connections to
  arbitrary hosts. That is a different claim from "Claude cannot reach Postgres".

**The design.** Use the Artifact `mcp` runtime capability (available on Andre's
account, runtime contract 0.2.49). The page declares
`capabilities: {mcp: {servers: [{server: "futileMCP", tools: [...]}]}}` and calls
those tools at view time with the viewer's own credentials; tokens are never
exposed to the page.

```
dashboard page (claude.ai URL, phone or desk)
    → mcp capability, Andre's credentials
        → futileMCP (his server)
            → Postgres
```

Genuinely live, not a regenerated snapshot. `watchTool` for displayed data (cache
replay plus staleness refresh), `callTool` for actions such as completing a task.

**Sequencing constraint**: do not publish a page calling `pa_*` tools before
observing a real request/response pair for each in-session. So deployment first,
then observe the payloads, then build against the observed shapes. Never guess a
connector tool's argument names or result encoding.

## MCP vs skill — raised by Andre, worth keeping

- **MCP** is the data and action path: tools, credentials, transport. Postgres
  communication belongs here, and now does.
- **A skill** is instructions for Claude only — no data path, no credentials, no
  network. It cannot talk to Postgres; it tells Claude how to use the tools that
  can.

Both, eventually. The skill is the right home for the procedure layer ("how to
run my morning triage", "how to run a Sunday weekly review", which tools in which
order, what to push back on). Write it once the workflow has settled into
something real rather than guessing it now.

## Open questions

1. **Which MCP framework futileMCP uses** — FastMCP, the low-level
   `mcp.server.Server`, or hand-rolled. Decides which adapter applies. Andre can
   paste the file header to settle it.
2. **Does the JSON context store migrate into Postgres?** Leaning yes but later,
   as a Phase 2 migration keeping `get_context`/`set_context` signatures
   identical so nothing else changes.
3. **Postgres version on the server** — schema targets 13+, verified on 16.

## Next steps

1. Andre deploys: migrate, wire `dispatch` into futileMCP, restart.
2. Claude calls each `pa_*` tool once to observe real payload shapes.
3. Build the live dashboard against those shapes.
4. Google Takeout export of Tasks → import with list mapping → curate the inbox;
   fix the placeholder names and cadences.
5. Calendar + Gmail joins for conflict detection in the brief.
6. Write the procedure skill once the workflow is real.
