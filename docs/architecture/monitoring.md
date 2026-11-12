# Monitoring — the chain as built, November 2026

Written 2026-11-11. Everything here was built between 6 and 11 November, triggered by a
four-day outage in which the check-in agent failed every single run while every health
signal reported green.

**The governing rule, and it is the whole document in one line:**

> **A watchdog asserts on outcomes, never on health.**

---

## The incident that produced all of this

Between **2026-11-06 11:48** and **2026-11-09 18:57**, `futile-assistant.service` failed
eleven consecutive check-ins. Every one died on its first Anthropic API call with a
non-retryable `400 — credit balance is too low`. For three days:

| Asked | Answer |
|---|---|
| `systemctl --user status` | `active (running)` |
| `Result` | `success` |
| `NRestarts` | `0` |
| the journal | logging normally, scheduler firing on time |
| the in-process watchdog | ran at all 12 fires, raised task 199 on the first |
| Andre | noticed on the fourth day, by looking |

**Nothing was broken and nothing worked.** Two separate reasons it stayed quiet, and
they needed different fixes:

1. **The alarm had no route out.** Task 199 was raised correctly in Postgres. The only
   thing that reads Andre's tasks and talks to him is the check-in agent — the dead
   component. The alarm rang inside the burning building.
2. **The failure was invisible to anything watching health.** Four health signals were
   green throughout.

Then on **2026-11-10** the house lost power, futileassistant did not power itself back
on, and nothing told him — because every watchdog built so far had to be *alive* to
complain. That produced the dead man's switch.

## What exists now

### Layer 1 — in-process (`assistant/watchdog.py`)

Runs from the agent's own scheduler. Raises Postgres task rows via
`metadata->>'watchdog_alert'`, deduped while open and auto-closed when the condition
clears. `BACKUP_STALE_AFTER_HOURS = 30`, `CHECKIN_STALE_AFTER_HOURS = 6`, alert window
08:30–20:30 ET.

**Keep it. It works.** It simply cannot report its own death, and its alerts go to a
channel only it reads.

### Layer 2 — external (`scripts/external_watchdog.py`)

Section 12 of `backup.sh`, so it runs nightly at ~03:16 in **a different process, a
different unit and a different timer** from the agent it watches. Five assertions:

| Marker | Level | Fires when | Proves |
|---|---|---|---|
| `checkin_stale` | ALERT | `task_agent_watchdog.last_run_at` > 10h, or unreadable | a run completed |
| `delivery_stale` | ALERT | `last_delivered_at` > 10h, missing or unparseable | a message reached the phone |
| `recurrence_stale` | ALERT | `recurrence_runner.last_run_at` > 26h | the runner fired |
| `recurrence_behind` | WARN | the runner's self-check found an unspawned past occurrence, or an unparseable rule | **the recurrences are current** |
| `off_machine_reduced` | WARN | `result=ok` but `off_machine_copies < 2` | both off-machine legs held |

**It reports by calling the Signal container directly** on `127.0.0.1:8080`. The
container is its own Docker process and stayed healthy throughout the November outage,
so this path depends on neither the agent, nor the Anthropic API, nor anything reading
Postgres. It raises a task **and** sends a message: the task is the record, the message
is the alarm.

**Repeat, don't dedupe, for the message.** The in-process watchdog dedupes correctly for
a table row, and that was part of why the outage was quiet — after the first alert, nine
further checks did nothing visible. Nightly repetition is at most one message a day and
cannot be missed for three days.

**Proven with the agent stopped.** With `futile-assistant.service` dead it read the real
timestamp and delivered a message. If it could not do that, it would not be external.

### Layer 3 — the dead man's switch (external service)

Two checks on healthchecks.io, which complains when *nothing* happens:

| Check | Pinged by | Window | Catches |
|---|---|---|---|
| `futileassistant-alive` | `deadman-ping.timer`, hourly | 1h + 75m grace | machine off, dead, asleep, no network, user timers not running |
| `futile-pa-backup` | `backup.sh` §13, only when `RESULT=ok` | ~26h | the backup timer never firing |

**The hourly ping is deliberately unconditional.** It asserts only that the machine is
powered on, its user session is running its timers, and it has outbound network — **not
that the agent is healthy**, which is covered twice already. That scope is written into
the script's docstring so a green check is never over-read.

**The ping is not tied to the check-in**, and the reason is arithmetic: fires are at
07:48/11:48/15:48/19:48, so the longest healthy gap is the **overnight twelve hours**. A
check-in-tied ping would need a window wider than that, and a machine dying at 21:00
would go unnoticed until morning. The cadence of the watched thing must not set the
resolution of the watcher.

**Self-hosting the monitor would be wrong**, and not for budget reasons: the obvious host
is the Pi, which is in the same house on the same power and was found switched off on
11-08.

**Both directions proven, 2026-11-10.** The timer was stopped and the down email
arrived; it was restarted and the recovery email arrived — which also answered a question
the documentation did not.

### Layer 4 — delivery receipts

Signal sends a delivery receipt automatically when a recipient's device receives a
message, and unlike read receipts the recipient cannot switch them off. The bridge was
already polling and discarding them.

Now, a delivery receipt from **device 1** (the primary device, always the phone, even
after a re-registration) stamps `task_agent_watchdog.last_delivered_at = now`.

- **No deadline timer, deliberately.** A phone off for six hours is normal, and a timer
  would invent false alarms for it. A late receipt stamps its arrival time, which is the
  truthful answer to "when did a message last reach him". Staleness is judged nightly.
- **No matching to a specific send.** The question is "is the channel alive", and a bare
  receipt answers exactly that. It does **not** prove a particular check-in arrived.
- **A separate process cannot stamp it.** `checkin-once` and `signal-send` start no
  bridge, so a manual run can never make a dead channel look alive.
- **Restricted to device 1** because a linked Signal Desktop left running would
  acknowledge messages while the phone is dead.

### Layer 5 — the heartbeat

Every fire sends something: the questions if there are any, otherwise one line composed
**in code with no model call**, because a heartbeat that dies when the API runs out of
credit is not a heartbeat.

```
✓ futile-pa 15:48 — nothing needs you right now. 128 open, 1 due today, 1 overdue; backup ok. No reply needed.
```

It carries **falsifiable numbers**. "System operating normally" alone is
indistinguishable from a stub that always says that.

**Its real job is to feed the machine, not to make Andre notice silence.** Four messages
a day becomes wallpaper within a week, and a human failing to notice absent wallpaper is
not a monitoring system. What it actually does is refresh `last_delivered_at` four times
a day, every day, so staleness becomes unambiguous.

A failed run sends a one-line failure notice built in code, then re-raises so
`last_run_at` does not advance.

## Honest delivery semantics

`notify.send()` used to swallow a failed send, so `run_once()` logged "produced output"
and the watchdog stamped a successful run **when nothing had left the machine**. Fixed:
it returns success, retries 3 times at 5s and 20s (enough to ride out a container
restart), and does **not** retry a read timeout, because that is ambiguous and a
duplicate question is worse than one recorded as undelivered.

A failed send sets `undelivered_since`, and the next run's prompt is told that those
questions were never seen. Since task 213 (2026-11-11) there is nothing to correct:
`assistant/ledger.py` records `asked_recently` and `open_questions` only after a send
succeeds, from the ids in the text as sent. Before that, the model was supposed to
record them during the run, before the send. On 11-10 it didn't record them at all.

## The diagnostic table

The two agent assertions fail differently, and the combination says why:

| `last_run_at` | `last_delivered_at` | Means |
|---|---|---|
| fresh | fresh | healthy |
| stale | stale | the agent is not running — the November outage |
| fresh | **stale** | **the agent is running and Andre is hearing nothing** |
| stale | fresh | the runs are failing but the failure notices are getting through |

The fourth cell was briefed as "should be impossible, report it as a bug". The machine
disagreed correctly: a failing check-in now sends a failure notice built in code, and
that notice gets a receipt, so the cell is reachable by design and diagnostic.

## What is still open

- **An alarm about Signal that does not travel over Signal** (task 207). If the machine
  is up and the Signal channel is dead, the hourly ping stays green by design, and
  `delivery_stale` alerts *over Signal*. The shape agreed: a third healthchecks check
  pinged only when a delivery receipt advances `last_delivered_at`. Window must exceed
  the overnight twelve hours, so detection is about half a day.
- **An agent alive but wedged.** Process runs, timers fire, no check-in happens. Covered
  only by the nightly watchdog.
- **`recurrence_behind` shares `_occurrences()`** with the thing it checks, so a bug
  inside that expansion is invisible to it.
- **The backup check's own 26-hour alarm has never fired.** Only the hourly check's path
  is proven end to end.
- **healthchecks.io itself**, and email delivery, are a single external observer on a
  single channel.

## Non-negotiables for anyone touching this

1. **Never put the ping URLs, the phone numbers or a DSN on a command line.** argv is in
   every process listing. Read `.env` in-process.
2. **`backup.sh` is the most load-bearing script on the machine.** Additions go at the
   end, after `RESULT` is final, guarded with `|| true` so `set -euo pipefail` cannot see
   a failure, and bounded by `timeout`. The backup's own result must be identical whether
   the addition succeeds, fails, hangs or is deleted — and a forced run must demonstrate
   it.
2. **Do not copy the in-process 6-hour threshold into a nightly check.** At 03:16 the
   newest healthy run is 7h28m old, so 6 hours cries wolf every morning. 10 hours leaves
   2h32m of margin and still catches one missed evening fire.
3. **Fire every new alarm deliberately before trusting it.**
4. **Say what an assertion does not prove**, in the code, next to what it does.
