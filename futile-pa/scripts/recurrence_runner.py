#!/usr/bin/env python3
"""The recurrence engine's clock, and a check that it did its job (tasks 161, 188).

pa.recurrence.run() was built, tested in isolation, and never driven by
anything: the pa_recurrence_run tool says "normally driven by cron" and no
cron existed. Between 10-21 and 11-11 three Job Search series missed twelve
occurrences with no error anywhere. This script is that cron, on its own
systemd timer (recurrence-runner.timer, 03:00 ET), deliberately not inside the
check-in agent: the agent was dead for three days on an API credit error, and
a doer that dies with it would go unnoticed.

Two things it proves, kept apart because they are not the same claim:

  * IT RAN. On a successful run it writes recurrence_runner.last_run_at to the
    context store. external_watchdog.py alerts (recurrence_stale) when that is
    older than 26h or missing. A fresh stamp says the timer fired and run()
    returned -- nothing about whether the series are current.
  * THE SERIES ARE CURRENT. After run(), every active series is re-expanded
    with the engine's own _occurrences() and any occurrence that should
    already exist (due-to-exist more than SLACK_DAYS ago, after the series'
    last_spawned_for) but has no pa.task row is reported, by series id, in
    recurrence_runner.last_check. external_watchdog.py alerts on that
    (recurrence_behind). This is the check that would have caught 188: the
    engine firing into nothing.

The self-check trusts last_spawned_for as a deliberate boundary. Moving it
forward (as was done on 11-11 to discard missed occurrences) is a decision,
not a fault, and is not flagged.

Logs series ids and task ids only, never titles: a Job Search title is the
kind of content kept out of the journal. A failure logs a class name and
exits 0 -- the missing stamp is the signal, and the watchdog raises it.

Usage:
  recurrence_runner.py            # the nightly run (the timer calls this)
"""
from __future__ import annotations

import datetime as _dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

CATEGORY = "recurrence_runner"
# max_backfill_days bounds how far back ANY fixed series reaches (task 221; it
# used to bound only a series that had never spawned). An occurrence older
# than this that was never created is discarded, not created late as an
# overdue row. On 11-11 a 30-day bound would have created twelve overdue Job
# Search rows, five at importance 5 (task 188). One day is enough for a
# nightly runner: it covers a series created yesterday and a run that straddles
# midnight. Stated here as well as being the engine's default, so a change to
# that default cannot silently change the runner.
MAX_BACKFILL_DAYS = 1
# A due-to-exist occurrence this many days old with no row is a fault. run()
# has just created everything due through today, so one day is only slack for
# a run straddling midnight.
SLACK_DAYS = 1


def log(msg: str) -> None:
    print(f"recurrence_runner: {msg}", flush=True)


def unspawned(db, today: _dt.date) -> tuple[list[int], list[int]]:
    """(series behind, series with an unexpandable rule), by id.

    'Behind' means an occurrence whose task should already exist -- its
    occurrence date minus lead time is at least SLACK_DAYS in the past -- and
    has no pa.task row, counting only occurrences after last_spawned_for.
    """
    from pa.recurrence import _occurrences

    cutoff = today - _dt.timedelta(days=SLACK_DAYS)
    behind: list[int] = []
    bad: list[int] = []
    for r in db.rows("select * from pa.recurrence where active order by id"):
        dtstart = _dt.date.fromisoformat(r["dtstart"])
        until = _dt.date.fromisoformat(r["until_date"]) if r.get("until_date") else None
        lead = _dt.timedelta(days=int(r.get("lead_time_days") or 0))
        if r["mode"] == "fixed":
            last = _dt.date.fromisoformat(r["last_spawned_for"]) if r.get("last_spawned_for") else None
            start = (last + _dt.timedelta(days=1)) if last else dtstart
            end = cutoff + lead
            if until:
                end = min(end, until)
            try:
                # Expand one day first, so a rule that cannot be parsed is
                # reported now, not on the day a dormant 2027 series comes due:
                # run() skips a series before expanding it while nothing is due.
                _occurrences(r["rrule"], dtstart, dtstart, dtstart)
                dates = _occurrences(r["rrule"], dtstart, start, end) if start <= end else []
            except Exception:
                bad.append(r["id"])
                continue
            if not dates:
                continue
            have = {x["occurrence_date"] for x in db.rows(
                "select occurrence_date from pa.task where recurrence_id = %s and occurrence_date = any(%s)",
                (r["id"], dates))}
            if any(d.isoformat() not in have for d in dates):
                behind.append(r["id"])
        else:
            # after_completion: same rule as run() -- no live instance means the
            # next one is due from last_completed_on (or dtstart if never done).
            live = db.value("select count(*) from pa.task where recurrence_id = %s "
                            "and status not in ('done','dropped')", (r["id"],))
            if live:
                continue
            anchor = (_dt.date.fromisoformat(r["last_completed_on"])
                      if r.get("last_completed_on") else None)
            first = (anchor + _dt.timedelta(days=int(r["interval_days"]))) if anchor else dtstart
            if (until is None or first <= until) and first - lead <= cutoff:
                behind.append(r["id"])
    return behind, bad


def main() -> int:
    try:
        from assistant import context_store  # loads .env in-process; no DSN on argv
        from pa import recurrence
        from pa.db import Db

        db = Db(url=os.environ["PA_DATABASE_URL"], actor="system", use_pool=False)
        today = _dt.date.today()  # host TZ is America/New_York, same as Postgres current_date
        out = recurrence.run(as_of=today, max_backfill_days=MAX_BACKFILL_DAYS, db=db)
        made = ", ".join(f"series {c['recurrence_id']} -> task {c['task_id']}" for c in out["created"])
        log(f"created {out['created_count']} task(s)" + (f": {made}" if made else ""))
        if out["skipped"]:
            log("skipped series with a bad rule: " + ", ".join(str(s["recurrence_id"]) for s in out["skipped"]))
        # Missed occurrences given up rather than created late (task 221).
        # Normally empty. Non-empty means the runner was not running, which
        # recurrence_stale will already have alerted on. Ids and counts only.
        discarded = {str(d["recurrence_id"]): len(d["dates"]) for d in out["discarded"]}
        if discarded:
            log("discarded missed occurrence(s), not created late: "
                + ", ".join(f"series {k} x{v}" for k, v in discarded.items()))

        behind, bad = unspawned(db, today)
        if behind or bad:
            log("SELF-CHECK FAILED: series " + ", ".join(map(str, behind + bad))
                + " still have unspawned past occurrences" + (f" (bad rule: {bad})" if bad else ""))
        else:
            log("self-check: every active series is current")

        now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
        # last_check first, so a fresh last_run_at never sits beside a stale check.
        context_store.set(CATEGORY, "last_check", {
            "at": now, "as_of": today.isoformat(), "created_count": out["created_count"],
            "discarded": discarded, "behind": behind, "bad_rule": bad})
        context_store.set(CATEGORY, "last_run_at", now)
    except Exception as e:  # never a traceback: no locals, no DSN in the journal
        log(f"run failed ({type(e).__name__}) -- no stamp written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
