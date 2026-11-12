# Deployment status — futile-pa

Last updated: **2026-11-11.** Live, connected, fully triaged, in version control.

**The backup project is complete** (verified dumps, encryption the server cannot
reverse, a proven restore, an off-machine copy on the Pi, a compliance-locked off-site
copy in B2, the age private key printed and filed with the estate documents).

**The monitoring chain is complete as of 2026-11-11** and is the biggest change since
October. It has its own document: `architecture/monitoring.md`. Read that before
touching anything to do with alerting.

**New session? Start with `claude/claude-code-handoff.md`.** It is self-contained and
covers the machines, GitHub, the brief loop, the conventions and the live backlog.

Design record: `architecture/phase-1-task-backend.md`. Open work:
`architecture/backlog.md`. Backups: `architecture/backup-status.md`. Security:
`architecture/encryption-at-rest.md`. Voice: `architecture/voice-assistant.md`.
Shopping: `architecture/shopping-lists.md`. Process: `claude/working-agreement.md`.

## HOW WE WORK — the brief loop

**Andre's standing instruction, 2026-10-07: use `/home/andre/developer/tmp` on
shadow for both writing briefs and reading results.**

```
write  ──▶  /home/andre/developer/tmp/XXX_BRIEF.md     (on shadow)
                      │
            Andre hands it to the Claude CLI on the server
                      │
read   ◀──  /home/andre/developer/tmp/xxx-report.md
```

Do **not** deliver a brief into the chat and assume it exists on disk. Write it with
`device_commit_files`, then verify by reading the content back — a `{"written": [...]}`
result has been wrong once.

**The server cannot reach shadow**, so the executing Claude writes its report to
`/home/dremon/developer/tmp/` and Andre carries it across. Name that path as the
primary destination in every brief.

**Read the exchange folder before writing a brief.** Brief 13's first draft asserted
things two intervening briefs had already fixed, because that session had no record of
them. A context window is not the project's memory; the folder and these docs are.

**Run checks on the machine that has the thing.** Diagnostic commands have been run on
shadow for a condition on futileassistant more than three times.

Naming: briefs `UPPERCASE_BRIEF.md`, reports `lowercase-report.md`. A `server-brief`
skill captures the procedure. The full brief and report structure is in
`claude/claude-code-handoff.md` §3.

**Why briefs at all:** a chat session has no route to the server. It reaches shadow
through the device bridge, but the MCP server, Postgres and futile-pa live on a machine
only Andre can reach. `pa_sql` is a read-only way to check the database from here, which
is how a restore was independently verified.

## Current state

| | |
|---|---|
| MCP server | `/home/dremon/tools/personal-context-mcp/server.py` (git: `github.com/aalguero/personal-context-mcp`, SSH remote, branch `main`, HEAD `c4a7169`) |
| futile-pa | `/home/dremon/tools/personal-context-mcp/futile-pa` — inside the repo. Old tree at `/home/dremon/futile-pa.pre-git` |
| Scripts | `futile-pa/scripts/` — `backup.sh`, `external_watchdog.py`, `recurrence_runner.py`, `deadman_ping.py`, `backup_alerts.py`, `prune_backups.py`, `migrate.py`. **Note the `futile-pa/` prefix** |
| Interpreter | `/home/dremon/tools/personal-context-mcp/.venv-new/bin/python` — Python 3.13.5. **Not** `futile-pa/.venv-new` |
| Database | `futile_assistant`, role `musicbrainz`, PostgreSQL 18.6, schema `pa` |
| Config | `.env` beside `server.py`, mode 600. `PGPASSWORD`, `PA_AUTH_TOKEN`, age recipient, B2 credentials, `SIGNAL_*`, `DEADMAN_PING_URL`, `BACKUP_PING_URL` |
| Services | `personal-context-mcp.service` (MCP), `futile-assistant.service` (the agent). Both user units; **lingering enabled for `dremon`** — verified after the 11-10 power cut |
| Timers | `futile-pa-backup.timer` 03:15+jitter · `recurrence-runner.timer` 03:00+jitter · `deadman-ping.timer` hourly |
| Transport | streamable HTTP at `/mcp` only |
| Public URL | `https://futileassistant.cormorant-barb.ts.net/mcp` via Tailscale Funnel (`/ → 127.0.0.1:8765`) |
| Auth | shared secret, ASGI middleware, all paths, **404 on failure**, fail-closed at startup |
| Bind | **`0.0.0.0:8765`, not `127.0.0.1`** — long-standing, LAN-reachable behind the token. Decision open: task 219 |
| Tools | **34** = 4 context tools + 25 `pa_*` + 5 `pa_shop_*`. The connector picks up new tools on its own after a few minutes; no reconnect needed |
| Migrations | **001–005 applied.** 001_core, 002_reference_data, 003_situation_fit, 004_shopping, 005_overdue_and_defer. `pa.schema_migration` is keyed by **filename**, not version |
| Agent | fires 07:48 / 11:48 / 15:48 / 19:48 America/New_York. `main()` starts the Signal bridge as a daemon thread, then the scheduler |
| Signal | `signal-cli-rest-api` in Docker, image pinned by digest, `MODE=normal`, `--log-driver none`, bound `127.0.0.1:8080`. **A separately registered account**, not a linked device. Discoverability off |
| Monitoring | 5 external watchdog assertions + 2 dead-man checks + delivery receipts + a heartbeat on every fire. See `architecture/monitoring.md` |
| Test suites | `test_smoke.py` **70** checks, `test_shopping.py` **94**. Both refuse any database but `pa_verify` / `pa_scratch*` |
| Disk encryption | **Server: NONE** (task 112). **shadow: YES** — and shadow holds the age private key, which is what makes the asymmetry hold. See `encryption-at-rest.md` |
| Tailnet | futileassistant `100.108.86.16` / LAN `192.168.86.35`; shadow (DNS `darkslip`) `100.102.50.58` / `192.168.86.40`; vocalassistant `100.75.132.20` / `192.168.86.251`; homeassistant `100.96.213.73`; pixelatedandroid `100.122.109.81` |

**Network shape after the room move (2026-10-20):** the server and the Pi are on the
same `192.168.86.0/24` subnet, so the nightly transfer takes a direct LAN path rather
than relaying through DERP.

**Home Assistant is reachable securely from the phone** at
`https://homeassistant.cormorant-barb.ts.net` via Tailscale Serve, tailnet-only (not
Funnel). Details in `voice-assistant.md`.

**Load-bearing detail:** `/.well-known/*` must keep returning 404. Anything
OAuth-shaped there makes claude.ai attempt dynamic client registration, which fails and
already cost a day.

## What changed in the week of 2026-11-06

Fourteen tasks closed, each with proof rather than a clean start. In order of
consequence:

1. **The four-day check-in outage** (11-06 → 11-09). Cause: Anthropic credit
   exhausted, eleven identical non-retryable 400s. No bug anywhere. Auto-reload is now
   on. Full account in `monitoring.md`.
2. **Task 197** — a check-in question now enters the Signal conversation history, so a
   reply is interpreted against the question that was asked. The memory reset became
   idle-based. End-to-end proof on 11-10: a scheduled question about the turkey,
   answered 1.9 hours later, wrote to that task id and no other.
3. **Tasks 182, 124, 178** — the external watchdog, proven with the agent stopped.
4. **Task 205** — delivery is proven by Signal receipts, not by a 201. Verified with
   his phone genuinely offline.
5. **Task 211** — the dead man's switch, after a power cut the machine did not return
   from. Alarm fired and recovered for real.
6. **Task 204** — the bot's number removed from the container log, 5,919 matching lines
   to zero, measured.
7. **Tasks 161 and 188** — the recurrence engine had no clock since it was built. It
   now has a nightly timer **and checks its own work.**
8. **Tasks 194, 184, 195** — "overdue" was defined in ten places. It is now one view,
   and deferring actually moves the date. The overdue list went from 3 items to 1; two
   of the three were parked `someday` tasks.
9. **Shopping lists phase 1** — new tables, 5 tools, and a byte-for-byte proof that the
   task backlog is unaffected.

## Data

**226 tasks, 127 open.** 37 projects, 10 recurrences, 18 contexts, 9 situations,
9 people, 9 goals.

Structure, per Andre's framing: **Brook House** is the area covering house and land
(renamed from "House & Land"; slug stays `house-land`). Beneath it: Farmhouse Projects,
Native Plant Garden, Land Maintenance, Vehicles. Added during triage: Backup Power,
Estate Documents and Authorizations, Seven Fires Cooking Setup, Basement and Moisture.
Added 2026-10-23 for the three streams: **Personal Assistant Operations** (area 37) and
**Personal Assistant Build** (project 18).

Full triage record in `architecture/backlog.md` section G.

## Known-benign log noise — recorded so nobody investigates it twice

**`tailscaled: Drop: TCP{...} > [...]:51865 ... no rules matched` on futileassistant.
Baseline: about 13 a day. This is normal.**

Investigated 2026-10-20 and closed with no action. The sources carry `tag:ingress` and
the capability `https://tailscale.com/cap/ingress` — they are **Tailscale's Funnel
ingress relays**, not nodes in this tailnet (`tailscale whois` says `peer not found`).
The destination is `tailscaled`'s own live Funnel listener. So this is deny-by-default
refusing connections from relays that are not carrying the active session, while the one
that *is* gets through.

**The number is the point.** 13/day is the baseline. A jump to hundreds would mean
something; without the baseline written down, the next person with journal access spends
an hour rediscovering this.

Also harmless: `tailscaled` advertises the Docker bridge (`172.17.0.1:41641`) as a local
endpoint. And the Signal bridge logs `Signal receive failing (ConnectionError)` followed
by `recovered` within seconds during the nightly backup — that is `backup.sh` stopping
the container to copy the account directory cold.

## Migration 003 — found only by loading real data

1. **`land_day` was providing `@home`** ("indoors"), so a day on the five acres
   suggested "Snuff boxes" above "Wood pile".
2. **A generality penalty in `pa.suggest_tasks`.** `@phone` tasks were satisfiable by
   six of nine situations and crowded every list.
   `fit_bonus = (total situations − situations that can satisfy this task) × 4`, capped
   at 32. Reasoning is opportunity, not preference: a task one situation can satisfy has
   one chance to get done. A bonus rather than a sort key, so an overdue hard deadline
   still wins.

## The next real gap — situation vocabulary is too coarse

`dog_walk`, `evening_low`, `waiting_room` and `morning_coffee` all reduce to `phone` +
`lowenergy`/`online`, so the engine cannot tell a dog walk from a waiting room.

| | dog walk | waiting room |
|---|---|---|
| Can talk at length, out loud | yes | **no** |
| Overheard by strangers | no | **yes** |
| Hands free | yes | no |
| Can read or tap a screen | no | yes |

A private call about his father's finances is ideal on a dog walk and wrong in a waiting
room. Likely fix: split `@phone` into `@call`, `@private`, `@handsfree`, `@screen` and
retag.

**Risk if left:** Andre tunes out the situation feature for exactly the cases where it
was meant to be most useful.

## Corrections that came back from the machine

**Seventeen now, and every one came from data rather than from more reasoning.** The
first thirteen are below; 14–17 arrived in November and are the sharpest yet.

1. Broken venv was a typo'd path baked into pip's console scripts
   (`personal-cont**a**ct-mcp`), not a Python upgrade.
2. Funnel was running correctly with `--bg`, not dead.
3. The `/messages/` double-send mechanism was real; the claimed connection corruption
   did not reproduce.
4. The connector failure was a transport mismatch, not OAuth.
5. A path-rewrite fix Andre correctly called hacky; replaced with a structural one.
6. `env -u PA_DATABASE_URL` does not unset it — `load_dotenv()` defaults to
   `override=False` and reinstates it.
7. The import brief gave the venv as `/home/dremon/futile-pa/.venv-new/`. It is at
   `/home/dremon/tools/personal-context-mcp/.venv-new/`.
8. **`load_dotenv()` ran *after* `FUTILE_PA_PATH` was read** — see below.
9. **The darkslip failure was a tailnet SSH policy refusal for the login user, not a
   dead sshd.** And a note claimed a change was "covered in `BACKUP_FIX_BRIEF.md`
   section 5" when it was never in that brief. A note that points at a document is a
   claim; read the document.
10. **System packages would not shadow the hand-installed binaries.** `~/.local/bin` is
    first in PATH.
11. **Tailscale SSH and sshd do not necessarily present different host keys.** On the Pi
    both present the same ed25519 key, so a LAN-IP connection fails on *authentication*,
    not on the host key.
12. **A run reporting `result: ok` had been pruning only one remote file per run since
    the code was written.** `ssh` inside a `while read` loop inherits the loop's stdin;
    `ssh -n` fixes it.
13. **The dropped tailnet packets were explained as "stale connections settling after
    the room move" — reasoned from timing, while the `whois` that disproved it was
    already running.** The explanation was wrong in substance and wrong in method.
14. **`checkin-once` does not run in-process with the Signal bridge.** `main.py` starts
    the bridge only in the no-argument `main()`, so `checkin-once` is a separate process
    with a throwaway history. A brief warned about this trap in §0 and then prescribed
    it in §4 as the test — which would have made a working fix look broken.
15. **Registering the shopping tools in `pa/tools.py` would have handed them to the
    agent.** `assistant/toolset.py` does `from pa.tools import TOOLS` wholesale, so the
    brief's own "do not touch `assistant/`" rule would have been broken without editing
    a single file there. Hence `pa/shop.py`.
16. **`max_backfill_days` bounds only a series that has never spawned.** One with a
    `last_spawned_for` resumes from that stamp however long the runner was down —
    proven by a run with `max_backfill_days=0` creating three old rows. The parameter
    does not do what its name says (task 221).
17. **`pa_recurrence_preview` cannot verify what the engine will do.** It ignores
    `last_spawned_for` and anchors `after_completion` differently from `run()`. A brief
    prescribed it as the verification; it is a *verification that measures something
    other than what it claims* (task 222).
18. **`grep` in a Claude Code session on the server is ugrep with `--ignore-files`.**
    It honours `.gitignore`, so a sweep for the compromised password (task 196,
    2026-11-11) returned no hits at all, not even the file it had to find. The brief's
    positive control caught it; `/usr/bin/grep` found the file. Without the control the
    result would have read as "no copies anywhere".
19. **The same brief said `.gitignore` did not cover `.env.bak-*`.** It does, on line 14.
    The claim came from reading the file with `head`, which showed ten of thirty lines,
    so a conclusion was drawn from a truncated listing. Same shape as correction 13 and
    the 11-04 `grep -oE '^[A-Z_]+='` miss: *a listing that silently stops short is not
    evidence of absence.*

### Correction 8 in full, because the shape of it matters

`server.py` read the variable three lines before loading `.env`:

```python
FUTILE_PA_PATH = os.environ.get("FUTILE_PA_PATH", "/home/dremon/futile-pa")
sys.path.insert(0, FUTILE_PA_PATH)
from pa.tools import ...            # line 40
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")   # line 43
```

So **the `.env` value was never read, on any start, ever.** The change that "replaced
the hardcoded path with an environment variable" didn't — it moved the hardcoded path
into a default that was unconditionally taken. It looked correct because the default
*was* the real path.

The next brief then moved futile-pa into the repo and pointed `.env` at the new
location. That change was **also inert**, for the same reason, and appeared verified
only because the old directory still existed at the moment of the restart. It was
renamed to `.pre-git` afterwards — arming a landmine the next restart would trigger,
which happened a week later during an unrelated password rotation.

### The pattern, now seen seventeen times

| | The change | Why it looked fine | What actually proved it |
|---|---|---|---|
| 1 | `FUTILE_PA_PATH` in `.env` | the hardcoded default happened to be correct | pointing it at nonsense |
| 2 | the dedicated SSH backup key | rsync failures read as key problems | `ssh -vv` showing key exchange *succeed* |
| 3 | remote retention | the run reported `result: ok` | listing the remote directory |
| 4 | the unattended timer run | two manual runs succeeded | reading the journal for 03:25 |
| 5 | the B2 upload | the status said `skipped (not configured)` | giving it credentials |
| 6 | the off-site alarm | `result: ok` while B2 could be dead | task 116, now closed |
| 7 | the §4 task-id requirement | it was written, reviewed and believed live | `ls -l` and `grep -c` on the server's copy — it was an **uncommitted edit in a working tree** |
| 8 | the recurrence engine | ten series defined, all active | `select source … group by source` returning **no rows** for `recurrence` |
| 9 | a thread-safety lock | 8 threads × 500 appends, nothing lost | removing the lock — **the test still passed**, because the GIL makes `list.append` atomic |
| 10 | a recurrence self-check | it caught a seeded fault | seeding a *dormant* series — the first version never expanded future-dated rules |

***A configuration or code change is not verified by the system still working. It is
verified by proving it controls something.*** This belongs in every future brief, and it
is why briefs ask for listings, hashes and verbatim output rather than exit codes.

**Corollary, 2026-10-16:** a protective feature being *enabled* is not the protection
*applying*. B2 Object Lock was on with no default retention, so every object would have
arrived unlocked while the status file reported success. Check the object, not the
setting.

**Corollary, 2026-10-19:** a monitoring decision has a shelf life. Declining to alarm on
`off_site_copies == 0` was right when B2 was unconfigured and zero was normal. B2 works
now, so zero is an anomaly.

**Corollary, 2026-11-09:** a watchdog asserts on outcomes, never on health. See
`monitoring.md`.

**Corollary, 2026-11-11:** a test that has never failed is not a test. Break the thing
on purpose and watch the check go red before trusting it.

### A fourth kind: the prerequisite invisible until it fires

Host keys. The first `BatchMode=yes` probe to the Pi failed with `Host key verification
failed.` — nothing to do with the destination change, and it would have broken the 03:15
run with no symptom but `off_machine_copies: 0`. **Host-key provisioning is a named
prerequisite for any new backup destination**, and the right way to establish a key is
from the control plane (`tailscale status --json`, `Peer[].sshHostKeys`) rather than by
accepting whatever answered.

### Diagnostics that lie

A journal read without permission returns an **empty result**, not a refusal. On 10-17
that made a hypothesis look disproven when it was merely unreadable. Fixed by task 122;
an empty result that reads like evidence is the worst failure mode a diagnostic tool has.

**Method that works here: test before theorising.** Two curls settled the transport
question after a day of speculation. Loading 78 real tasks caught two ranking bugs. A
3-line synthetic loop settled the `ssh -n` question in one command.

A corollary from triage: **imported titles record what, never why, and why determines
method.** "Fill cracks" was rewritten three times — moisture sealing, structural
repointing, rodent exclusion — because the purpose lived only in Andre's head.

## Identity belongs in a stable key, never in a label

- A test asserting the display name `"House & Land / Native Plant Garden"` broke when
  Andre renamed the area. Fixed to resolve by **slug**.
- Two more hardcoded display names in the suite, fixed the same way.
- The backup alert dedupe and auto-close matched tasks by **exact title**, including an
  em dash. Moved to `pa.task.metadata->>'backup_alert'`. The watchdogs built in November
  follow the same pattern with `watchdog_alert` and `external_watchdog_alert`.

## Incident, 2026-10-10 — the test suite wrote into production

Recorded in full in `backup-status.md`. An agent aimed `test_smoke.py` (an integration
test that writes) at the live database; ~26 fixture tasks landed and a review date was
reset. Recovered from a two-minute-old dump; Andre independently confirmed 110 rows, max
id 111. **Structural fix in place** (`5568ff1`): the suite refuses any database not on a
scratch allowlist, no override. The lesson is not "be more careful"; a suite that writes
to whatever it is pointed at will eventually be pointed at production.

## Open items

Live source of truth is the database — project 18 (Build) and area 37 (Operations). The
snapshot below is 2026-11-11; `claude/claude-code-handoff.md` §10 has the full table.

**Highest value:**

- **213** — the agent asks questions without recording that it asked them, so the
  24-hour no-repeat rule and the unanswered-run escalation both run on a record nothing
  writes.
- **196** — delete `.env.bak-20261104-110206`, which holds the compromised musicbrainz
  password. Five minutes.
- **219** — decide the `0.0.0.0:8765` bind.
- **221, 227** — two places where a protection does not apply as widely as its name
  suggests: `max_backfill_days`, and `hard_deadline` being enforced in one path of
  three.
- **217** — until shopping phase 2, asking the bot to add to a list makes it reach for
  `pa_task_add`.

**Queued and specified:**

- **212** — shopping phases 2 (Signal), 3 (`town_trip` and staples), 4 (voice).
- **207** — an alarm about Signal that does not travel over Signal.
- **206, 223** — the Signal history grows without bound; `context.json` has four writer
  processes and a per-process lock.
- **218** — the unbuilt "Migration 004" spec must be renumbered to **006**.
- **222, 226** — recurrence preview lies; two places still compute overdue by hand.
- **162, 169** — data cleanup and retagging for season and low energy.

**Still open from October:**

- **Split `@phone`** into `@call` / `@private` / `@handsfree` / `@screen` and retag.
- **Nine tool-surface gaps** in backlog section J — chiefly that `pa_task_update`
  replaces `notes` while `pa_task_complete` appends, and that `pa_task_update` has no
  `depends_on`, `parent_task_id` or `kind` (task 183).
- **Decide about no disk encryption on the server** (task 112).
- **114** PiTower case code into git · **123** narrow the B2 key · **125** three
  backup-alert refinements · **198** Signal hardening leftovers.
- **Rosco's medication schedule** — still to be rebuilt as recurrences.
- The `vocalassistant` Pi gets switched off. It is the second off-machine backup leg and
  the intended host for voice capture, which is why voice is phase 4 rather than phase 1.
