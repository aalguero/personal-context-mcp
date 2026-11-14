#!/usr/bin/env python3
"""An alarm that does not depend on the thing it watches (task 182).

Run once a night at the end of backup.sh -- a different unit, a different
trigger and a different process from futile-assistant.service. Between
2026-11-06 and 11-09 that service failed every check-in on an API credit
error while reporting active (running), Result=success, NRestarts=0; its
in-process watchdog raised task 199 correctly, and the only thing that reads
tasks and talks to Andre was the thing that had failed. So:

  * Assert on outcomes, never on health. task_agent_watchdog.last_run_at is
    written only by a *successful* check-in (a failed run writes nothing --
    proven 11-08), so it cannot read fresh while the agent is failing. Unit
    state, process liveness and exit codes are deliberately not consulted.
  * Deliver by calling the signal-cli-rest-api container directly -- its own
    Docker process -- so the alert needs neither the assistant, nor the
    Anthropic API, nor anyone reading Postgres. Signal is the alert; the task
    is the record.
  * Repeat, don't dedupe, the message: once per run while the condition
    holds, which at one run a night is self-limiting. The task row is
    deduped and auto-closed on pa.task.metadata->>'external_watchdog_alert'
    -- a different key from the in-process watchdog's 'watchdog_alert', so
    the two never fight over one row.

  marker               level  raised when                                 closed when
  checkin_stale        ALERT  last_run_at older than 10h, or unreadable   last_run_at fresh
  delivery_stale       ALERT  last_delivered_at older than 10h, or        last_delivered_at fresh
                              unreadable or absent (task 205)
  off_machine_reduced  WARN   result ok but off_machine_copies < 2        result ok with 2 copies
                              (result degraded/failed belongs to backup_alerts.py and is left alone)
  recurrence_stale     ALERT  recurrence_runner.last_run_at older than    last_run_at fresh
                              26h, or unreadable or absent (task 161)
  recurrence_behind    WARN   the runner's own self-check found a series  self-check clean
                              with an unspawned past occurrence (188)

The two recurrence checks prove different things. recurrence_stale green means
the runner's timer fired and pa.recurrence.run() returned -- nothing more.
recurrence_behind green means that run's re-expansion found every active
series current. Only the second says the tasks actually exist; it is skipped,
not passed, while recurrence_stale alerts, since a stale self-check proves
nothing about tonight.

Never logs or sends a phone number, a key, or a message body. Errors are a
class name or an HTTP status.

Usage:
  external_watchdog.py              # the nightly run (backup.sh calls this)
  external_watchdog.py --dry-run    # verdicts only: no send, no task writes (reads pa.agent_state)
  external_watchdog.py --test-message [--api-base URL]
  external_watchdog.py --context-file FILE   # synthetic input; messages say [TEST]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

ENV_FILE = "/home/dremon/tools/personal-context-mcp/.env"
# The stamps live in pa.agent_state since migration 009 (they were in
# context.json). Reading them now needs Postgres, which is fine for the
# property above: a database that cannot be read makes every stamp
# STORE_UNREADABLE, which alerts, and the alert itself still goes straight to
# the Signal container. --context-file still reads a context.json-shaped file,
# for synthetic tests.
STATUS_FILE = "/home/dremon/.personal-context/backup-status.json"
SIGNAL_CONTAINER = "signal-cli-rest-api"
TZ = ZoneInfo("America/New_York")

# Fires are 07:48/11:48/15:48/19:48 ET and this runs at ~03:16, so on a
# healthy night the newest success is the 19:48 fire, 7h28m old. 10h leaves
# 2.5h of margin above that and still catches one missed 19:48 fire (11h28m)
# the same night. Not the in-process 6h: that is right on a 4-hour cadence and
# would alarm here every single night.
CHECKIN_STALE_AFTER = timedelta(hours=10)
# recurrence_runner.py is nightly at 03:00 (+5min jitter) and this runs at
# 03:15-03:30 (+15min jitter); the ordering is a nicety, not a requirement. 26h
# is one night plus both jitters, so either order is fine; 24h would be a coin
# flip on a night the runner fires late and the backup early.
RECURRENCE_STALE_AFTER = timedelta(hours=26)
# A stamp slightly ahead of this clock is skew; far ahead means the clock or
# the store is wrong, and a stamp from the future proves nothing about today.
FUTURE_TOLERANCE = timedelta(minutes=10)
CONTAINER_READY_TIMEOUT = 180  # health check interval is 20s

STORE_UNREADABLE = object()
MISSING_KEY = object()

ALERTS = {
    "checkin_stale": dict(
        title="Check-in agent has stopped completing runs (external watchdog)",
        importance=5, effort_minutes=20, energy="medium",
        contexts=["computer", "focus"],
        notes="Written automatically by scripts/external_watchdog.py at the end of "
              "the nightly backup. task_agent_watchdog.last_run_at -- written only by a "
              "successful check-in -- is older than 10h or unreadable. The unit may well "
              "say active (running); that is not evidence (see the 11-06 outage). Start "
              "with journalctl --user -u futile-assistant.service, then API credit. "
              "The in-process watchdog may have raised its own task for the same "
              "condition; that is deliberate, two independent records.",
    ),
    "delivery_stale": dict(
        title="No message has reached Andre's phone recently (external watchdog)",
        importance=5, effort_minutes=20, energy="medium",
        contexts=["computer", "focus"],
        notes="Written automatically by scripts/external_watchdog.py. "
              "task_agent_watchdog.last_delivered_at -- stamped only when a Signal "
              "delivery receipt from Andre's device reaches the bridge -- is older than "
              "10h or absent. Every check-in run sends something (questions or the "
              "heartbeat), so on a healthy day this refreshes four times. If last_run_at "
              "is fresh, the agent is running and nothing is reaching him: check the "
              "phone, the Signal account, and the bridge's 'Signal receive' journal "
              "lines. Note the Signal alert for this travels the same channel, so the "
              "task may be the only record that arrives.",
    ),
    "off_machine_reduced": dict(
        title="Backup reached only one off-machine copy",
        importance=2, effort_minutes=10, energy="low",
        contexts=["computer"],
        notes="Written automatically by scripts/external_watchdog.py. The backup "
              f"verified and reported result=ok, but off_machine_copies < 2 (see "
              f"destinations in {STATUS_FILE}). Usually the Pi was switched off; at "
              "03:16 that looks identical to a dead SD card. A redundancy notice, not "
              "an outage. If this fires most nights, that is a conversation to have, "
              "not a threshold to raise.",
    ),
    "recurrence_stale": dict(
        title="Recurrence runner has stopped completing runs (external watchdog)",
        importance=4, effort_minutes=20, energy="medium",
        contexts=["computer", "focus"],
        notes="Written automatically by scripts/external_watchdog.py. "
              "recurrence_runner.last_run_at -- written only by a successful run of "
              "scripts/recurrence_runner.py -- is older than 26h or absent, so no recurring "
              "task has been created since. Start with systemctl --user list-timers and "
              "journalctl --user -u recurrence-runner.service. Missed occurrences are NOT "
              "lost for a series that has spawned before: it resumes from last_spawned_for, "
              "so check pa_recurrence_list before restarting it after a long gap.",
    ),
    "recurrence_behind": dict(
        title="A recurring series has occurrences that were never created (external watchdog)",
        importance=4, effort_minutes=30, energy="medium",
        contexts=["computer", "focus"],
        notes="Written automatically by scripts/external_watchdog.py. The recurrence "
              "runner ran, but its own self-check found an active series with a past "
              "occurrence that has no pa.task row (or a rule it cannot expand) -- a fault "
              "in the engine or in the series definition, the shape of task 188. The "
              "series ids are in recurrence_runner.last_check and the runner's journal "
              "line. Use pa_recurrence_preview on each before changing anything.",
    ),
}


def log(msg: str) -> None:
    print(f"external_watchdog: {msg}", flush=True)


# ---------------------------------------------------------------------------
# Verdicts: pure functions of their inputs, so they can be tested with
# synthetic values without touching the real store.
# ---------------------------------------------------------------------------
def _check_stamp(raw: object, now: datetime, field: str, stale: str, fresh: str,
                 category: str = "task_agent_watchdog",
                 stale_after: timedelta = CHECKIN_STALE_AFTER) -> tuple[str, str]:
    """('ok' | 'alert', reason). Anything that is not positive proof of a
    recent success alerts: a watchdog that cannot read its input must say so,
    not go quiet."""
    if raw is STORE_UNREADABLE:
        return "alert", f"the agent state could not be read (database unreachable, or the test file missing), so {field} cannot be checked"
    if raw is MISSING_KEY or raw in (None, ""):
        return "alert", f"the agent state has no {category}.{field}"
    try:
        stamp = datetime.fromisoformat(str(raw))
    except ValueError:
        return "alert", f"{category}.{field} is unparseable"
    if stamp.tzinfo is None:
        return "alert", f"{category}.{field} has no timezone"
    age = now - stamp
    if age < -FUTURE_TOLERANCE:
        return "alert", (f"{category}.{field} is {(-age).total_seconds() / 3600:.1f}h "
                         "in the future (clock or store wrong)")
    if age > stale_after:
        last = stamp.astimezone(TZ).strftime("%a %H:%M ET")
        return "alert", f"{stale} for {age.total_seconds() / 3600:.1f}h (last {last})"
    return "ok", f"{fresh} {max(age.total_seconds(), 0) / 3600:.1f}h ago"


def check_checkin(raw: object, now: datetime) -> tuple[str, str]:
    return _check_stamp(raw, now, "last_run_at", "no check-in has completed", "last check-in")


def check_delivered(raw: object, now: datetime) -> tuple[str, str]:
    """last_delivered_at is stamped only by a delivery receipt from Andre's
    device. Same 10h threshold and the same reasoning: every fire sends
    something, so on a healthy night the newest receipt is the 19:48 one,
    7h28m before the 03:16 backup. Absent alerts -- it is not 'fine'."""
    return _check_stamp(raw, now, "last_delivered_at",
                        "no message has reached Andre's phone", "last delivery receipt")


def diagnose(run_level: str, delivered_level: str) -> str:
    """The two assertions fail differently, and the combination says why."""
    return {
        ("ok", "ok"): "healthy",
        ("alert", "alert"): "the agent is not running, and nothing is reaching Andre",
        ("ok", "alert"): "the agent is running but nothing is reaching Andre's phone",
        # Possible since the heartbeat: a failing check-in still sends a
        # failure notice, and that notice gets a receipt.
        ("alert", "ok"): "the agent's runs are failing, but its failure notices are reaching Andre",
    }[(run_level, delivered_level)]


def check_recurrence_run(raw: object, now: datetime) -> tuple[str, str]:
    """recurrence_runner.last_run_at, written only by a successful run. Absent
    alerts: the first-night case is avoided by running the runner once by hand
    at install, not by treating 'never ran' as fine."""
    return _check_stamp(raw, now, "last_run_at", "no recurrence run has completed",
                        "last recurrence run", category="recurrence_runner",
                        stale_after=RECURRENCE_STALE_AFTER)


def check_recurrence_current(check: object, last_run_at: object, run_level: str) -> tuple[str, str]:
    """('ok' | 'warn' | 'skip', reason) from the runner's own self-check.
    skip while recurrence_stale alerts: an old self-check says nothing about
    tonight, and one cause should not send two messages."""
    if run_level != "ok":
        return "skip", "no fresh run to judge (see recurrence_stale)"
    if not isinstance(check, dict):
        return "warn", "recurrence_runner.last_check is missing or unreadable"
    if check.get("at") != last_run_at:
        return "warn", "recurrence_runner.last_check is not from the latest run"
    behind = list(check.get("behind") or []) + list(check.get("bad_rule") or [])
    if behind:
        return "warn", ("recurring series " + ", ".join(str(i) for i in behind)
                        + " have past occurrences that were never created")
    return "ok", f"every active series current as of {check.get('as_of')}"


def diagnose_recurrence(run_level: str, current_level: str) -> str:
    if run_level != "ok":
        return "the recurrence runner has not completed a run"
    if current_level != "ok":
        return "the recurrence runner is running but some series are not being created"
    return "recurrences current"


def check_off_machine(status: object) -> tuple[str, str]:
    """('ok' | 'warn' | 'skip', reason). skip = not this check's business
    (degraded and failed runs are backup_alerts.py's)."""
    if not isinstance(status, dict):
        return "warn", "backup-status.json is missing or unreadable"
    if status.get("result") != "ok":
        return "skip", f"result={status.get('result')}: handled by backup_alerts.py"
    copies = status.get("off_machine_copies")
    if not isinstance(copies, int):
        return "warn", "backup-status.json has no off_machine_copies"
    if copies >= 2:
        return "ok", f"off_machine_copies={copies}"
    dests = status.get("destinations") or {}
    failed = sorted(k for k, v in dests.items() if k != "local" and v != "ok")
    return "warn", (f"last night's backup reached {copies} of 2 off-machine copies"
                    + (f" ({', '.join(failed)} did not)" if failed else ""))


def read_last_run_at(path: str | None = None) -> object:
    return read_watchdog_field("last_run_at", path)


def read_watchdog_field(key: str, path: str | None = None,
                        category: str = "task_agent_watchdog") -> object:
    """From pa.agent_state, or from a context.json-shaped file when `path` is
    given (synthetic tests only)."""
    if path is None:
        return _read_agent_state(category, key)
    try:
        with open(path) as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return STORE_UNREADABLE
    except (OSError, ValueError):
        return "unreadable"  # check_checkin turns this into an unparseable alert
    entry = (data.get("contexts") or {}).get(category)
    if not isinstance(entry, dict) or key not in entry:
        return MISSING_KEY
    return entry[key]


def _read_agent_state(category: str, key: str) -> object:
    try:
        from pa.db import get_db
        row = get_db().row("select value from pa.agent_state where namespace = %s and key = %s",
                           (category, key))
    except Exception as e:  # class name only: no DSN, no traceback
        log(f"reading {category}.{key} failed ({type(e).__name__})")
        return STORE_UNREADABLE
    if row is None or "value" not in row:   # Db.row drops a JSON null with the column
        return MISSING_KEY
    return row["value"]


def read_status(path: str = STATUS_FILE) -> object:
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Signal, direct to the container. Numbers come from .env and go only into a
# request body -- never a URL, a log line, an argument or an exception text.
# ---------------------------------------------------------------------------
def _env(key: str) -> str:
    try:
        with open(ENV_FILE) as fh:
            for line in fh:
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def container_ready(api_base: str, timeout: float = CONTAINER_READY_TIMEOUT) -> tuple[bool, str]:
    """backup.sh stops this container mid-run and `docker start` returns before
    it can serve, so wait for Docker's own health verdict and then confirm
    the API answers ourselves."""
    deadline = time.monotonic() + timeout
    health = "unknown"
    while time.monotonic() < deadline:
        try:
            health = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Health.Status}}", SIGNAL_CONTAINER],
                capture_output=True, text=True, timeout=15).stdout.strip() or "unknown"
        except (OSError, subprocess.SubprocessError) as e:
            health = type(e).__name__
        if health == "healthy":
            try:
                with urllib.request.urlopen(f"{api_base}/v1/health", timeout=10):
                    return True, "healthy"
            except urllib.error.HTTPError as e:
                health = f"health endpoint HTTP {e.code}"
            except (urllib.error.URLError, OSError) as e:
                health = f"health endpoint {type(e).__name__}"
        time.sleep(5)
    return False, f"container not ready after {timeout:.0f}s ({health})"


def send(text: str, api_base: str) -> tuple[bool, str]:
    own, to = _env("SIGNAL_OWN_NUMBER"), _env("SIGNAL_ALLOWED_SENDER")
    if not (own and to):
        return False, "Signal numbers not configured in .env"
    ready, why = container_ready(api_base)
    if not ready:
        return False, why
    body = json.dumps({"message": text, "number": own, "recipients": [to]}).encode()
    req = urllib.request.Request(f"{api_base}/v2/send", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30):
            return True, "sent"
    except urllib.error.HTTPError as e:
        return False, f"send: HTTP {e.code}"
    except (urllib.error.URLError, OSError) as e:
        return False, f"send: {type(e).__name__}"


# ---------------------------------------------------------------------------
# The record: one open task per marker, closed when the condition clears.
# ---------------------------------------------------------------------------
def record(raise_: set[str], close: set[str]) -> dict[str, int]:
    """Returns {marker: open task id} for raised markers. Best-effort: the
    caller sends regardless of whether this succeeds."""
    from pa import repo
    from pa.db import get_db

    ids: dict[str, int] = {}
    db = get_db()
    try:
        for marker in sorted(raise_):
            existing = db.row("""select id from pa.task
                                  where metadata->>'external_watchdog_alert' = %s
                                    and status not in ('done','dropped')""", (marker,))
            if existing:
                ids[marker] = existing["id"]
                continue
            with db.transaction():
                # source/project: same reasoning as backup_alerts.py -- "manual"
                # is the closest allowed source, 37 is Personal Assistant Operations.
                t = repo.task_add(project=37, status="next", source="manual", db=db,
                                  **ALERTS[marker])
                db.execute("""update pa.task
                                 set metadata = metadata || jsonb_build_object('external_watchdog_alert', %s::text)
                               where id = %s""", (marker, t["id"]))
            ids[marker] = t["id"]
            log(f"raised {marker} task {t['id']}")
        for marker in sorted(close):
            for t in db.rows("""select id from pa.task
                                 where metadata->>'external_watchdog_alert' = %s
                                   and status not in ('done','dropped')""", (marker,)):
                repo.task_complete(t["id"], note="Closed automatically by scripts/external_watchdog.py: "
                                                 f"the {marker} condition cleared.", db=db)
                log(f"closed {marker} task {t['id']}")
    finally:
        db.close()
    return ids


def message(marker: str, reason: str, task_id: int | None, test: bool = False,
            diagnosis: str = "") -> str:
    ref = f" Task #{task_id}." if task_id else ""
    tag = "[TEST — synthetic input] " if test else ""
    why = f" Diagnosis: {diagnosis}." if diagnosis else ""
    if marker == "checkin_stale":
        return (f"{tag}⚠️ futile-pa watchdog: {reason}.{why} The service may still show as running; "
                f"check journalctl --user -u futile-assistant.service, then API credit.{ref}")
    if marker == "delivery_stale":
        return (f"{tag}⚠️ futile-pa watchdog: {reason}.{why} If you are reading this, the channel "
                f"is back — check the phone was online and Signal is still registered.{ref}")
    if marker == "recurrence_stale":
        return (f"{tag}⚠️ futile-pa watchdog: {reason}.{why} No recurring task is being created; "
                f"check journalctl --user -u recurrence-runner.service.{ref}")
    if marker == "recurrence_behind":
        return (f"{tag}futile-pa watchdog (warning): {reason}.{why} The runner ran but did not "
                f"do its job; check pa_recurrence_preview for those series.{ref}")
    return f"{tag}futile-pa watchdog (warning): {reason}. Is the Pi on?{ref}"


def run(api_base: str, dry_run: bool, context_file: str | None = None) -> int:
    now = datetime.now(timezone.utc)
    ctx = context_file  # None: the real store, pa.agent_state
    verdicts = {
        "checkin_stale": check_checkin(read_watchdog_field("last_run_at", ctx), now),
        "delivery_stale": check_delivered(read_watchdog_field("last_delivered_at", ctx), now),
        "off_machine_reduced": check_off_machine(read_status()),
    }
    rec_run = read_watchdog_field("last_run_at", ctx, "recurrence_runner")
    verdicts["recurrence_stale"] = check_recurrence_run(rec_run, now)
    verdicts["recurrence_behind"] = check_recurrence_current(
        read_watchdog_field("last_check", ctx, "recurrence_runner"), rec_run,
        verdicts["recurrence_stale"][0])
    diagnosis = diagnose(verdicts["checkin_stale"][0], verdicts["delivery_stale"][0])
    rec_diagnosis = diagnose_recurrence(verdicts["recurrence_stale"][0], verdicts["recurrence_behind"][0])
    log(f"diagnosis: {diagnosis}")
    log(f"recurrence diagnosis: {rec_diagnosis}")
    raise_, close = set(), set()
    for marker, (level, reason) in verdicts.items():
        log(f"{marker}: {level} ({reason})")
        if level in ("alert", "warn"):
            raise_.add(marker)
        elif level == "ok":
            close.add(marker)
    if dry_run:
        return 0

    ids: dict[str, int] = {}
    try:
        ids = record(raise_, close)
    except Exception as e:  # never a traceback: no locals, no URLs in the journal
        log(f"recording tasks failed ({type(e).__name__}) — sending anyway")

    for marker in sorted(raise_):
        diag = {"off_machine_reduced": "", "recurrence_stale": rec_diagnosis,
                "recurrence_behind": rec_diagnosis}.get(marker, diagnosis)
        ok, why = send(message(marker, verdicts[marker][1], ids.get(marker),
                               test=context_file is not None, diagnosis=diag),
                       api_base)
        log(f"{marker}: Signal alert {'sent' if ok else 'NOT sent (' + why + ')'}")
    if not raise_:
        log("all clear — nothing sent")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="verdicts only: no send, no task writes")
    ap.add_argument("--test-message", action="store_true",
                    help="send one clearly marked test message, then exit (no database)")
    ap.add_argument("--api-base", default=None,
                    help="override SIGNAL_API_BASE (testing the failure path)")
    ap.add_argument("--context-file", default=None,
                    help="read the watchdog and recurrence_runner stamps from this file instead of the real store (testing); "
                         "messages are then marked [TEST]")
    args = ap.parse_args()
    api_base = args.api_base or _env("SIGNAL_API_BASE") or "http://127.0.0.1:8080"
    try:
        if args.test_message:
            ok, why = send("[TEST] futile-pa external watchdog: test message, no action needed.", api_base)
            log(f"test message {'sent' if ok else 'NOT sent (' + why + ')'}")
            return 0 if ok else 1
        return run(api_base, args.dry_run, args.context_file)
    except Exception as e:
        log(f"unexpected error ({type(e).__name__})")
        return 1


if __name__ == "__main__":
    sys.exit(main())
