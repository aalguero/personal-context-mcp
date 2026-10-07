#!/usr/bin/env python3
"""Load the curated Google Tasks migration into pa.task.

Reads the raw Takeout export plus `task_plan.json`, which carries a per-task
decision for every open task in that export. It refuses to run if any open task
is missing from the plan — so the plan cannot silently fall out of date with the
export, and nothing is imported by accident or dropped by oversight.

What it does, in order:
  1. skips completed and deleted tasks (3,421 completed — history, not work)
  2. drops email-capture junk
  3. collapses duplicates case-insensitively, keeping the most recent instance
     (Google's recurrence handling left RMV appointment x25, Rosco Meds x21)
  4. sets aside the lists that are not task lists (packing, watchlist) — those
     went to the context store instead
  5. applies the plan's project / effort / energy / contexts / importance / person
  6. inserts with source='google_tasks' and the Google task id as source_id, so a
     re-run updates rather than duplicating

Everything lands as status 'inbox'. That is deliberate: these have not been
triaged in this system, and putting 78 unvetted items straight into the actionable
pool would make the first fortnight of suggestions worthless.

    python3 load_google_tasks.py Tasks.json --plan task_plan.json --dry-run
    python3 load_google_tasks.py Tasks.json --plan task_plan.json
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from pa import repo                      # noqa: E402
from pa.db import get_db                 # noqa: E402

NOT_TASK_LISTS = {"Travel", "Trip checklist", "Camping", "To see", "Places to Fish"}
JUNK = [re.compile(p, re.I) for p in (r"Kat's Pet Care", r"^Re:\s*\[", r"new submission")]


def is_junk(t: str) -> bool:
    return any(p.search(t) for p in JUNK)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("export")
    ap.add_argument("--plan", default=str(pathlib.Path(__file__).parent / "task_plan.json"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    plan = json.loads(pathlib.Path(a.plan).read_text())
    tasks_plan = {k.casefold(): v for k, v in plan["_tasks"].items()}
    drops = {k.casefold(): v for k, v in plan["_drop"].items()}

    data = json.loads(pathlib.Path(a.export).read_text())

    best: dict[str, dict] = {}
    stats = collections.Counter()
    reference = 0

    for L in data["items"]:
        lname = L.get("title") or "Google Tasks"
        for t in L.get("items") or []:
            if t.get("deleted"):
                stats["deleted"] += 1;  continue
            if t.get("status") == "completed":
                stats["completed"] += 1; continue
            title = (t.get("title") or "").strip()
            if not title:
                stats["untitled"] += 1; continue
            if is_junk(title):
                stats["junk"] += 1; continue
            if lname in NOT_TASK_LISTS:
                reference += 1; continue
            key = title.casefold()
            prev = best.get(key)
            if prev is None:
                best[key] = t | {"_list": lname, "_title": title}
            else:
                stats["duplicate"] += 1
                if (t.get("updated") or "") > (prev.get("updated") or ""):
                    best[key] = t | {"_list": lname, "_title": title}

    # Refuse to guess. Every surviving task must have an explicit decision.
    unplanned = [v["_title"] for k, v in best.items()
                 if k not in tasks_plan and k not in drops]
    if unplanned:
        print("REFUSING TO RUN — these open tasks have no decision in the plan:")
        for t in sorted(unplanned):
            print(f"  {t}")
        print("\nAdd them to _tasks or _drop in the plan and re-run.")
        return 2

    to_load = [(k, v) for k, v in best.items() if k in tasks_plan]
    dropped = [(k, v) for k, v in best.items() if k in drops]

    print(f"export: {a.export}")
    print(f"  completed skipped:        {stats['completed']}")
    print(f"  junk skipped:             {stats['junk']}")
    print(f"  duplicates collapsed:     {stats['duplicate']}")
    print(f"  reference lists set aside:{reference}  (already in the context store)")
    print(f"  dropped by plan:          {len(dropped)}")
    print(f"  TO IMPORT:                {len(to_load)}")
    by_proj = collections.Counter(tasks_plan[k]["project"] for k, _ in to_load)
    for p, n in by_proj.most_common():
        print(f"      {n:>4}  {p}")

    if a.dry_run:
        print("\n(dry run — nothing written)")
        return 0

    db = get_db()
    created = updated = 0
    with db.transaction():
        for key, t in sorted(to_load, key=lambda x: x[1]["_title"]):
            d = tasks_plan[key]
            gid = t.get("id")
            existing = db.row(
                "select id from pa.task where source = 'google_tasks' and source_id = %s",
                (gid,)) if gid else None

            notes = "\n".join(x for x in (t.get("notes"), d.get("notes")) if x) or None
            if existing:
                repo.task_update(
                    existing["id"], project=d["project"], notes=notes,
                    importance=d.get("importance", 3),
                    effort_minutes=d.get("effort_minutes"), energy=d.get("energy"),
                    contexts=d.get("contexts"), replace_contexts=True, db=db,
                )
                updated += 1
            else:
                repo.task_add(
                    title=t["_title"], project=d["project"], notes=notes,
                    status="inbox", importance=d.get("importance", 3),
                    effort_minutes=d.get("effort_minutes"), energy=d.get("energy"),
                    contexts=d.get("contexts"), person=d.get("person"),
                    due_on=(t.get("due") or "")[:10] or None,
                    source="google_tasks", source_id=gid, db=db,
                )
                created += 1

    print(f"\ncreated {created}, updated {updated}")
    print("All at status 'inbox'. Triage from the daily brief.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
