"""Import Google Tasks into pa.task.

Accepts what Google actually gives you, which is three different shapes
depending on route:

  1. **Takeout** (`Tasks/Tasks.json`) — `{"items": [ <list>, ... ]}` where each
     list has its own `items` array of tasks. This is the usual route and needs
     no API credentials.
  2. **Tasks API** `tasklists().list()` + `tasks().list()` output saved as
     `{"<list name>": [ <task>, ... ]}` or a bare list of tasks.
  3. A flat JSON array of task objects.

Three details that decide whether the import is right or subtly wrong:

* **Due dates must not be timezone-converted.** Google stores a due date as
  `2026-09-20T00:00:00.000Z` and means *the 20th*, not midnight UTC. Parsing it
  as an instant and converting to America/New_York silently moves every due date
  back a day. So the date is taken from the string's first ten characters and the
  time component is discarded, which is what Google's own clients do.

* **Imports are idempotent**, keyed on (source, source_id) — the Google task id.
  Re-running after exporting again updates rather than duplicates, so a partial
  or interrupted import is safe to repeat.

* **Open tasks land in `inbox`, not `next`.** They have not been triaged in the
  new system, and dropping several hundred untouched items straight into the
  actionable list would make the first week's suggestions useless. Inbox is the
  honest status, and the brief will keep prompting until it is empty.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any, Iterable

from ..db import Db, get_db

IMPORT_AREA_SLUG = "imported"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _due_date(raw: str | None) -> str | None:
    """'2026-09-20T00:00:00.000Z' -> '2026-09-20'. No timezone maths, by design."""
    if not raw:
        return None
    return str(raw)[:10] or None


def _walk(payload: Any) -> Iterable[tuple[str, dict]]:
    """Yield (list_name, task) pairs from any of the supported shapes."""
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        items = payload["items"]
        # Takeout: outer items are lists, each with its own inner items.
        if items and any(isinstance(i, dict) and isinstance(i.get("items"), list) for i in items):
            for lst in items:
                name = lst.get("title") or lst.get("id") or "Google Tasks"
                for t in lst.get("items") or []:
                    yield name, t
            return
        for t in items:                       # API: flat items of one list
            yield payload.get("title") or "Google Tasks", t
        return

    if isinstance(payload, dict):             # {"list name": [tasks...]}
        for name, tasks in payload.items():
            if isinstance(tasks, list):
                for t in tasks:
                    yield str(name), t
        return

    if isinstance(payload, list):
        for t in payload:
            if isinstance(t, dict):
                yield "Google Tasks", t


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def import_file(
    path: str | pathlib.Path,
    list_map: dict[str, Any] | None = None,
    default_parent: str = IMPORT_AREA_SLUG,
    status_for_open: str = "inbox",
    include_completed: bool = True,
    dry_run: bool = False,
    db: Db | None = None,
) -> dict[str, Any]:
    """Import a Google Tasks export.

    list_map routes a Google list onto an existing project, e.g.
    {"Garden": "native-garden", "Money": "finances"}. Anything unmapped becomes a
    project of its own beneath `default_parent`, which is created if absent —
    better than silently merging distinct lists into one bucket.
    """
    db = db or get_db()
    payload = json.loads(pathlib.Path(path).read_text())
    pairs = list(_walk(payload))
    if not pairs:
        return {"error": "No tasks found. Expected Takeout Tasks.json or an API dump.",
                "inspected_keys": list(payload)[:10] if isinstance(payload, dict) else None}

    list_map = {k.strip().lower(): v for k, v in (list_map or {}).items()}
    report: dict[str, Any] = {
        "file": str(path), "found": len(pairs), "created": 0, "updated": 0,
        "skipped_completed": 0, "skipped_deleted": 0, "lists": {}, "projects_created": [],
        "dry_run": dry_run,
    }

    if dry_run:
        for list_name, t in pairs:
            report["lists"].setdefault(list_name, 0)
            report["lists"][list_name] += 1
        return report

    from .. import repo  # local import to avoid a cycle at module load

    with db.transaction():
        parent_id = _ensure_parent(db, default_parent)
        project_ids: dict[str, int | None] = {}
        id_map: dict[str, int] = {}          # google task id -> pa task id
        deferred_parents: list[tuple[int, str]] = []

        for list_name, t in pairs:
            report["lists"].setdefault(list_name, 0)
            report["lists"][list_name] += 1

            if t.get("deleted"):
                report["skipped_deleted"] += 1
                continue
            completed = (t.get("status") == "completed") or bool(t.get("completed"))
            if completed and not include_completed:
                report["skipped_completed"] += 1
                continue

            key = list_name.strip().lower()
            if key not in project_ids:
                project_ids[key] = _project_for_list(db, list_name, list_map, parent_id, report)
            pid = project_ids[key]

            gid = t.get("id")
            title = (t.get("title") or "").strip() or "(untitled)"
            notes = t.get("notes")
            due = _due_date(t.get("due"))
            status = "done" if completed else status_for_open

            existing = db.row(
                "select id from pa.task where source = 'google_tasks' and source_id = %s", (gid,)
            ) if gid else None

            if existing:
                db.execute(
                    """update pa.task
                          set title = %s, notes = coalesce(%s, notes), due_on = %s,
                              status = %s, project_id = coalesce(project_id, %s)
                        where id = %s""",
                    (title, notes, due, status, pid, existing["id"]),
                )
                tid = existing["id"]
                report["updated"] += 1
            else:
                row = db.row(
                    """insert into pa.task
                         (title, notes, project_id, status, due_on, source, source_id, metadata)
                       values (%s,%s,%s,%s,%s,'google_tasks',%s,%s)
                       returning id""",
                    (title, notes, pid, status, due, gid,
                     json.dumps({"google_list": list_name,
                                 "google_position": t.get("position"),
                                 "google_updated": t.get("updated")})),
                )
                tid = row["id"]
                report["created"] += 1

            if completed and t.get("completed"):
                db.execute("update pa.task set completed_at = %s where id = %s",
                           (t["completed"], tid))

            if gid:
                id_map[gid] = tid
            if t.get("parent"):
                deferred_parents.append((tid, t["parent"]))

        # Subtask links, once every id is known.
        linked = 0
        for child_id, google_parent in deferred_parents:
            parent = id_map.get(google_parent)
            if parent and parent != child_id:
                db.execute("update pa.task set parent_task_id = %s where id = %s",
                           (parent, child_id))
                linked += 1
        report["subtasks_linked"] = linked

    report["note"] = (
        "Open tasks imported as 'inbox' so they are queued for triage rather than "
        "treated as vetted next actions. Google Tasks does not export recurrence "
        "rules, so any repeating items came across as single tasks — recreate those "
        "with pa_recurrence_add, where fixed vs after-completion can be stated properly."
    )
    return report


def _ensure_parent(db: Db, slug_or_ref: str) -> int:
    row = db.row("select id from pa.project where slug = %s or lower(name) = lower(%s)",
                 (slug_or_ref, slug_or_ref))
    if row:
        return row["id"]
    row = db.row(
        """insert into pa.project (name, slug, kind, status, importance, description, metadata)
           values ('Imported', %s, 'area', 'active', 2,
                   'Landing area for imported lists. Refile into real projects, then delete.',
                   '{"imported": true}'::jsonb)
           returning id""",
        (slug_or_ref,),
    )
    return row["id"]


def _project_for_list(db: Db, list_name: str, list_map: dict[str, Any],
                      parent_id: int, report: dict) -> int | None:
    mapped = list_map.get(list_name.strip().lower())
    if mapped is not None:
        row = db.row(
            "select id from pa.project where slug = %s or lower(name) = lower(%s) or id::text = %s",
            (str(mapped), str(mapped), str(mapped)),
        )
        if row:
            return row["id"]

    row = db.row(
        "select id from pa.project where lower(name) = lower(%s) and parent_id = %s",
        (list_name, parent_id),
    )
    if row:
        return row["id"]

    row = db.row(
        """insert into pa.project (name, kind, parent_id, status, importance, description, metadata)
           values (%s, 'project', %s, 'active', 3, %s, '{"imported": true}'::jsonb)
           returning id""",
        (list_name, parent_id, f"Imported from the Google Tasks list '{list_name}'."),
    )
    report["projects_created"].append(list_name)
    return row["id"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Import a Google Tasks export into pa.task")
    ap.add_argument("path")
    ap.add_argument("--map", action="append", default=[],
                    metavar="LIST=PROJECT",
                    help="Route a Google list to an existing project. Repeatable.")
    ap.add_argument("--parent", default=IMPORT_AREA_SLUG,
                    help="Where unmapped lists get created (default: the 'Imported' area)")
    ap.add_argument("--status", default="inbox", help="Status for open tasks (default: inbox)")
    ap.add_argument("--no-completed", action="store_true", help="Skip already-completed tasks")
    ap.add_argument("--dry-run", action="store_true", help="Report what would happen and stop")
    a = ap.parse_args()

    mapping = dict(m.split("=", 1) for m in a.map)
    out = import_file(a.path, list_map=mapping, default_parent=a.parent,
                      status_for_open=a.status, include_completed=not a.no_completed,
                      dry_run=a.dry_run)
    print(json.dumps(out, indent=2, ensure_ascii=False))
