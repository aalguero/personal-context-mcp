"""Materialising recurring tasks.

Recurrence is split between Python and SQL along a deliberate line:

  * **after_completion** series roll forward inside pa.complete_task(), because
    the next due date is a pure function of the finish date and belongs in the
    same transaction as the completion.

  * **fixed** series are expanded here, because expanding an RFC 5545 RRULE
    ("last Sunday of October", "every third Tuesday") is a solved problem in
    dateutil and would be several hundred lines of plpgsql to redo badly.

The scheduler is idempotent: a unique index on (recurrence_id, occurrence_date)
means running it five times a day, or twice concurrently, creates one task per
occurrence. That matters because it will be called both from a cron job and
opportunistically whenever a daily brief is generated.

The backfill guard is the other thing worth knowing about. A series with
dtstart in 2020 and no materialised occurrences would otherwise generate six
years of overdue trash on its first run. So a first run only looks back
`max_backfill_days` (default 30); older occurrences are treated as water under
the bridge.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from dateutil.rrule import rrulestr

from .db import Db, get_db


def _occurrences(rule_text: str, dtstart: _dt.date, window_start: _dt.date,
                 window_end: _dt.date) -> list[_dt.date]:
    """Expand an RRULE into dates within [window_start, window_end].

    dateutil wants a datetime for DTSTART; we work in whole days and throw the
    time component away, since 'due on the 1st' has no meaningful time of day.
    """
    text = rule_text.strip()
    if not text.upper().startswith(("RRULE:", "DTSTART", "FREQ")):
        text = "FREQ=" + text  # tolerate bare 'MONTHLY;BYMONTHDAY=1'
    if text.upper().startswith("FREQ"):
        text = "RRULE:" + text

    rule = rrulestr(text, dtstart=_dt.datetime.combine(dtstart, _dt.time.min))
    out: list[_dt.date] = []
    for occ in rule.between(
        _dt.datetime.combine(window_start, _dt.time.min),
        _dt.datetime.combine(window_end, _dt.time.max),
        inc=True,
    ):
        out.append(occ.date())
    return out


def run(as_of: str | _dt.date | None = None, max_backfill_days: int = 30,
        db: Db | None = None) -> dict[str, Any]:
    """Create task rows for every occurrence now due to exist.

    "Due to exist" means: occurrence_date <= as_of + lead_time_days. Lead time is
    how far ahead something should appear so it can be planned rather than
    discovered — a nursery pickup wants three weeks' notice; putting the bins out
    wants none.
    """
    db = db or get_db()
    today = _dt.date.fromisoformat(as_of) if isinstance(as_of, str) else (as_of or _dt.date.today())

    created: list[dict] = []
    skipped: list[dict] = []

    for r in db.rows("select * from pa.recurrence where active order by id"):
        dtstart = _dt.date.fromisoformat(r["dtstart"])
        until = _dt.date.fromisoformat(r["until_date"]) if r.get("until_date") else None
        lead = int(r.get("lead_time_days") or 0)
        horizon = today + _dt.timedelta(days=lead)

        if r["mode"] == "fixed":
            last = _dt.date.fromisoformat(r["last_spawned_for"]) if r.get("last_spawned_for") else None
            window_start = (last + _dt.timedelta(days=1)) if last else max(
                dtstart, today - _dt.timedelta(days=max_backfill_days)
            )
            if window_start > horizon:
                continue
            try:
                dates = _occurrences(r["rrule"], dtstart, window_start, horizon)
            except Exception as exc:  # a bad rule should not stop the others
                skipped.append({"recurrence_id": r["id"], "title": r["title"],
                                "error": f"bad rrule: {exc}"})
                continue
        else:
            # after_completion: normally rolled forward on completion. The only
            # case needing attention here is a series with no live instance at
            # all — newly created, or one whose task was deleted outright.
            existing = db.value(
                "select count(*) from pa.task where recurrence_id = %s and status not in ('done','dropped')",
                (r["id"],),
            )
            if existing:
                continue
            anchor = (_dt.date.fromisoformat(r["last_completed_on"])
                      if r.get("last_completed_on") else None)
            first = (anchor + _dt.timedelta(days=int(r["interval_days"]))) if anchor else dtstart
            dates = [first] if first <= horizon else []

        for occ in dates:
            if until and occ > until:
                continue
            row = db.row(
                """
                insert into pa.task
                  (project_id, person_id, title, notes, status, importance,
                   effort_minutes, energy, due_on, hard_deadline, defer_until,
                   recurrence_id, occurrence_date, source)
                values (%s,%s,%s,%s,'next',%s,%s,%s,%s,%s,%s,%s,%s,'recurrence')
                on conflict (recurrence_id, occurrence_date) do nothing
                returning id
                """,
                (r.get("project_id"), r.get("person_id"), r["title"], r.get("notes"),
                 r["importance"], r.get("effort_minutes"), r.get("energy"), occ,
                 r["hard_deadline"], (occ - _dt.timedelta(days=lead)) if lead else None,
                 r["id"], occ),
            )
            if row:
                db.execute(
                    """insert into pa.task_context(task_id, context_id)
                       select %s, context_id from pa.recurrence_context where recurrence_id = %s
                       on conflict do nothing""",
                    (row["id"], r["id"]),
                )
                created.append({"task_id": row["id"], "title": r["title"],
                                "due_on": occ.isoformat(), "recurrence_id": r["id"]})

        if dates:
            db.execute(
                "update pa.recurrence set last_spawned_for = greatest(coalesce(last_spawned_for, %s), %s) where id = %s",
                (max(dates), max(dates), r["id"]),
            )

    return {"as_of": today.isoformat(), "created": created,
            "created_count": len(created), "skipped": skipped}


def preview(recurrence_id: int, count: int = 6, db: Db | None = None) -> list[str]:
    """Next N occurrence dates for a series, without creating anything.

    Worth having: an RRULE is easy to get subtly wrong, and seeing the next six
    dates is the fastest way to notice that 'FREQ=MONTHLY;BYDAY=1SU' is not what
    you meant.
    """
    db = db or get_db()
    r = db.row("select * from pa.recurrence where id = %s", (recurrence_id,))
    if not r:
        raise ValueError(f"No recurrence {recurrence_id}")
    dtstart = _dt.date.fromisoformat(r["dtstart"])
    if r["mode"] == "after_completion":
        anchor = (_dt.date.fromisoformat(r["last_completed_on"])
                  if r.get("last_completed_on") else _dt.date.today())
        step = int(r["interval_days"])
        return [(anchor + _dt.timedelta(days=step * (i + 1))).isoformat() for i in range(count)]
    start = max(dtstart, _dt.date.today())
    end = start + _dt.timedelta(days=365 * 3)
    dates = _occurrences(r["rrule"], dtstart, start, end)
    return [d.isoformat() for d in dates[:count]]
