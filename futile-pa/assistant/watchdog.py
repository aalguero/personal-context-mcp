"""Two independent staleness checks, run from the scheduler on every fire:
has backup.sh actually run recently (task 124), and has the check-in agent
itself actually completed a run recently (task 182).

These are not the same shape of problem, even though they look alike:

- The backup check is genuinely independent. backup.sh runs on its own
  systemd timer, unrelated to this service's scheduler, so this process
  checking on that one is a real second witness — if backup.sh's timer dies,
  this process is still running and still notices.
- The check-in self-check is NOT fully independent, and can't be from in
  here. It runs inside the same process as the thing it watches. If this
  entire service dies, this check dies with it — the exact "a watchdog
  cannot watch itself" limitation task 182 already names. What it CAN still
  catch: the process staying alive while run_once() keeps failing every
  cycle, which systemd's own active/failed status can't tell you (systemd
  only knows the Python process is running, not whether it's accomplishing
  anything). A true fix for total process death needs something outside
  this service entirely — unbuilt, and out of scope here.

Both alerts use the same metadata-marker convention backup_alerts.py already
established (pa.task.metadata->>'watchdog_alert', not the title, is identity),
under a distinct key so they're never confused with backup.sh's own
backup_alert markers, and both file into project 37 (Personal Assistant
Operations) — the same project the working agreement moved backup alerts
into, for the same reason: an alert filed in the wrong place is an alert
nobody reads.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from pa import repo
from pa.db import get_db

from . import toolset
from .checkin import WATCHDOG_CATEGORY

log = logging.getLogger("futile-assistant.watchdog")

OPERATIONS_PROJECT = 37  # Personal Assistant Operations — numeric id, the project has no slug

BACKUP_STATUS_FILE = Path("/home/dremon/.personal-context/backup-status.json")
BACKUP_STALE_AFTER_HOURS = 30  # nightly at 03:15; one missed night plus a buffer

# Captured at import time, which happens once when the scheduler (or a
# one-shot CLI command) starts — a proxy for "when did this process begin."
# Needed for the grace-period fix below.
PROCESS_STARTED_AT = datetime.now(timezone.utc)

CHECKIN_STALE_AFTER_HOURS = 6
# Task 182's own guard against crying wolf: the longest legitimate gap between
# fires is the overnight one (19:48 to 07:48, twelve hours), so a naive 6h
# threshold applied overnight would alarm every single morning. Only evaluate
# during the hours a fire is actually expected.
CHECKIN_ALERT_WINDOW = (8, 30, 20, 30)  # 08:30-20:30 America/New_York
TZ = ZoneInfo("America/New_York")

ALERTS = {
    "backup_stale": dict(
        title="Backup hasn't run in over a day — check the timer on futileassistant",
        importance=4, effort_minutes=15, energy="low", contexts=["computer"],
        notes="Written automatically by assistant/watchdog.py. backup.sh's own "
              "alerts cover every way a *running* backup can fail, but nothing "
              "previously noticed a backup that stopped running at all (task 124). "
              f"See {BACKUP_STATUS_FILE} for the last known attempt.",
    ),
    "checkin_stale": dict(
        title="The task check-in agent hasn't completed a run in hours — check futile-assistant.service",
        importance=3, effort_minutes=15, energy="low", contexts=["computer"],
        notes="Written automatically by assistant/watchdog.py. This can only "
              "detect the process running but failing repeatedly, not the "
              "process itself being dead (task 182's remaining limitation) — "
              "if this fires, check `systemctl --user status futile-assistant.service` "
              "and the journal, not just the task data.",
    ),
}


def run_checks() -> None:
    """Run both checks. Each is independent; one failing (e.g. a bad status
    file) must not block the other."""
    try:
        _check_backup()
    except Exception:
        log.exception("Backup staleness check failed")
    try:
        _check_checkin()
    except Exception:
        log.exception("Check-in staleness check failed")


def _check_backup() -> None:
    now = datetime.now(timezone.utc)
    try:
        status = json.loads(BACKUP_STATUS_FILE.read_text(encoding="utf-8"))
        last_attempt = datetime.fromisoformat(status["last_attempt"])
        age_hours = (now - last_attempt).total_seconds() / 3600
        stale = age_hours > BACKUP_STALE_AFTER_HOURS
        detail = f"last attempt {status['last_attempt']}, {age_hours:.1f}h ago"
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        # Missing or unreadable is itself worth a human looking at — it's the
        # same actionable signal as genuine staleness, from the backlog's
        # point of view, so it raises the same marker rather than a silent skip.
        stale = True
        detail = f"status file unreadable ({exc})"

    log.info("Backup staleness check: %s (%s)", "STALE" if stale else "fresh", detail)
    _apply("backup_stale", stale, detail)


def _check_checkin() -> None:
    now_et = datetime.now(TZ)
    start_h, start_m, end_h, end_m = CHECKIN_ALERT_WINDOW
    in_window = (start_h, start_m) <= (now_et.hour, now_et.minute) <= (end_h, end_m)
    if not in_window:
        log.info("Check-in staleness check: outside the %02d:%02d-%02d:%02d window, skipping",
                  start_h, start_m, end_h, end_m)
        return

    now_utc = now_et.astimezone(timezone.utc)
    last_run = toolset.dispatch("get_context", {"category": WATCHDOG_CATEGORY}).get("last_run_at")

    if not last_run:
        # Fix-4 deploy found this blind spot: last_run_at is only written on a
        # *successful* run, so "the agent has never once succeeded" and "this
        # process just started a minute ago" looked identical — meaning a
        # permanently-broken agent would never trip this check at all, the
        # same "change that controls nothing" shape as everything else this
        # project has been burned by. A grace period since process start
        # keeps a fresh restart quiet while still catching persistent failure.
        since_start_hours = (now_utc - PROCESS_STARTED_AT).total_seconds() / 3600
        if since_start_hours <= CHECKIN_STALE_AFTER_HOURS:
            log.info("Check-in staleness check: no successful run yet, within the "
                      "%.1fh grace period since process start, skipping", CHECKIN_STALE_AFTER_HOURS)
            return
        detail = f"no successful run since process start, {since_start_hours:.1f}h ago"
        log.info("Check-in staleness check: STALE (%s)", detail)
        _apply("checkin_stale", True, detail)
        return

    age_hours = (now_utc - datetime.fromisoformat(last_run)).total_seconds() / 3600
    stale = age_hours > CHECKIN_STALE_AFTER_HOURS
    detail = f"last successful run {last_run}, {age_hours:.1f}h ago"
    log.info("Check-in staleness check: %s (%s)", "STALE" if stale else "fresh", detail)
    _apply("checkin_stale", stale, detail)


def _apply(marker: str, stale: bool, detail: str) -> None:
    """Raise `marker` if stale and nothing open already carries it; close any
    open one if not stale. Mirrors backup_alerts.py's raise/close/dedupe
    shape exactly, under the watchdog_alert key instead of backup_alert.
    """
    db = get_db()
    existing = db.row(
        """select id from pa.task
             where metadata->>'watchdog_alert' = %s
               and status not in ('done','dropped')""",
        (marker,),
    )
    if stale:
        if existing:
            return
        spec = dict(ALERTS[marker])
        spec["notes"] += f"\n\n{detail}"
        with db.transaction():
            t = repo.task_add(project=OPERATIONS_PROJECT, status="next", source="manual", db=db, **spec)
            db.execute(
                """update pa.task set metadata = metadata || jsonb_build_object('watchdog_alert', %s::text)
                     where id = %s""",
                (marker, t["id"]),
            )
        log.warning("Raised watchdog alert %s: task %s", marker, t["id"])
    elif existing:
        repo.task_complete(existing["id"],
                            note=f"Closed automatically by assistant/watchdog.py: {detail}.", db=db)
        log.info("Closed watchdog alert %s: task %s", marker, existing["id"])
