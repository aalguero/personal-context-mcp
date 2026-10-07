#!/usr/bin/env python3
"""Grandfather-father-son retention for futile-pa-<stamp>.tar.age backups.

Pure function of filenames, no filesystem access, so it works the same way
against a local directory listing or a listing read from a remote host over
SSH -- the caller does the deleting.

Keeps:
  * the 7 most recent (daily)
  * the most recent file in each of the last 4 ISO weeks (weekly)
  * the most recent file in each of the last 12 calendar months (monthly)
Union of the three. Never empties the directory, whatever the dates say.

Usage:  find <dir> -maxdepth 1 -name 'futile-pa-*.tar.age' -printf '%f\n' \
          | python3 prune_backups.py
Prints the filenames to delete, one per line. Prints nothing, and exits 0,
when there is nothing to prune.
"""
from __future__ import annotations

import datetime as dt
import re
import sys

NAME_RE = re.compile(r"^futile-pa-(\d{8})-(\d{6})\.tar\.age$")


def parse_stamp(name: str) -> dt.datetime | None:
    m = NAME_RE.match(name.strip())
    if not m:
        return None
    date_part, time_part = m.groups()
    try:
        return dt.datetime.strptime(date_part + time_part, "%Y%m%d%H%M%S")
    except ValueError:
        return None


def iso_week(d: dt.date) -> tuple[int, int]:
    y, w, _ = d.isocalendar()
    return (y, w)


def year_month(d: dt.date) -> tuple[int, int]:
    return (d.year, d.month)


def main() -> None:
    files = [line.strip() for line in sys.stdin if line.strip()]
    dated = sorted(
        ((parse_stamp(f), f) for f in files),
        key=lambda pair: pair[0] or dt.datetime.min,
        reverse=True,
    )
    # Anything that doesn't match the naming convention is left alone --
    # pruning only ever acts on files it fully understands.
    dated = [(ts, f) for ts, f in dated if ts is not None]

    if len(dated) <= 1:
        return

    keep: set[str] = set()
    keep.update(f for _, f in dated[:7])  # daily

    seen_weeks: set[tuple[int, int]] = set()
    for ts, f in dated:
        wk = iso_week(ts.date())
        if wk not in seen_weeks:
            seen_weeks.add(wk)
            keep.add(f)
        if len(seen_weeks) >= 4:
            break

    seen_months: set[tuple[int, int]] = set()
    for ts, f in dated:
        ym = year_month(ts.date())
        if ym not in seen_months:
            seen_months.add(ym)
            keep.add(f)
        if len(seen_months) >= 12:
            break

    total = len(dated)
    to_delete = [f for _, f in dated if f not in keep]

    # Belt and suspenders on top of the per-category logic above: never let
    # a bug here empty the directory.
    if total - len(to_delete) < 1:
        to_delete = to_delete[:-1] if to_delete else []

    for f in to_delete:
        print(f)


if __name__ == "__main__":
    main()
