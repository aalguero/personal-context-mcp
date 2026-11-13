# Deployment status — futile-pa

Last updated: **2026-11-12.** Live, connected, fully triaged, in version control.
What changed since 11-11: see *What changed 2026-11-11 → 12*, below.

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
| MCP server | `/home/dremon/tools/personal-context-mcp/server.py` (git: `github.com/aalguero/personal-context-mcp`, SSH remote, branch `main`, HEAD `0facde4`, agent restarted 2026-11-12 17:14; MCP process still from `7177270` at 16:10, as nothing it runs changed) |
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
| Bind | **`127.0.0.1:8765` only**, since 2026-11-12 14:23 (task 219, Andre's decision). Every client goes through Funnel. Unit in git: `futile-pa/scripts/systemd/personal-context-mcp.service` (`785a99a`). Proved from shadow: tailnet and LAN direct went from 404 to connection refused; Funnel still answers. The Claude Code connector on shadow (`~/.claude.json`, `personal-context`) uses the Funnel URL; from shadow that hostname resolves to the tailnet address and reaches tailscaled on 443, which forwards to 127.0.0.1. **In the access log, port `0` means "arrived through tailscaled"** (`X-Forwarded-For` carries no port); a real port means a direct connection, which now can only be localhost |
| Tools | **34** = 4 context tools + 25 `pa_*` + 5 `pa_shop_*`. The connector picks up new tools on its own after a few minutes; no reconnect needed. **The agent has two registries** (since `0facde4`): the check-in holds 27 (25 `pa_*` + `get_context`/`set_context`), Signal replies hold 32 (those + the five `pa_shop_*`) |
| Migrations | **001–007 applied.** 001_core, 002_reference_data, 003_situation_fit, 004_shopping, 005_overdue_and_defer, 006_deterministic_order (2026-11-11 22:30), 007_hard_deadline_guard (2026-11-12 16:10). `pa.schema_migration` is keyed by **filename**, not version. The unbuilt spec once called "Migration 004", then "007", must now be **008** (task 218), since 007 was taken by the next real migration |
| Agent | fires 07:48 / 11:48 / 15:48 / 19:48 America/New_York. `main()` starts the Signal bridge as a daemon thread, then the scheduler |
| Signal | `signal-cli-rest-api` in Docker, image pinned by digest, `MODE=normal`, `--log-driver none`, bound `127.0.0.1:8080`. **A separately registered account**, not a linked device. Discoverability off |
| Monitoring | 5 external watchdog assertions + 2 dead-man checks + delivery receipts + a heartbeat on every fire. See `architecture/monitoring.md` |
| Test suites | `test_smoke.py` **93** checks (70 until 11-12; 23 added for tasks 221 and 227), `test_shopping.py` **95** (94 until 11-11; the 95th checks the backlog snapshot is deterministic). Both refuse any database but `pa_verify` / `pa_scratch*`. `test_ledger.py` **42**, no database, refuses to run unless the context store is redirected to a temp dir. `test_toolset.py` **30** (since `0facde4`), which agent path holds the shop tools. It is the only DB suite that cleans up after itself, so it's safe to rerun on one restore |
| Agent's own record | `task_agent.asked_recently`, `open_questions`, `last_reply_at`, `unanswered_runs` are **written by code** (`assistant/ledger.py`, task 213). Model writes to them are refused by the agent's `toolset.dispatch` **and** by `server.py`'s `set_context` / `delete_context`. The list is in `assistant/agent_keys.py`; `server.py` loads it by file path and **exits at startup** if it cannot |
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

## What changed 2026-11-11 → 12

The first stretch of work done from Claude Code on shadow rather than from the chat.
Four briefs, four reports, every claim checked from the shadow side afterwards.

1. **Task 196 — the pre-rotation `.env` backup is shredded** (`shred-env-bak-report.md`).
   The old password was proven dead **with a control**: a nonsense password failed the
   same way and the current one succeeded on the same route. A sweep for every live
   secret found each only in its own `.env`, so nothing needed rotating; two `.env~`
   editor copies were also removed.
2. **Task 213 — the check-in's record is written by code** (`463391f`,
   `ledger-213-report.md`). Proven live end to end: Andre's message turned the prose
   `last_reply_at` into a timestamp; the 07:48 run on 11-12 asked about task 149 and the
   ask was recorded at 07:49:54; his 09:39 reply reset the strikes; the 11:48 run cited
   `open_questions` and declined to re-ask 149 under the 24-hour rule. Prose the model
   had written into those keys was moved intact to `task_agent.notes_pre_213`.
3. **The same guard in the MCP server** (`891c57a`, `guard-and-ties-report.md`), because
   the chat's `set_context` is how the prose got there. Proven live 11-12 17:07Z: a write
   of `task_agent.unanswered_runs` through the claude.ai connector was refused, and the
   store's last write stayed at the 11:48 check-in's 16:49:31Z stamp.
4. **Ranking ties broken on id** (`891c57a`, migration 006 plus `pa/repo.py`). Nothing
   broke ties at a `LIMIT`, so which tied rows made `suggest`'s 25 or the brief's
   `top_next` 8 was undefined, and the same data could give two briefs. Measured on
   production: **2 tied pairs out of id order before 006, 0 after.** Probably what made
   `test_shopping.py` fail 4 checks once on 11-11 and then pass; that run was never
   reproduced, so it stays probable. The suite now passes 95/0 five times on fresh
   restores and prints the first differing path when a comparison fails.
5. The task-id pattern shared by the sensitivity filter and the ledger
   (`assistant/task_ids.py`) now also matches the brief's id-numbered `171)` form.
   Before, such a line was invisible to the filter and a sensitive task numbered that
   way would have gone out unredacted.
6. **Tasks 221 and 227: two protections now cover every path** (`7177270`, migration
   007, `backfill-and-deadline-report.md`). Code written and proven on shadow against a
   throwaway Postgres in the session scratchpad, deployed by brief.
   - **221.** Every fixed series now looks back at most `max_backfill_days` (default 1;
     `pa_recurrence_run` was 30). That includes a series with a stamp. Missed
     occurrences are **discarded**, which makes Andre's 11-11 ruling the general rule.
     They're logged as `series N xK`, recorded in `recurrence_runner.last_check.discarded`,
     and `last_spawned_for` moves past them so `recurrence_behind` can't raise a warning
     it can never clear. On a restore of production, a simulated 21-day outage of series 1
     discarded five dates with 0 late rows; the old code created them.
   - **227.** Trigger `task_hard_deadline_guard` refuses an open hard-deadline task that
     is `someday`, or whose `defer_until` is on or after its `due_on`. Lead-time hiding
     before the deadline stays legal (task 159). `defer_until` left `TASK_WRITABLE`, so
     `pa_task_update` can only clear it. Refusals come back as `{"error": ...}`. On real
     task 159 both hiding paths were refused, and both controls went through.
   - Live tool schema proven from shadow 11-12 without a write: `pa_task_update` on a
     non-existent task with `defer_until` set was rejected with `is not of type 'null'`.
     That type exists only in 7177270.
   - **Still pending:** the first live 03:00 runner on the new code. Expect `created 0`
     and no `discarded` line on 11-13 (Friday; nothing is due).
7. **Task 212 phase 2: shopping over Signal, and only there** (`0facde4`, no migration,
   `shop-phase2-report.md`, deployed 2026-11-12 17:14). Signal replies run through
   `quickask.py` under `quick-ask-brief.md`, not the check-in. So 217's suggested fix,
   a line in `task-agent-brief.md`, would have missed. `toolset.QUICKASK_TOOLS` (32) =
   `ALL_TOOLS` (27) + the five `pa_shop_*`. The check-in keeps 27, and its `dispatch()`
   doesn't route shop names. Both briefs say an item is never a task.
   - On a production restore: toolset **30/0** twice, with counts identical before and
     after (the suite cleans up by id floor). Smoke 93, ledger 42, shopping 95 twice.
     The real `import assistant` built 27 and 32.
   - Only the agent restarted (444942 → 524017). The MCP server stayed on 444827,
     because the only change it runs is a docstring. Production `233|0|0` before and
     after. Funnel 404.
   - The pull and the restart were one second apart. §5 ran from a detached worktree,
     so the live-config briefs never ran ahead of the code.
   - `main.py`'s CLI `ask` path also calls `quickask.ask`, so it gets the shop tools too.
     That is intended: it's on-demand, like Signal.
   - **Live Signal test passed, 2026-11-12 17:17–17:18** (Andre). `zqprobe beans`
     landed as shopping_item 1, category `grocery`. No list was created
     (`pa.shopping_list` 0 rows), so the model read "grocery" as a category. The
     item was removed with reason "it was a test", `bought_at` null. `pa.task` stayed
     at 233 with no probe-titled task. His next real adds also landed as items. Read back
     via `pa_sql`. **Task 217's failure mode is closed in practice.**
8. **Log hygiene, hard rule 8** (`33cadcd` + `acc5019`, `log-hygiene-report.md`, agent
   restarted 17:34, 524017 → 549266, MCP untouched). The agent's journal no longer
   holds content:
   - `agent_loop` logs the length of the model's side text, not the text.
   - `checkin` logs `Check-in reply: N chars, <outcome>, task ids [..]` instead of the
     whole reply, which went out **before** `filter_outbound`, four times a day.
   - `main`/`watchdog` log class names only, where they had used `log.exception`.
   - `tests/test_log_hygiene.py` passed 27/0 on the server. Each old version restored
     on shadow failed it.
   - **Baseline left in the journal** (counted, never printed): 6 side-text lines,
     46 full replies (10-28 → 11-12 15:49), 17 tracebacks (14 from `Check-in run
     failed`, 3 unattributed, all 11-04 → 11-09).
   - The journal is persistent and has **no retention configured**, so these lines stay
     until the size cap rotates them. The **user** journal is 177.2M. The 642.6M first
     reported was probably every journal (inferred).
   - **Live-proven** by four fires, 11-12 19:48 → 11-13 15:48
     (`notify-and-purge-report.md`): one `Check-in reply: N chars` line each, no new
     full-reply or traceback lines.
   - **Found, then fixed in item 9:** `notify.send()` appended **every** outbound message,
     unfiltered, to `futile-pa/assistant/pending_notifications.log`, before it checks
     whether Signal is configured. The file is mode 664 in a 775 directory, 28 entries
     since 10-28, gitignored. That is the same content, in a file every local user
     can read. A leftover from before Signal worked, when the file was the only output.
   - Also found: `main()` calls `run_scheduler()` with no handler, so an exception that
     escapes the loop prints a full traceback via systemd's stderr capture. It handles
     no task content today, inferred.
9. **`notify` keeps only undelivered messages** (`56d4b28`, `notify-and-purge-report.md`,
   agent restarted 2026-11-12 17:55:12, PID 575653, MCP untouched). Messages are
   written only when Signal is unconfigured or the send failed, after
   `filter_outbound`, to a mode-600 file. A failing filter writes "withheld", never
   the raw text.
   - Log hygiene passed 43/0 on the server.
   - **Proven live:** `pending_notifications.log` stayed absent through four delivered
     fires, including 19:48 on 11-12, which sent real questions.
   - **The old file is gone.** Two server sessions ran this brief. The first did the
     pull, restart and removal, then wrote no report, so **whether it was `shred` or
     `rm` is unrecorded**.
   - No backup bundle covers `futile-pa/assistant/`, read from `backup.sh`.
   - **The journal purge is NOT done.** The server CLI's permission classifier refused
     the rotate+vacuum as "Logging/Audit Tampering". Separately, `dremon` has no write on
     `/var/log/journal/<id>/` (`root:systemd-journal`, setgid, ACL r-x), so it needs
     root either way.
   - **Andre's commands:** `journalctl --user --rotate`, then
     `sudo find /var/log/journal/ -name 'user-1000@*.journal' -print -delete`, and the
     same for `'user-1000@*.journal~'`. The brief's pattern missed the four `~` archives.
   - The purge removes every `dremon` user unit's journal history, including desktop
     units. `external_watchdog.py` never reads the journal, so monitoring is unaffected.
   - The system journal holds 0 full-reply lines. rsyslog is inactive and there's no
     `/var/log/syslog`.
   - **Found:** `server.log` beside `server.py`. 63,748 bytes, **mode 664**, mtime
     2026-08-15, gitignored. Nothing in the repo writes it: `server.py` logs to stderr.
     A pre-systemd leftover, probably MCP traffic from August. **Andre reviewed it
     2026-11-13, found nothing sensitive, and removed it with `rm`.**

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

**Twenty-one now, and every one came from data rather than from more reasoning.** The
first thirteen are below; 14–21 arrived in November and are the sharpest yet.

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
    does not do what its name says (task 221). **Fixed 2026-11-12 by `7177270`.** The
    bound now applies to every fixed series, and the skipped dates are discarded and
    recorded.
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
20. **A migration check that could not fail.** `GUARD_AND_TIES_BRIEF` verified 006 with
    `position('v.id' in pg_get_functiondef(...)) > 0`, but `v.id` was already in 003's
    select list, so the check printed `t` against the old function too. The executing
    Claude caught it. The tie probe (2 → 0) was the real evidence. Same shape as
    correction 17: *a verification that measures something other than what it claims.*
    Check for the change itself, here `nulls last,` followed by `v.id`, or better, for
    its effect.
21. **A stubbed test that crashed looked like a pass.** While proving the task 213
    wiring on shadow, a mutant run was checked with `grep FAIL`. The mutant crashed
    before any check (the copied tree lacked `docs/`), printed no `FAIL`, and read as
    green. Caught by comparing exit codes. An empty grep is not a passing test.
22. **A grep count expected 1 and got 3** (`BACKFILL_AND_DEADLINE_BRIEF`). The trigger
    name `task_hard_deadline_guard` is a substring of its function's name
    `tg_task_hard_deadline_guard`. Benign, but it's the same shape as correction 20:
    grep for the thing itself (`create trigger task_hard_deadline_guard`), not for a
    string that other things also contain.
23. **Live config goes live at the pull, not the restart.** The same brief said running
    code stays old until the restart. True for Python, but `task-agent-brief.md` is
    re-read on every check-in, so its new rule was live from the pull (16:08), two
    minutes before the restart. No fire ran in between. A brief that changes the agent
    brief and code together must treat the pull as the deploy for the former.

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

### The pattern, now seen twenty-one times

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
snapshot below is 2026-11-12; `claude/claude-code-handoff.md` §10 has the full table.
**Closed 11-11 → 12:** 196, 213.

**Highest value:**

- **228** — the event log records the agent's edits as `actor=andre`. **229** —
  `repo._resolve`'s exact-name branch silently picks one of several same-named rows
  (latent: no duplicate names today). **230** — `task_list`'s non-default orderings
  have no id tie-break.
- **231** — two other services on futileassistant answer on the LAN: an
  unauthenticated uvicorn app on `:8000` (probably the album catalog) and Apache on
  `:80`. Same question 219 settled for the MCP server; exposure is Andre's decision.
- **The Google Tasks importer bypasses `repo.py`** (found by the 007 deployment, not yet
  a task). On re-import it overwrites `status` and `due_on` on every task it imported
  before, resetting Andre's own status changes to `inbox`. Since 007, some re-imports
  would also die on an uncaught 22023. It is run by hand only; last run 2026-09-23.
- **217** — until shopping phase 2, asking the bot to add to a list makes it reach for
  `pa_task_add`.

**Queued and specified:**

- **212** — shopping phases 2 (Signal), 3 (`town_trip` and staples), 4 (voice).
- **207** — an alarm about Signal that does not travel over Signal.
- **206, 223** — the Signal history grows without bound; `context.json` has four writer
  processes and a per-process lock.
- **218** — the unbuilt "Migration 004" spec must be renumbered to **007**: 006 was
  taken by `006_deterministic_order` on 11-11. The task's title still says 005.
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
