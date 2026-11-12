# Claude Code handoff — everything a new session needs

Written 2026-11-11. **This is the file to hand to a fresh Claude Code session.** It is
self-contained: a session that reads only this can work on the project without guessing.

Andre Alguero's personal assistant: a Postgres task backend, an MCP server, a
standalone agent that checks in four times a day over Signal, and a monitoring chain
built in November. Not a product. One user, one household, five acres.

---

## 1. The machines, and which one has the thing

| Host | Role | Addresses |
|---|---|---|
| **futileassistant** | **Everything that matters.** Postgres, the MCP server, the futile-pa agent, the Signal container, all timers | tailnet `100.108.86.16` / LAN `192.168.86.35` |
| **shadow** (DNS `darkslip`) | Andre's laptop. Holds the repo clone, the brief/report exchange folder, and the age private key | `100.102.50.58` / `192.168.86.40` |
| **vocalassistant** | Raspberry Pi. Off-machine backup target, future voice capture. Gets switched off | `100.75.132.20` / `192.168.86.251` |
| **homeassistant** | Home Assistant, reachable via Tailscale Serve | `100.96.213.73` |

**Run checks on the machine that has the thing.** Diagnostic commands have been run on
shadow for a condition on futileassistant more than three times. State the host
explicitly in every brief and in chat.

**A chat session cannot reach futileassistant.** It reaches shadow through the device
bridge, and it can read the database read-only through `pa_sql` over the MCP
connector. That asymmetry is why briefs exist (§3).

## 2. GitHub and the repo

- **Remote:** `git@github.com:aalguero/personal-context-mcp.git` — SSH, private.
- **Branch:** `main`. No PR workflow; commits go straight to main.
- **Who can push:** Andre, from his own terminal on shadow, and the server agent, from
  futileassistant. The chat sandbox has no key, and the device VM that runs commands on
  shadow cannot reach `~/.ssh` (protected location), so `git ls-remote` there fails
  with `Host key verification failed`.
- **HEAD:** `git log -1 origin/main` is the authority. What is *deployed* is recorded in
  `architecture/deployment-status.md`, because a pushed commit is not a deployed one.

**Layout** (server path `/home/dremon/tools/personal-context-mcp/`):

```
server.py                     MCP server, ASGI auth middleware, tool registration
.env                          mode 600. PGPASSWORD, PA_AUTH_TOKEN, age recipient,
                              B2 creds, SIGNAL_*, DEADMAN_PING_URL, BACKUP_PING_URL
futile-pa/
  pa/                         the data layer, shared by the server and the agent
    repo.py                   queries, daily_brief, suggest
    tools.py                  TOOLS + HANDLERS for the pa_* surface
    shop.py                   shopping tools (kept separate on purpose — see §7)
    recurrence.py             run(), preview(), occurrence expansion
    db.py
  assistant/                  the standalone agent — NOT imported by the MCP server
                              (server.py loads only agent_keys.py, by file path)
    main.py                   starts the Signal bridge thread, then the scheduler
    checkin.py                run_once(), heartbeat_line(); reads the brief fresh
    signal_bridge.py          send/receive, history, delivery receipts
    notify.py                 outbound with retries; returns success
    watchdog.py               in-process staleness checks
    context_store.py          context.json read-modify-write
    quickask.py, agent_loop.py, toolset.py, sensitivity_filter.py
    ledger.py                 code-owned task_agent keys: asked, replied, strikes (213)
    agent_keys.py             the list of those keys; no imports, shared with server.py
    task_ids.py               the one task-id pattern (filter and ledger)
  scripts/
    backup.sh                 nightly; §12 external_watchdog, §13 deadman ping
    external_watchdog.py      five assertions, alerts via the Signal container
    recurrence_runner.py      nightly 03:00
    deadman_ping.py           hourly
    backup_alerts.py, migrate.py, prune_backups.py
    systemd/                  MCP, deadman-ping and recurrence-runner units,
                              byte-identical to the installed copies. The backup unit
                              is not in git; the agent's is in assistant/systemd/
  migrations/                 001_core, 002_reference_data, 003_situation_fit,
                              004_shopping, 005_overdue_and_defer,
                              006_deterministic_order,
                              007_hard_deadline_guard
  tests/test_smoke.py         93 checks; refuses any DB but pa_verify / pa_scratch*
  tests/test_shopping.py      95 checks, same guard
  tests/test_ledger.py        42 checks, no DB; refuses unless the store is redirected
  tests/test_toolset.py       30 checks; which agent path holds the shop tools (212)
  tests/test_log_hygiene.py   27 checks, no DB; no model text, reply text or traceback reaches the journal
docs/claude/task-agent-brief.md   the agent's own instructions — LIVE CONFIG, see §7
docs/architecture/                design records
CLAUDE.md                     repo-level orientation
```

**The venv is `/home/dremon/tools/personal-context-mcp/.venv-new/bin/python`**, Python
3.13.5. Not `futile-pa/.venv-new`. This has been got wrong in a brief before.

## 3. How work actually gets done — the brief loop

```
Claude (chat)  ──writes──▶  /home/andre/developer/tmp/XXX_BRIEF.md   (on shadow)
                                      │  Andre carries it
                            Claude CLI on futileassistant executes
                                      │
Claude (chat)  ◀──reads───  /home/andre/developer/tmp/xxx-report.md
```

- Briefs are `UPPERCASE_BRIEF.md`; reports are `lowercase-report.md`.
- The executing agent writes its report to `/home/dremon/developer/tmp/` and **Andre
  carries it to shadow.** Name the server path as the destination in the brief.
- Write briefs with `device_commit_files`, then **verify by reading content back** —
  a `{"written": [...]}` result has been wrong once.
- **Read the exchange folder before writing a brief.** A context window is not the
  project's memory; the folder and the docs are.

### What a brief contains, in this order

1. **§0 Why this exists** — the measured evidence, not the hypothesis. Tables of real
   numbers beat prose.
2. **Hard rules** — the secrets list, what not to touch, and anything this brief
   specifically must not do. Numbered, so a report can cite "rule 4".
3. **Insurance** — `pg_dump` with `umask 077`, plus a copy of whatever is about to be
   edited. `cp -p` preserves mode and defeats `umask`, so `chmod 600` after.
4. **Push first, then pull** — commits have been left unpushed on futileassistant
   twice. Push at the end too, and confirm with `git ls-remote`.
5. **The change**, with the reasoning for each decision, not just the instruction.
6. **Prove it** — the section that matters. Name the specific test that would catch
   the specific failure, including the false-positive case.
7. **Rollback.**
8. **Report** — what the report must contain, explicitly.
9. Two closing requirements: **"Anything that looks wrong to you"** with pointed
   questions, and **"Observed vs inferred", two lists kept apart.**
10. The last line: **"If the machine disagrees with this brief, trust the machine and
    say so."**

### What a report contains

The same shape in reverse, plus the convention that has made this work:
**"The machine disagreed with the brief in N places"**, each one marked and explained.
Every report so far has found real errors in its brief. That is the point of the
format, not a failure of it.

## 4. The doctrine

> **A change is not verified by the system still working. It is verified by proving it
> controls something.**

Twenty-one instances of the opposite have been found on this project. `FUTILE_PA_PATH`
sat inert in `.env` because it was read three lines before `load_dotenv()`. A dedicated
SSH key was inert from birth. Remote retention deleted one file of five while reporting
`ok`. The B2 upload reported "skipped (not configured)" over code that could never have
worked. A `§4` requirement in the agent brief was believed live for days while sitting
as an uncommitted edit in a working tree.

Three corollaries, each learned the hard way:

- **A protective feature being enabled is not the protection applying.** B2 Object Lock
  was on with no default retention; every object would have arrived unlocked.
- **A monitoring decision has a shelf life.** The right alarm depends on what normal
  is, and normal changes.
- **A watchdog asserts on outcomes, never on health.** During the November outage the
  unit reported `active (running)`, `Result=success`, `NRestarts=0` for three days of
  total functional failure.

And the practice that follows from it: **positive controls.** Before trusting a test,
break the thing it watches and confirm the test fails. This has caught two tests that
could not fail — a thread-safety check that passed with the lock removed (the GIL makes
`list.append` atomic) and a recurrence self-check that never expanded a future-dated
rule.

**Method:** test before theorising. Two curls settled a transport question after a day
of speculation. A three-line loop settled an `ssh -n` question in one command.

## 5. The three streams, and the rule about them

| Stream | What | Where filed |
|---|---|---|
| **Life** | Wood, garlic, taxes, the job search, family, the house | the life areas |
| **Operations** | Running the assistant: alerts, data hygiene, retagging | area 37, `Personal Assistant Operations` |
| **Build** | Changing the assistant: schema, engine, MCP server, monitoring | project 18, `Personal Assistant Build` |

**The rule: capture, don't chase.** In a Life session a system fault becomes a task and
nothing more. In a Build session, do not reorganise his life — note the observation and
leave it.

**It is auditable, and the audit is the point.** `claude/working-agreement.md` holds
two queries that show which streams a session wrote to. The standard in a Build session
is **zero life writes**. Measured on 2026-11-09: 36 Build/Ops writes, 8 life writes all
made by the agent from Andre's Signal replies, and 1 by Claude — a stale
`scheduled_for` tidied while passing, which nobody had asked for.

## 6. Hard rules that apply to every brief

1. **Never print, log or echo** `PA_AUTH_TOKEN`, `PGPASSWORD`, any B2 key,
   `ANTHROPIC_API_KEY`, either ping URL, `.env` contents, **either phone number**, or
   the Signal PIN — not even partially masked. Never put a DSN or a URL on a command
   line; argv is visible in a process listing. Read `.env` in-process.
2. **Never edit an applied migration.** Take the next number from
   `pa.schema_migration`, which is **keyed by `filename`, not `version`**. Use
   `create or replace` in a new migration to change a function or view.
3. **Exposure is Andre's decision alone.** Never change Tailscale or Funnel.
4. **`/.well-known/*` must keep returning 404.** Anything OAuth-shaped there makes
   claude.ai attempt dynamic client registration, which fails and cost a day.
   Unauthenticated requests get **404, never 401**.
5. **Never read or copy `/home/dremon/.signal-cli-rest-api`.** That directory *is* the
   Signal account.
6. **Never create a recurrence.** `pa_recurrence_add` is create-only with no undo.
7. **`tests/test_smoke.py` never runs against `futile_assistant`.** The suite refuses
   any database but `pa_verify` / `pa_scratch*`, structurally, after an agent once
   wrote 26 fixture tasks into production.
8. **No tracebacks in logs.** Class name or `SignalAPIError` text only — model and tool
   errors can carry task content.
9. **`mcp==1.27.0` stays pinned.**
10. **Do not upload probe files to B2.** Compliance mode makes them undeletable for 30
    days. Do not delete from B2 or change its lock, retention or lifecycle.
11. **The age private key never goes on the server.** It lives on shadow and on paper
    with the estate documents. That asymmetry is what makes server-side encryption
    meaningful.
12. **Stop and report rather than improvise.**

## 7. Decisions already made — do not relitigate

**Signal is a separately registered account, not a linked device.** Reversed on
2026-11-04 after noticing a linked device receives every message in every conversation
and can send as him. `SIGNAL_OWN_NUMBER` is the bot (his cell); `SIGNAL_ALLOWED_SENDER`
is him (his Google Voice number). **They must differ.** Discoverability by number is
off, verified externally.

**`MODE=normal` on the container, never `json-rpc`.** In normal mode signal-cli fetches
from Signal's servers on each receive, so messages queue server-side while the
container is down — which is what makes stopping it for the nightly backup safe. Under
`json-rpc` with the default receive mode, inbound messages arriving with no websocket
subscriber are **dropped and never redelivered**. Rejected 2026-11-10.

**`--log-driver none` on the container.** Its access log recorded the bot's number in
`/v1/receive/<number>` about twenty times a minute; 5,919 of 6,033 lines. Verified
before and after by counting matches with the pattern fed from a file descriptor.

**Delivery is proven by receipt, not by a 201.** `last_delivered_at` is stamped only by
a Signal delivery receipt from **device 1** (the phone). Proven with his phone genuinely
offline: the container returned `HTTP 201` and the stamp did not move for 100 seconds.

**The agent's brief is live config.** `checkin.py` re-reads
`docs/claude/task-agent-brief.md` at the start of every run, so editing it changes
behaviour with no deploy and no restart. Pull it onto the server and it is live.

**The agent and the MCP server share `pa/` but not `assistant/`.** `toolset.py` does
`from pa.tools import TOOLS` **wholesale**, so anything added to `pa/tools.py` lands in
the agent's registry on its next restart. That is why the shopping tools live in
`pa/shop.py`, imported only by `server.py`.

**Shopping lists:** own tables, never `pa.task`. Categories and named lists are
orthogonal. No `needed_by` column, because items never surface in the backlog and a
date nothing reads would be trusted. Full reasoning in
`architecture/shopping-lists.md`.

**Deferring moves `due_on` — unless `hard_deadline`.** A soft date is a pace line he
set himself; a hard deadline is external and cannot be moved by deciding to. `someday`
is parked and never appears in overdue, and the agent must never raise those tasks.

**Missed recurrences are discarded, not backfilled.** Decided 2026-11-11 for eleven
missed occurrences: the work is not recoverable, only the rhythm is, and a dozen
overdue rows at importance 5 would damage the brief more than the gap does.

**Pace lines versus hard deadlines.** Subtask dates are soft; the parent carries the
one real date. Used for the seven cords of firewood and the garlic.

## 8. Stylistic conventions

- **Identity belongs in a stable key, never a label.** Resolve projects by slug, not
  display name. Alert dedupe uses `pa.task.metadata->>'backup_alert'`, not an exact
  title with an em dash in it.
- **Task notes carry the reasoning, not just the instruction.** A note says what was
  decided, why, what was rejected and what remains inferred. They are long on purpose —
  a future session with no memory reads them instead of re-deriving.
- **Imported titles record what, never why, and why determines method.** "Fill cracks"
  was rewritten three times before anyone knew whether it meant moisture sealing,
  repointing or rodent exclusion.
- **Thresholds get their arithmetic written down.** The watchdog's 10 hours exists
  because the backup runs at 03:16 and the newest healthy check-in is the 19:48 fire,
  7h28m earlier — so the in-process 6 hours would cry wolf every morning.
- **An alarm channel that has never delivered a message is not an alarm channel; it is
  a hope.** Fire it deliberately and confirm it arrives.
- **Andre's tone preference:** push gently, give the reason a task is worth doing, and
  explain the logic rather than the conclusion. He asks for clarifying questions and
  answers them crisply.

## 9. Known traps

- **`pa.task.due_on`** is the deadline date. `due_at` exists, is a timestamptz, and is
  mostly null — querying it makes a populated backlog look empty.
- **`pa.event`'s timestamp column is `at`**, not `created_at`.
- **`pa_sql` applies printf formatting**, so `%v` or `%d` inside a `like` pattern
  raises *"only '%s', '%b', '%t' are allowed"*. Use `position('x' in title) > 0`.
- **`pa.event.actor` is `andre` for both his edits and the agent's edits on his
  behalf.** It cannot distinguish them; correlate by time against the journal.
- **Importing the `assistant` package has side effects** — its `__init__` calls
  `load_dotenv()` and `set_db()`, which will silently swap a test's database handle.
- **`filter_outbound` extracts task ids** with the pattern in `assistant/task_ids.py`
  (`(171)`, `task 171`, and a line starting `171)`), and a hit on a sensitive project
  redacts the whole line. Render `coffee — 2 lb`, never `coffee (2 lb)`.
- **`context.json` is written by three processes through two implementations**
  (`assistant/context_store.py` and `server.py`'s own `ContextStore`), each with only
  a per-process lock (task 223). Its `.1–.3`
  rotations are written milliseconds apart by the same run, so they hold about one
  second of history and are not a recovery path.
- **A journal read without permission returns an empty result, not a refusal.** An
  empty result that reads like evidence is the worst failure a diagnostic can have.
- **`grep` inside a Claude Code session on the server is not GNU grep.** It is a shell
  function wrapping ugrep with `--ignore-files`, which honours `.gitignore`, so it
  silently skips gitignored files, which here means every `.env` copy. On 2026-11-11 it
  returned zero hits for a secret sitting in a file it was pointed at. **Any search
  whose result is an absence uses `/usr/bin/grep`**, and has a positive control.

## 10. The backlog

The database is the only backlog. Project 18 is Build and area 37 is Operations:

```sql
select t.id, case t.project_id when 18 then 'Build' else 'Ops' end as stream,
       t.importance, t.title
  from pa.task t
 where t.project_id in (18, 37) and t.status not in ('done', 'dropped')
 order by t.importance desc, t.id;
```

`claude/working-agreement.md` has the audit queries.

## 11. What to read, in order

1. **This file.**
2. `architecture/deployment-status.md` — what is running, and the accumulated
   corrections that came back from the machine.
3. `claude/working-agreement.md` — the three streams and the audit.
4. `claude/task-agent-brief.md` — the agent's own instructions. **Live config.**
5. `architecture/monitoring.md` — the monitoring chain, if the work touches alerting.
6. `architecture/shopping-lists.md`, `architecture/backup-status.md`,
   `architecture/encryption-at-rest.md`, `architecture/backlog.md` as the work requires.
7. The repo's `CLAUDE.md`, and the latest few reports in `~/developer/tmp`.
