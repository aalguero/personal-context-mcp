#!/usr/bin/env python3
"""Raise and close the warning tasks backup.sh is responsible for.

Called once at the end of every backup.sh run with that run's outcome. Lives
in its own file rather than in heredocs so that the code a scratch-database
test exercises is the code production runs, not a copy of it.

Five alert kinds, each named for the condition it detects. Identity is
pa.task.metadata->>'backup_alert', written in the same transaction as the
task; the duplicate guard and the auto-close both match on it -- never on the
title, which is a label for a human and may be edited freely.

  marker          raised when                                closed when
  failure         result == failed                           result is ok or degraded
  no_off_machine  result == degraded (off_machine_copies 0)  result == ok
  no_off_site     result ok/degraded and off_site_copies 0   result ok/degraded and off_site_copies > 0
  pi_corruption   a Pi integrity check found a mismatch      a later Pi integrity check is clean
  no_signal_data  result ok/degraded, signal_included false  result ok/degraded, signal_included true

A condition that this run could not evaluate (a failed run says nothing about
B2; a run without an integrity check says nothing about the Pi's old files)
leaves its task alone in both directions.

no_off_site is deliberately independent of result. Downgrading result to
degraded on a B2 outage would skip remote pruning, so one broken destination
would quietly turn into unbounded growth on the Pi. no_signal_data follows
the same rule: the database backup is good without the Signal data, and
degraded would also raise a false no_off_machine.

Usage (PA_DATABASE_URL in the environment):
  backup_alerts.py --result ok --off-site-copies 1 --pi-integrity not_run \\
      --run-started 2026-10-20T03:15:00-04:00 --git-head abc1234
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from pa import repo  # noqa: E402
from pa.db import get_db  # noqa: E402

STATUS_FILE = "/home/dremon/.personal-context/backup-status.json"

ALERTS = {
    "failure": dict(
        title="Backup is failing — investigate",
        importance=5, effort_minutes=30, energy="medium",
        contexts=["computer", "focus"],
        notes="Written automatically by scripts/backup.sh. "
              f"See {STATUS_FILE} for the error. "
              "Importance 5: an unnoticed backup failure is the failure mode the "
              "whole design exists to prevent.",
    ),
    "no_off_machine": dict(
        title="Backup has no off-machine copy",
        importance=3, effort_minutes=15, energy="low",
        contexts=["computer"],
        notes="Written automatically by scripts/backup.sh. A verified, "
              f"encrypted backup exists locally (see {STATUS_FILE}) but reached "
              "neither vocalassistant nor B2. Lower importance than the failure "
              "task: the backup itself is good, it just isn't off this machine. "
              "Remote pruning on vocalassistant is skipped until this clears.",
    ),
    "no_off_site": dict(
        title="Backup has no off-site copy",
        importance=3, effort_minutes=15, energy="low",
        contexts=["computer"],
        notes="Written automatically by scripts/backup.sh. A verified backup "
              "exists, but the B2 leg failed, so nothing left the house tonight "
              f"(see destinations.b2 in {STATUS_FILE} and the journal for the "
              "masked rclone error). Likely causes: key revoked or expired, bucket "
              "reconfigured, an rclone upgrade changing behaviour. Remote pruning "
              "on vocalassistant continues as normal.",
    ),
    "pi_corruption": dict(
        title="Backup copy on vocalassistant failed its integrity check",
        importance=4, effort_minutes=20, energy="low",
        contexts=["computer"],
        notes="Written automatically by scripts/backup.sh. sha256sum -c on "
              "vocalassistant reported a mismatch or a missing file "
              f"(see pi_integrity_* in {STATUS_FILE}). Nothing was deleted. "
              "Importance 4, not 5: the local copy and B2 are both independently "
              "verified, but an SD card that corrupts one file may corrupt more.",
    ),
    "no_signal_data": dict(
        title="Backup is missing the Signal account data",
        importance=3, effort_minutes=15, energy="low",
        contexts=["computer"],
        notes="Written automatically by scripts/backup.sh. The database backup "
              "is verified and shipped, but signal-cli-data.tar.gz is not in it "
              f"(signal_included false in {STATUS_FILE}; the journal says why: "
              "container would not stop, directory missing, or tar failed). "
              "Without it, losing the server means re-registering the bot's "
              "number. Also check the signal-cli-rest-api container is running.",
    ),
}


def decide(result: str, off_site_copies: int, pi_integrity: str,
           signal_included: str = "unknown") -> tuple[set[str], set[str]]:
    """Return (markers to raise, markers to close) for one run's outcome."""
    trustworthy = result in ("ok", "degraded")
    raise_, close = set(), set()

    if result == "failed":
        raise_.add("failure")
    elif trustworthy:
        close.add("failure")

    if result == "degraded":
        raise_.add("no_off_machine")
    elif result == "ok":
        close.add("no_off_machine")

    if trustworthy:
        (raise_ if off_site_copies == 0 else close).add("no_off_site")

    if pi_integrity == "mismatch":
        raise_.add("pi_corruption")
    elif pi_integrity == "ok":
        close.add("pi_corruption")

    if trustworthy and signal_included == "false":
        raise_.add("no_signal_data")
    elif trustworthy and signal_included == "true":
        close.add("no_signal_data")

    return raise_, close


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", required=True, choices=["ok", "degraded", "failed"])
    ap.add_argument("--off-site-copies", type=int, required=True)
    ap.add_argument("--pi-integrity", required=True,
                    choices=["ok", "mismatch", "skipped", "not_run"])
    ap.add_argument("--pi-mismatches", default="",
                    help="newline-separated sha256sum failure lines, for the task notes")
    ap.add_argument("--run-started", required=True)
    ap.add_argument("--git-head", required=True)
    ap.add_argument("--signal-included", default="unknown",
                    choices=["true", "false", "unknown"],
                    help="whether the bundle has signal-cli-data.tar.gz; unknown = no bundle was built")
    args = ap.parse_args()

    to_raise, to_close = decide(args.result, args.off_site_copies, args.pi_integrity,
                                args.signal_included)
    run = f"run {args.run_started} (git {args.git_head})"

    db = get_db()
    try:
        for marker in sorted(to_raise):
            existing = db.row("""select id from pa.task
                                  where metadata->>'backup_alert' = %s
                                    and status not in ('done','dropped')""", (marker,))
            if existing:
                continue
            spec = dict(ALERTS[marker])
            if marker == "pi_corruption" and args.pi_mismatches.strip():
                spec["notes"] += "\n\nsha256sum -c reported:\n" + args.pi_mismatches.strip()
            with db.transaction():
                # "backup.sh" is not in pa.task's source check constraint (manual,
                # claude, google_tasks, gmail, calendar, recurrence, import -- see
                # migrations/001_core.sql). Closest existing fit.
                #
                # project=37 (Personal Assistant Operations), not "admin-2026"
                # (Household Admin 2026): the working agreement (2026-10-23)
                # moved script-written alerts there specifically because
                # Household Admin is where task 156 sat invisible for two
                # days. Numeric id, not a slug -- the project has none set.
                t = repo.task_add(project=37, status="next",
                                  source="manual", db=db, **spec)
                # task_add takes no metadata argument; same transaction, so the
                # task never exists without its marker.
                db.execute("""update pa.task
                                 set metadata = metadata || jsonb_build_object('backup_alert', %s::text)
                               where id = %s""", (marker, t["id"]))
            print(f"backup.sh: raised {marker} task {t['id']}")

        # Closing matters as much as raising: an open alert suppresses every
        # new one of its kind through the duplicate guard above. Task 105 sat
        # open for nine days as a stale alarm silencing the alarm system.
        for marker in sorted(to_close):
            open_tasks = db.rows("""select id from pa.task
                                     where metadata->>'backup_alert' = %s
                                       and status not in ('done','dropped')""", (marker,))
            for t in open_tasks:
                repo.task_complete(t["id"],
                    note=f"Closed automatically by scripts/backup.sh: {run} "
                         f"cleared the {marker} condition.",
                    db=db)
                print(f"backup.sh: closed {marker} task {t['id']}")
    finally:
        # Without an explicit close the pool's threads take ~20s to time out at exit.
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
