# futileMCP / futile-pa — orientation for Claude Code

> **Updated 2026-10-27, evening.** What changed since the 10-26 version: the server's
> unpushed commits **are now on GitHub**, and this clone is current with them — the
> warning that used to be in this file is resolved and has been replaced with the
> facts. The second clone in `~/developer/tmp` turned out to be far staler than this
> file claimed. Three new build tasks exist (181, 182, 183) — **181 is now closed**:
> the check-in agent has been recreated as a local scheduled task reading this repo,
> and the old claude.ai Project trigger is disabled. This file itself was committed to
> git for the first time this evening, along with `docs/`.

You are running on **shadow**, Andre's own computer (tailnet DNS name `darkslip`,
`100.102.50.58`; OS user `andre`). The system you are helping build runs somewhere
else: **futileassistant**, the server, which holds the MCP server, Postgres and
futile-pa. Andre is the only route there.

`docs/` holds a snapshot of the claude.ai Project "Personal Assistant", which is the
design record. The Project is the source of truth; these copies drift. If anything you
observe disagrees with the docs, **trust what you observe and say so.**

## Where things are

| Thing | Where | How you reach it |
|---|---|---|
| Code | `github.com/aalguero/personal-context-mcp` (futile-pa is the `futile-pa/` subdirectory) | **This clone, `~/developer/futile-pa`, is current** — see below. Edit here, commit here |
| Running server, Postgres, `.env`, `context.json` | futileassistant, `/home/dremon/tools/personal-context-mcp` | **Only through briefs** (below). Don't SSH there |
| Brief exchange folder | `~/developer/tmp` on shadow | Read and write directly |
| Live task data | Postgres `futile_assistant`, schema `pa` | Through the futileMCP tools if this session has them (`pa_sql` is read-only). Check before assuming |

**Git state, verified 2026-10-27.** `origin/main` is at **`abea507`** ("Alarm on a dead
off-site leg, re-check the Pi weekly, drop stale PATH") and this clone has been
fast-forwarded to match. The backup work from 10-16 to 10-21 **is** in GitHub and **is**
in this working tree: `rclone copyto --immutable`, the `ssh -n` prune fix,
`backup_alerts.py`, the `backup_alert` metadata markers, `globals.sql` in the bundle.
Verified by grepping the files, not by reading a commit message.

The older warning in this file — that the server had local commits which were never
pushed — **is resolved.** It was true on 10-20 and is not true now. A chat session
re-derived that warning on 10-27 from a timing inference, stated it as fact, and was
wrong; the lesson is in *How to know something worked*, below.

**The second clone is stale, not a parallel workspace.** The 10-26 version of this file
described `~/developer/tmp/personal-context-mcp` as Andre's own working checkout of the
MCP server infrastructure. In fact it sits at `5c21332 Initial commit` from
**18 September** and has no `futile-pa/scripts/backup.sh` at all. Treat it as an
artefact, not a second entity. **Ignore it.** Everything happens in
`~/developer/futile-pa`.

## What is running right now

| Component | State |
|---|---|
| futileMCP server | Live on futileassistant behind Tailscale Funnel, 29 tools, shared-secret auth that returns **404**, never 401 |
| Backups | Proven end to end including a real restore (10-19): verified dumps, age encryption the server cannot reverse, off-machine on the Pi, compliance-locked off-site in B2, four alert kinds keyed on `metadata->>'backup_alert'` |
| **Task check-in agent** | **Live, in this repo now — task 181 closed 2026-10-27.** Local scheduled task `task-checkin` (`~/.claude/scheduled-tasks/task-checkin/SKILL.md`), same four-runs-a-day schedule, reads `docs/claude/task-agent-brief.md` off disk. The old claude.ai Project trigger (`trig_01LP3NydyjH9kb7VsfbjsLXY`) is disabled, not deleted — history kept. **Its rules bind that agent — don't break what it relies on** |
| Recurrence engine | Ten series exist; nothing runs it on a timer (task 161). Recurrences 1 and 9 collide every Monday and cannot be fixed without SQL |

## Things on shadow you must not touch

- **`~/.age/`** holds the age private key that decrypts every backup. A backup bundle
  also contains `.env`, so this key plus any archive is the credential to the whole
  system. Never read, copy, print or move it. Never decrypt a backup unless Andre asks
  in that conversation, in those words.
- **The context store lives only on the server** (`/home/dremon/.personal-context/context.json`),
  reached through `get_context` / `set_context`. A stale April 2026 copy on shadow
  (`~/.personal-context/`) was deleted on 2026-10-26 at Andre's request. **Never
  create a local copy:** an unsynced copy of medical and financial facts gets read as
  current.
- Andre's other folders (`~/FinanceComm`, `~/Documents` and so on) are outside this
  project. Stay in `~/developer/futile-pa`, `~/developer/tmp`, and clones you make
  yourself.

## Two routes, and which to use

**Code authoring happens here now.** The clone is current with production, so writing
and committing code no longer needs a brief. Branch, edit, run what can be run locally,
commit.

**Anything that touches the running system still needs a brief.** Deploying, migrating,
restarting the unit, reading the journal, inspecting `.env`, verifying against live
Postgres — Andre is the only route to futileassistant. A change that is committed is
not a change that is deployed, and this project has produced fifteen corrections'
worth of evidence that the difference matters.

### The brief loop

```
you write  ──▶  ~/developer/tmp/XXX_BRIEF.md              (shadow)
                        │
              Andre runs it with the Claude CLI on futileassistant
                        │
you read   ◀──  ~/developer/tmp/xxx-report.md             (Andre carries it back)
```

Naming: briefs `UPPERCASE_BRIEF.md`, reports `lowercase-report.md`. **Read the folder
before writing a brief.** About 30 earlier briefs and reports are there, and a brief
once asserted things two intervening briefs had already fixed.

The executing Claude has **no memory of this conversation**, so every brief stands
alone. Structure that has worked:

1. One-line framing: which brief, and what state the system is in.
2. Prerequisites Andre must do first. Tell it to **stop and report** if one is missing.
3. Hard rules (below), copied in.
4. `pg_dump -Fc` before any data change, and report the file size.
5. The work in phases, with the *reason* for each step, not just the command.
6. Verification: exact commands and the output expected.
7. Rollback.
8. A report written to **`/home/dremon/developer/tmp/<name>-report.md`** on the server
   (the server cannot reach shadow) and also printed. End the report with
   *"Anything that looks wrong to you"*, then *"Observed vs inferred"* as two lists.
   That last section has caught seven wrong assumptions.

Close every brief with: *If the machine disagrees with this brief, trust the machine
and say so.*

When a report comes back, record durable findings so the chat session can put them
in the Project docs: state goes in `deployment-status.md`, open work in `backlog.md`.
You can't write the Project, so tell Andre what to carry over.

## Hard rules: for you, and copied into every brief

1. **Never print, log or echo** `PA_AUTH_TOKEN`, `PGPASSWORD`, any B2 key, or the
   contents of `.env`, not even partially masked.
2. **`/.well-known/*` must keep returning 404.** Anything OAuth-shaped there makes
   claude.ai attempt dynamic client registration, which fails.
3. **Never change Tailscale or Funnel exposure.** That is Andre's decision alone.
4. **Never edit an applied migration** (001, 002, 003). `migrate.py` checksums them
   and refuses to run if one changes. New work goes in a new file.
5. **`tests/test_smoke.py` never runs against `futile_assistant`.** It writes. It's
   guarded (`pa_verify` / `pa_scratch*` only), and it once put 26 fixture rows into
   production.
6. **Don't touch `server.py`, `AuthMiddleware` or the service unit** unless that is
   the task.
7. **`mcp==1.27.0` stays pinned.** 2.x removes the low-level `Server` decorators and
   would take down all 29 tools.
8. **Never create a recurrence** (`pa_recurrence_add`). It is create-only: no update,
   no deactivate, no delete. A mis-specified series is permanent until someone runs
   SQL, and two already collide every Monday because this was done once without
   checking.
9. **Do not upload anything to the B2 bucket.** Compliance mode makes every object
   undeletable for 30 days, test files included.
10. **Stop and report** rather than improvise when a prerequisite is missing or a check
    fails.

## Server facts that briefs keep getting wrong

| Fact | Note |
|---|---|
| Interpreter | `/home/dremon/tools/personal-context-mcp/.venv-new/bin/python` — **not** under `futile-pa/` |
| Postgres role | `musicbrainz`, not the OS user. `createdb` needs `-U musicbrainz` |
| Scratch DB | Run `scripts/migrate.py` before the smoke test, unless you're restoring a dump |
| Smoke test | `tests/test_smoke.py`, expect `63 passed, 0 failed` |
| `env -u PA_DATABASE_URL` | Doesn't unset it: `load_dotenv()` (override=False) reinstates it |
| Backup scripts | `futile-pa/scripts/backup.sh` — note the prefix |
| Report path | `/home/dremon/developer/tmp/` on the server, not the shadow path |
| Pip outside a venv | needs `--break-system-packages` |
| Dates | Postgres `current_date` is US Eastern. A UTC container reads a day ahead in the evening |
| `pa_sql` | Read-only, and rejects `%` in strings. Use `position('x' in lower(col)) > 0` |

## How to know something worked

> **A change is not verified by the system still working. It is verified by proving
> it controls something.**

Fifteen corrections deep, every one came from data rather than more reasoning. List
the directory, not the exit code. Point the config at nonsense and watch it fail.
Check the object, not the setting. An empty log or grep result needs a positive
control before it counts. Run a check on the machine that has the thing, and name the
host.

Two of the fifteen are recent and both are worth internalising, because they were
made by a Claude session rather than found in the code:

**A write reported success and changed nothing.** A file was committed to this repo,
the tool returned `{"written": [...]}`, and the file on disk was still the previous
version. It was caught only by grepping the file for text that existed solely in the
new version. **Verify a write by reading back content, not by trusting the return
value.**

**An inference was presented as a fact.** A session correctly noted it could not read
GitHub — the repo is private and `git fetch` had failed for want of credentials — then
reasoned from directory timestamps to the confident conclusion that Andre had never
pushed. He had. The honest answer, *I can't verify this from here*, was available and
got talked past. **When you have said you cannot check something, that remains true
three sentences later.**

## The docs

Imported here because they govern every session:

@docs/claude/working-agreement.md
@docs/architecture/deployment-status.md

`deployment-status.md` is written from the chat session's side. "This session has no
route to the server" is true for you too.

Read the rest on demand. Newest wins where they disagree:

| Doc | Updated | For | Superseded parts |
|---|---|---|---|
| `architecture/phase-1-task-backend.md` | 09-15 | Schema, scoring formula, design reasoning | Its deployment notes ("no repo", tarball) |
| `architecture/backlog.md` | 10-06 | Migration 004 spec (§H), tool gaps (§J), triage record | §B (futile-pa is now in git) and §F backups (done) |
| `architecture/backup-status.md` | 10-16 | backup.sh internals, result states, host keys | "No off-site / no restore": both done since |
| `architecture/encryption-at-rest.md` | 10-19 | Threat model, age design, bundle contents | — |
| `architecture/deployment-status.md` | 10-20 | **Current state.** Start here | Its unpushed-commits note: resolved 10-27 |
| `architecture/voice-assistant.md` | 10-14 | The Pi 5 voice component (`vocalassistant`) | — |
| `architecture/backlog-dashboard.md` | 10-22 | The dashboard artifact and its queries | — |
| `claude/working-agreement.md` | 10-23 | Life / Operations / Build streams | — |
| `claude/voice-assistant-brief.md` | 10-26 | **Start here for voice work.** Handoff brief: what works, settings, decisions, next steps | Keep its working method: give Andre one command block at a time for the Pi and read his output. Don't SSH to `vocalassistant` unless he says so |
| `claude/task-agent-brief.md` | **10-27** | The live check-in agent — spec, first-run evidence, and the handover plan (§11) for recreating it here | — |

Work here is almost always **Build**. Per the working agreement, don't reorganise
Andre's life from a Build session. Note a life observation in one line and leave it.

## Open work (check before assuming it's still open)

Tasks live in Postgres. `pa_sql` against `pa.v_task` is the authority; the numbers
below are pointers, not a substitute.

**Build — in `Learning & AI / Personal Assistant Build`:**

- **181 — DONE 2026-10-27.** The check-in agent is recreated here (local scheduled
  task `task-checkin`, reading `docs/claude/task-agent-brief.md` off disk) and the
  Project trigger is disabled. §11 of the brief has the full record, including the
  confirmed-harmless overlap window between creation (10-26 22:26) and the disable
  (10-27 night).
- **182 — Nothing watches whether the agent is alive.** It writes
  `task_agent.last_run_at` every run and nothing reads it. Put the check in
  `pa_daily_brief`, not a second timer — a second timer is one more thing that can die
  quietly. Mind the threshold: the overnight gap is twelve hours, so a naive six-hour
  check cries wolf every morning.
- **183 — `pa_task_update` has no `depends_on`.** Dependencies can be declared at
  creation and never added. Live consequence: task 80 must precede task 15, and that
  ordering exists only as prose, so task 15 never reports blocked.
- **161 — nothing runs the recurrence engine.** Ten series, zero generated tasks until
  someone runs it by hand.
- **123 — narrow the B2 application key** to `listBuckets, listFiles, readFiles,
  writeFiles`. Needs `b2_create_key`; the console can't express it.
- **124 — nothing notices a backup run that never happens.** Same shape as 182.
- **Migration 004:** task gates, `estimated_cost`, `v_budget_queue`, a proper `source`
  value for scripts. Spec in `backlog.md` §H. It now has a concrete waiting
  population: seven budget-gated `someday` tasks.

**Operations — in `Learning & AI / Personal Assistant Operations`** (an area, 7-day
review cadence). Data hygiene, not code:

- **162** — nine collected items needing SQL, including the Monday recurrence
  collision, the `dry` context stuck on task 166, and clearing the stale dates on
  task 51.
- **169** — the `@lowenergy` and seasonal retagging. Two tasks carry `@lowenergy`
  against 43 on `@computer`, which inverts the whole point of the suggestion engine.

**A pattern worth naming, because three of the above are instances of it:** the tool
surface was built for capture and not for correction. Contexts can be set and never
changed (silently). Dates can be moved and never cleared. Dependencies can be declared
at birth and never added. Capture is the cheap half; correction is where keeping data
honest actually happens. The decision to start with capture only was made as a secondary 
security measure before full security was implemented. It is now open for update
