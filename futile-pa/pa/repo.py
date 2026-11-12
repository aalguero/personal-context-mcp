"""Query and command functions over the `pa` schema.

Every MCP tool is a thin wrapper around something here, so this module is where
the behaviour actually lives and where it can be tested without an MCP server in
the picture.

Two conventions run through it:

1. **References accept names, slugs or ids.** A model calling `task_add` should
   be able to say `project="native garden"` rather than looking up id 11 first.
   Resolution is exact-id, then slug, then case-insensitive name, then a single
   unambiguous substring match — and an ambiguous match raises rather than
   guessing, because silently filing a task under the wrong project is worse
   than an error message.

2. **Writes go through SQL functions where the operation has consequences.**
   Completion touches recurrence rollover; deferral touches the counter and the
   log. Putting that in the database rather than here means it behaves the same
   whether it is called by a tool, by a script, or by hand in psql.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Iterable, Sequence

import psycopg

from .db import Db, get_db

# Columns a caller may set on a task. Everything else — completed_at,
# defer_count, timestamps — is maintained by the database.
#
# defer_until is deliberately absent (task 227). Pushing it forward is a
# deferral, and a deferral goes through pa.defer_task, which moves a soft
# due_on with it and refuses a hard deadline. Through update it was a side
# door around both. task_update still accepts defer_until=None, because
# clearing a deferral only makes a task more visible and needs no guard.
TASK_WRITABLE = (
    "title", "notes", "status", "importance", "effort_minutes", "energy",
    "due_on", "due_at", "hard_deadline", "scheduled_for",
    "waiting_on_note", "project_id", "person_id", "waiting_on_person_id",
    "parent_task_id", "external_url",
)

PROJECT_WRITABLE = (
    "name", "kind", "parent_id", "status", "horizon", "importance",
    "description", "target_date", "started_on", "completed_on",
    "review_cadence_days", "last_reviewed_on", "notes", "person_id", "slug",
)

PERSON_WRITABLE = (
    "name", "relationship", "household", "location", "email", "phone",
    "birthday", "contact_cadence_days", "last_contact_on", "has_plan",
    "notes", "slug",
)


class Ambiguous(ValueError):
    pass


class NotFound(ValueError):
    pass


# ---------------------------------------------------------------------------
# Reference resolution
# ---------------------------------------------------------------------------

def _resolve(db: Db, table: str, ref: Any, label: str) -> int | None:
    """Turn a user-supplied reference into a primary key.

    Order matters: an integer is taken at face value, then slug, then exact
    name, then a unique substring. The substring step is what makes
    `project="garden"` work, and the Ambiguous exception is what stops it
    quietly picking one of three gardens.
    """
    if ref is None or ref == "":
        return None
    if isinstance(ref, bool):
        return None
    if isinstance(ref, int):
        return ref
    s = str(ref).strip()
    if s.isdigit():
        return int(s)

    row = db.row(f"select id from pa.{table} where slug = %s", (s,))
    if row:
        return row["id"]
    row = db.row(f"select id from pa.{table} where lower(name) = lower(%s)", (s,))
    if row:
        return row["id"]

    hits = db.rows(
        f"select id, name from pa.{table} where name ilike %s order by length(name) limit 5",
        (f"%{s}%",),
    )
    if len(hits) == 1:
        return hits[0]["id"]
    if len(hits) > 1:
        names = ", ".join(f"{h['name']} (#{h['id']})" for h in hits)
        raise Ambiguous(f"{label} '{s}' matches several: {names}. Use the id.")
    raise NotFound(f"No {label} matching '{s}'.")


def resolve_project(db: Db, ref: Any) -> int | None:
    return _resolve(db, "project", ref, "project")


def resolve_person(db: Db, ref: Any) -> int | None:
    return _resolve(db, "person", ref, "person")


def resolve_contexts(db: Db, names: Iterable[str] | None, create: bool = True) -> list[int]:
    """Map context names to ids, optionally creating unknown ones.

    Creating on demand is the right default: the vocabulary of contexts is
    discovered by use, and refusing a task because '@orchard' does not exist yet
    would train you to stop tagging. Anything auto-created is marked in its
    description so a later tidy-up can find it.
    """
    if not names:
        return []
    out: list[int] = []
    for raw in names:
        n = str(raw).strip().lstrip("@").lower().replace(" ", "_")
        if not n:
            continue
        row = db.row("select id from pa.context where name = %s", (n,))
        if row:
            out.append(row["id"])
        elif create:
            row = db.row(
                """insert into pa.context(name, label, kind, description)
                   values (%s, %s, 'other', 'auto-created on first use')
                   on conflict (name) do update set name = excluded.name
                   returning id""",
                (n, "@" + n),
            )
            out.append(row["id"])
    return out


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

def task_add(
    title: str,
    project: Any = None,
    notes: str | None = None,
    status: str = "next",
    importance: int | None = None,
    effort_minutes: int | None = None,
    energy: str | None = None,
    due_on: str | None = None,
    due_at: str | None = None,
    hard_deadline: bool = False,
    defer_until: str | None = None,
    scheduled_for: str | None = None,
    contexts: Sequence[str] | None = None,
    person: Any = None,
    waiting_on: Any = None,
    parent_task_id: int | None = None,
    depends_on: Sequence[int] | None = None,
    source: str = "claude",
    source_id: str | None = None,
    external_url: str | None = None,
    db: Db | None = None,
) -> dict:
    db = db or get_db()
    project_id = resolve_project(db, project)
    person_id = resolve_person(db, person)
    waiting_id = resolve_person(db, waiting_on)
    if waiting_id and status == "next":
        status = "waiting"

    # One transaction: a task and its tags either both land or neither does.
    with db.transaction():
        try:
            row = db.row(
                """
                insert into pa.task
                  (title, notes, project_id, person_id, waiting_on_person_id, status,
                   importance, effort_minutes, energy, due_on, due_at, hard_deadline,
                   defer_until, scheduled_for, parent_task_id, source, source_id, external_url)
                values (%s,%s,%s,%s,%s,%s,coalesce(%s,3),%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                returning id
                """,
                (title, notes, project_id, person_id, waiting_id, status, importance,
                 effort_minutes, energy, due_on, due_at, hard_deadline, defer_until,
                 scheduled_for, parent_task_id, source, source_id, external_url),
            )
        except psycopg.errors.InvalidParameterValue as exc:
            # The hard-deadline guard (migration 007): a refusal the caller can act on.
            raise ValueError(exc.diag.message_primary or str(exc)) from None
        tid = row["id"]

        for cid in resolve_contexts(db, contexts):
            db.execute(
                "insert into pa.task_context(task_id, context_id) values (%s,%s) on conflict do nothing",
                (tid, cid),
            )
        for dep in depends_on or []:
            db.execute(
                "insert into pa.task_dependency(task_id, depends_on_task_id) values (%s,%s) on conflict do nothing",
                (tid, int(dep)),
            )
        return task_get(tid, db=db)


def task_get(task_id: int, db: Db | None = None) -> dict:
    db = db or get_db()
    row = db.row("select * from pa.v_task where id = %s", (task_id,))
    if not row:
        raise NotFound(f"No task {task_id}")
    return row


def task_update(
    task_id: int,
    contexts: Sequence[str] | None = None,
    replace_contexts: bool = False,
    project: Any = None,
    person: Any = None,
    waiting_on: Any = None,
    db: Db | None = None,
    **fields: Any,
) -> dict:
    db = db or get_db()
    sets: list[str] = []
    params: list[Any] = []

    if project is not None:
        fields["project_id"] = resolve_project(db, project)
    if person is not None:
        fields["person_id"] = resolve_person(db, person)
    if waiting_on is not None:
        fields["waiting_on_person_id"] = resolve_person(db, waiting_on)

    for k, v in fields.items():
        if k == "defer_until":
            if v is not None:
                raise ValueError(
                    "defer_until cannot be set by an update. Use pa_task_defer, which records "
                    "the deferral, moves a soft due date with it and refuses a hard deadline. "
                    "Passing defer_until=null here clears a deferral.")
            sets.append("defer_until = null")
            continue
        if k not in TASK_WRITABLE:
            raise ValueError(f"Field '{k}' is not writable. Allowed: {', '.join(TASK_WRITABLE)}")
        sets.append(f"{k} = %s")
        params.append(v)

    if sets:
        params.append(task_id)
        try:
            n = db.execute(f"update pa.task set {', '.join(sets)} where id = %s", params)
        except psycopg.errors.InvalidParameterValue as exc:
            # The hard-deadline guard (migration 007): e.g. status=someday on a hard deadline.
            raise ValueError(exc.diag.message_primary or str(exc)) from None
        if n == 0:
            raise NotFound(f"No task {task_id}")

    if contexts is not None:
        if replace_contexts:
            db.execute("delete from pa.task_context where task_id = %s", (task_id,))
        for cid in resolve_contexts(db, contexts):
            db.execute(
                "insert into pa.task_context(task_id, context_id) values (%s,%s) on conflict do nothing",
                (task_id, cid),
            )
    return task_get(task_id, db=db)


def task_complete(task_id: int, note: str | None = None, db: Db | None = None) -> dict:
    """Complete a task; the SQL function handles recurrence rollover."""
    db = db or get_db()
    res = db.row("select * from pa.complete_task(%s, %s)", (task_id, note))
    out = {"completed": task_get(task_id, db=db)}
    if res and res.get("next_task_id"):
        out["next_occurrence"] = task_get(res["next_task_id"], db=db)
    return out


def task_defer(task_id: int, until: str, reason: str | None = None, db: Db | None = None) -> dict:
    """Deferring a soft-dated task moves its due_on; a hard deadline or a date
    that is not in the future is refused by pa.defer_task (migration 005).
    Those refusals come back as ValueError, so dispatch() returns them as
    {"error": ...} that the caller -- often the check-in agent -- can act on."""
    db = db or get_db()
    try:
        db.row("select pa.defer_task(%s, %s, %s)", (task_id, until, reason))
    except psycopg.errors.InvalidParameterValue as exc:
        raise ValueError(exc.diag.message_primary or str(exc)) from None
    return task_get(task_id, db=db)


def task_drop(task_id: int, reason: str, db: Db | None = None) -> dict:
    db = db or get_db()
    n = db.execute(
        "update pa.task set status = 'dropped', dropped_reason = %s where id = %s",
        (reason, task_id),
    )
    if n == 0:
        raise NotFound(f"No task {task_id}")
    return task_get(task_id, db=db)


def task_list(
    status: Sequence[str] | str | None = None,
    project: Any = None,
    include_subtree: bool = True,
    contexts: Sequence[str] | None = None,
    person: Any = None,
    due_before: str | None = None,
    due_after: str | None = None,
    search: str | None = None,
    unfiled: bool = False,
    include_deferred: bool = False,
    open_only: bool = True,
    order: str = "score",
    limit: int = 50,
    db: Db | None = None,
) -> list[dict]:
    """Filtered task query.

    Reads from v_scored when ordering by score (it excludes blocked, deferred
    and non-actionable rows by construction) and from v_task otherwise, so that
    asking for `status=["waiting"]` actually returns waiting tasks rather than an
    empty list.
    """
    db = db or get_db()
    statuses = [status] if isinstance(status, str) else list(status or [])

    needs_full_view = bool(statuses and set(statuses) - {"inbox", "next", "scheduled", "in_progress"})
    view = "pa.v_task" if (needs_full_view or order != "score" or include_deferred or not open_only) else "pa.v_scored"

    where: list[str] = []
    params: list[Any] = []

    if statuses:
        where.append("status = any(%s)")
        params.append(statuses)
    elif open_only:
        where.append("status not in ('done','dropped')")

    if unfiled:
        where.append("project_id is null")
    elif project is not None:
        pid = resolve_project(db, project)
        if include_subtree:
            where.append("project_id in (select id from pa.project_subtree(%s))")
        else:
            where.append("project_id = %s")
        params.append(pid)

    if contexts:
        where.append("contexts @> %s")
        params.append([str(c).lstrip("@").lower().replace(" ", "_") for c in contexts])

    if person is not None:
        where.append("(person_id = %s or waiting_on_person_id = %s)")
        pid = resolve_person(db, person)
        params += [pid, pid]

    if due_before:
        where.append("due_on <= %s")
        params.append(due_before)
    if due_after:
        where.append("due_on >= %s")
        params.append(due_after)
    if search:
        where.append("(title ilike %s or notes ilike %s)")
        params += [f"%{search}%", f"%{search}%"]
    if view == "pa.v_task" and not include_deferred and open_only:
        where.append("not is_deferred")

    order_sql = {
        "score": "score desc nulls last",
        "due": "due_on asc nulls last, importance desc",
        "importance": "importance desc, due_on asc nulls last",
        "created": "created_at desc",
        "updated": "updated_at desc",
        "effort": "effort_minutes asc nulls last",
    }.get(order, "id desc")
    if view == "pa.v_task" and order == "score":
        order_sql = "importance desc, due_on asc nulls last"

    sql = f"select * from {view}"
    if where:
        sql += " where " + " and ".join(where)
    sql += f" order by {order_sql} limit %s"
    params.append(min(int(limit), 300))
    return db.rows(sql, params)


def task_history(task_id: int, limit: int = 30, db: Db | None = None) -> list[dict]:
    db = db or get_db()
    return db.rows(
        """select at, actor, action, from_value, to_value, note, payload
             from pa.event
            where entity_type = 'task' and entity_id = %s
            order by at desc limit %s""",
        (task_id, limit),
    )


# ---------------------------------------------------------------------------
# Suggestion — the "what should I do right now" path
# ---------------------------------------------------------------------------

def suggest(
    situation: str | None = None,
    minutes: int | None = None,
    energy: str | None = None,
    contexts: Sequence[str] | None = None,
    include_untagged: bool = True,
    limit: int = 10,
    db: Db | None = None,
) -> dict:
    db = db or get_db()
    ctx = [str(c).lstrip("@").lower().replace(" ", "_") for c in contexts] if contexts else None
    rows = db.rows(
        "select * from pa.suggest_tasks(%s,%s,%s,%s,%s,%s)",
        (situation, minutes, energy, ctx, include_untagged, limit),
    )
    sit = None
    if situation:
        sit = db.row(
            """select s.name, s.label, s.typical_minutes, s.energy_available,
                      (select array_agg(c.name order by c.name)
                         from pa.situation_context sc join pa.context c on c.id = sc.context_id
                        where sc.situation_id = s.id) as provides
                 from pa.situation s where s.name = %s""",
            (situation,),
        )
    return {"situation": sit, "asked": {"minutes": minutes, "energy": energy, "contexts": ctx},
            "suggestions": rows}


def situations(db: Db | None = None) -> list[dict]:
    db = db or get_db()
    return db.rows(
        """select s.name, s.label, s.description, s.typical_minutes, s.min_minutes,
                  s.max_minutes, s.energy_available, s.notes,
                  (select array_agg(c.name order by c.name)
                     from pa.situation_context sc join pa.context c on c.id = sc.context_id
                    where sc.situation_id = s.id) as provides
             from pa.situation s order by s.name"""
    )


def contexts_list(db: Db | None = None) -> list[dict]:
    db = db or get_db()
    return db.rows(
        """select c.name, c.label, c.kind, c.description,
                  (select count(*) from pa.task_context tc
                     join pa.task t on t.id = tc.task_id
                    where tc.context_id = c.id and t.status not in ('done','dropped')) as open_tasks
             from pa.context c order by c.kind, c.name"""
    )


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

def project_add(
    name: str,
    kind: str = "project",
    parent: Any = None,
    status: str = "active",
    horizon: str | None = None,
    importance: int = 3,
    description: str | None = None,
    target_date: str | None = None,
    review_cadence_days: int | None = None,
    person: Any = None,
    slug: str | None = None,
    db: Db | None = None,
) -> dict:
    db = db or get_db()
    row = db.row(
        """insert into pa.project
             (name, slug, kind, parent_id, status, horizon, importance, description,
              target_date, review_cadence_days, person_id)
           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
        (name, slug, kind, resolve_project(db, parent), status, horizon, importance,
         description, target_date, review_cadence_days, resolve_person(db, person)),
    )
    return project_get(row["id"], db=db)


def project_get(ref: Any, db: Db | None = None) -> dict:
    db = db or get_db()
    pid = resolve_project(db, ref)
    row = db.row("select * from pa.v_project_health where id = %s", (pid,))
    if not row:
        raise NotFound(f"No project {ref}")
    row["goals"] = db.rows(
        """select g.id, g.name, g.horizon, g.target_date, gp.contribution
             from pa.goal_project gp join pa.goal g on g.id = gp.goal_id
            where gp.project_id = %s""",
        (pid,),
    )
    return row


def project_update(ref: Any, parent: Any = None, person: Any = None,
                   db: Db | None = None, **fields: Any) -> dict:
    db = db or get_db()
    pid = resolve_project(db, ref)
    if parent is not None:
        fields["parent_id"] = resolve_project(db, parent)
    if person is not None:
        fields["person_id"] = resolve_person(db, person)
    sets, params = [], []
    for k, v in fields.items():
        if k not in PROJECT_WRITABLE:
            raise ValueError(f"Field '{k}' is not writable on project.")
        sets.append(f"{k} = %s")
        params.append(v)
    if sets:
        params.append(pid)
        db.execute(f"update pa.project set {', '.join(sets)} where id = %s", params)
    return project_get(pid, db=db)


def project_tree(root: Any = None, include_done: bool = False, db: Db | None = None) -> list[dict]:
    """Flat list with a depth column — easier for a model to render than nesting."""
    db = db or get_db()
    where = "" if include_done else " and p.status not in ('done','abandoned')"
    if root is not None:
        rid = resolve_project(db, root)
        return db.rows(
            f"""select s.depth, h.*
                  from pa.project_subtree(%s) s
                  join pa.v_project_health h on h.id = s.id
                  join pa.project p on p.id = s.id
                 where true {where}
                 order by pa.project_path(h.id)""",
            (rid,),
        )
    return db.rows(
        f"""select (length(pa.project_path(p.id)) - length(replace(pa.project_path(p.id), ' / ', ''))) / 3 as depth,
                   h.*
              from pa.project p join pa.v_project_health h on h.id = p.id
             where true {where}
             order by pa.project_path(p.id)"""
    )


def project_review(ref: Any, on: str | None = None, note: str | None = None,
                   db: Db | None = None) -> dict:
    """Record that a project was reviewed, resetting its review clock."""
    db = db or get_db()
    pid = resolve_project(db, ref)
    db.execute("update pa.project set last_reviewed_on = coalesce(%s, current_date) where id = %s",
               (on, pid))
    if note:
        db.execute(
            """insert into pa.event(actor, entity_type, entity_id, action, note)
               values (pa.current_actor(), 'project', %s, 'note', %s)""",
            (pid, note),
        )
    return project_get(pid, db=db)


def stalled_projects(db: Db | None = None) -> list[dict]:
    db = db or get_db()
    return db.rows("select * from pa.v_stalled_projects")


# ---------------------------------------------------------------------------
# People and connection
# ---------------------------------------------------------------------------

def person_add(name: str, relationship: str | None = None, db: Db | None = None,
               **fields: Any) -> dict:
    db = db or get_db()
    cols = {"name": name, "relationship": relationship}
    for k, v in fields.items():
        if k not in PERSON_WRITABLE:
            raise ValueError(f"Field '{k}' is not writable on person.")
        cols[k] = v
    keys = list(cols)
    row = db.row(
        f"insert into pa.person ({', '.join(keys)}) values ({', '.join(['%s'] * len(keys))}) returning id",
        [cols[k] for k in keys],
    )
    return person_get(row["id"], db=db)


def person_update(ref: Any, db: Db | None = None, **fields: Any) -> dict:
    db = db or get_db()
    pid = resolve_person(db, ref)
    sets, params = [], []
    for k, v in fields.items():
        if k not in PERSON_WRITABLE:
            raise ValueError(f"Field '{k}' is not writable on person.")
        sets.append(f"{k} = %s")
        params.append(v)
    if sets:
        params.append(pid)
        db.execute(f"update pa.person set {', '.join(sets)} where id = %s", params)
    return person_get(pid, db=db)


def person_get(ref: Any, db: Db | None = None) -> dict:
    db = db or get_db()
    pid = resolve_person(db, ref)
    row = db.row("select * from pa.person where id = %s", (pid,))
    if not row:
        raise NotFound(f"No person {ref}")
    row["open_tasks"] = db.rows(
        """select id, title, status, due_on from pa.v_task
            where (person_id = %s or waiting_on_person_id = %s)
              and status not in ('done','dropped') order by due_on nulls last limit 20""",
        (pid, pid),
    )
    row["plan"] = db.row(
        "select id, name, status, horizon from pa.project where person_id = %s and kind = 'person_plan'",
        (pid,),
    )
    row["recent_contact"] = db.rows(
        """select at, to_value as contacted_on, note, payload from pa.event
            where entity_type = 'person' and entity_id = %s and action = 'contacted'
            order by at desc limit 5""",
        (pid,),
    )
    return row


def log_contact(person: Any, on: str | None = None, how: str | None = None,
                note: str | None = None, db: Db | None = None) -> dict:
    db = db or get_db()
    pid = resolve_person(db, person)
    db.row("select pa.log_contact(%s, coalesce(%s::date, current_date), %s, %s)",
           (pid, on, how, note))
    return person_get(pid, db=db)


def connection_due(db: Db | None = None) -> list[dict]:
    db = db or get_db()
    return db.rows("select * from pa.v_connection_due"
                   " order by never_logged desc, overdue_days desc nulls last, id")


def person_list(due_only: bool = False, db: Db | None = None) -> list[dict]:
    """Everyone, or only those past their contact cadence."""
    db = db or get_db()
    if due_only:
        return connection_due(db=db)
    return db.rows(
        """select pe.id, pe.name, pe.slug, pe.relationship, pe.household,
                  pe.contact_cadence_days, pe.last_contact_on, pe.has_plan, pe.notes,
                  case when pe.contact_cadence_days is null or pe.last_contact_on is null then null
                       else current_date - (pe.last_contact_on + pe.contact_cadence_days) end as overdue_days,
                  (select count(*) from pa.task t
                    where (t.person_id = pe.id or t.waiting_on_person_id = pe.id)
                      and t.status not in ('done','dropped')) as open_tasks
             from pa.person pe
            order by pe.relationship nulls last, pe.name"""
    )


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------

def goal_add(name: str, horizon: str | None = None, description: str | None = None,
             target_date: str | None = None, measure: str | None = None,
             importance: int = 3, person: Any = None, projects: Sequence[Any] | None = None,
             db: Db | None = None) -> dict:
    db = db or get_db()
    row = db.row(
        """insert into pa.goal (name, description, horizon, target_date, measure, importance, person_id)
           values (%s,%s,%s,%s,%s,%s,%s) returning id""",
        (name, description, horizon, target_date, measure, importance, resolve_person(db, person)),
    )
    gid = row["id"]
    for p in projects or []:
        db.execute(
            "insert into pa.goal_project(goal_id, project_id) values (%s,%s) on conflict do nothing",
            (gid, resolve_project(db, p)),
        )
    return goal_list(goal_id=gid, db=db)[0]


def goal_list(horizon: str | None = None, status: str = "active",
              goal_id: int | None = None, db: Db | None = None) -> list[dict]:
    db = db or get_db()
    where, params = ["1=1"], []
    if goal_id:
        where.append("g.id = %s")
        params.append(goal_id)
    else:
        if status:
            where.append("g.status = %s")
            params.append(status)
        if horizon:
            where.append("g.horizon = %s")
            params.append(horizon)
    return db.rows(
        f"""select g.*,
                  (select array_agg(p.name) from pa.goal_project gp
                     join pa.project p on p.id = gp.project_id where gp.goal_id = g.id) as projects,
                  (select count(*) from pa.goal_project gp
                     join pa.v_actionable a on a.project_id = gp.project_id
                    where gp.goal_id = g.id) as actionable_tasks
             from pa.goal g where {' and '.join(where)}
            order by case g.horizon when 'short' then 1 when 'medium' then 2 else 3 end,
                     g.importance desc""",
        params,
    )


# ---------------------------------------------------------------------------
# Recurrence
# ---------------------------------------------------------------------------

def recurrence_add(
    title: str,
    mode: str = "fixed",
    rrule: str | None = None,
    interval_days: int | None = None,
    project: Any = None,
    person: Any = None,
    notes: str | None = None,
    dtstart: str | None = None,
    until_date: str | None = None,
    lead_time_days: int = 0,
    effort_minutes: int | None = None,
    energy: str | None = None,
    importance: int = 3,
    hard_deadline: bool = False,
    contexts: Sequence[str] | None = None,
    db: Db | None = None,
) -> dict:
    db = db or get_db()
    row = db.row(
        """insert into pa.recurrence
             (title, notes, project_id, person_id, mode, rrule, interval_days,
              dtstart, until_date, lead_time_days, effort_minutes, energy,
              importance, hard_deadline)
           values (%s,%s,%s,%s,%s,%s,%s,coalesce(%s::date, current_date),%s,%s,%s,%s,%s,%s)
           returning id""",
        (title, notes, resolve_project(db, project), resolve_person(db, person), mode,
         rrule, interval_days, dtstart, until_date, lead_time_days, effort_minutes,
         energy, importance, hard_deadline),
    )
    rid = row["id"]
    for cid in resolve_contexts(db, contexts):
        db.execute(
            "insert into pa.recurrence_context(recurrence_id, context_id) values (%s,%s) on conflict do nothing",
            (rid, cid),
        )
    return db.row("select * from pa.recurrence where id = %s", (rid,))


def recurrence_list(active_only: bool = True, db: Db | None = None) -> list[dict]:
    db = db or get_db()
    where = "where active" if active_only else ""
    return db.rows(
        f"""select r.*, p.name as project_name,
                   (select array_agg(c.name) from pa.recurrence_context rc
                      join pa.context c on c.id = rc.context_id where rc.recurrence_id = r.id) as contexts
              from pa.recurrence r left join pa.project p on p.id = r.project_id
              {where} order by r.title"""
    )


# ---------------------------------------------------------------------------
# Daily brief
# ---------------------------------------------------------------------------

def daily_brief(for_date: str | None = None, horizon_days: int = 7,
                record: bool = True, db: Db | None = None) -> dict:
    """Everything the morning summary needs, in one round trip.

    Composed as one call rather than ten tool calls because a brief that takes
    ten round trips will not be generated every morning, and one that is not
    generated every morning is worthless.

    Ordering of sections reflects what should change your behaviour soonest:
    hard commitments, then what is genuinely late, then a small ranked set of
    candidates, then the slow signals (stalled work, people, reviews) that
    matter over weeks rather than hours.
    """
    db = db or get_db()
    d = for_date or _dt.date.today().isoformat()

    def cap(rows: list[dict], n: int) -> list[dict]:
        return rows[:n]

    brief: dict[str, Any] = {"for_date": d}

    # pa.v_overdue is the one definition of overdue (migration 005): not
    # someday, not deferred unless hard_deadline. It is evaluated against
    # current_date, so for a for_date other than today this section is still
    # "overdue now" -- the rule is not restated here to make it date-relative.
    brief["overdue"] = db.rows(
        """select id, title, project_name, due_on, hard_deadline, importance,
                  effort_minutes, contexts, (%s::date - due_on) as days_late
             from pa.v_overdue
            order by hard_deadline desc, due_on, id limit 20""",
        (d,),
    )

    brief["today"] = db.rows(
        """select id, title, project_name, due_on, due_at, scheduled_for, hard_deadline,
                  importance, effort_minutes, energy, contexts, score
             from pa.v_scored
            where due_on = %s::date or scheduled_for = %s::date or due_at::date = %s::date
            order by hard_deadline desc, due_at nulls last, score desc, id""",
        (d, d, d),
    )

    brief["soon"] = db.rows(
        """select id, title, project_name, due_on, hard_deadline, importance, contexts
             from pa.v_task
            where status not in ('done','dropped')
              and due_on > %s::date and due_on <= %s::date + %s
            order by due_on, id limit 20""",
        (d, d, horizon_days),
    )

    # Top candidates that are not already surfaced above, so the brief does not
    # repeat itself.
    brief["top_next"] = db.rows(
        """select id, title, project_name, importance, effort_minutes, energy,
                  contexts, score, score_importance, score_urgency, score_neglect
             from pa.v_scored
            where (due_on is null or due_on > %s::date)
              and (scheduled_for is null or scheduled_for <> %s::date)
            order by score desc, id limit 8""",
        (d, d),
    )

    brief["waiting"] = db.rows(
        """select id, title, waiting_on_name, waiting_on_note, waiting_since,
                  (%s::date - waiting_since) as days_waiting, project_name
             from pa.v_task where status = 'waiting'
            order by waiting_since nulls last, id limit 15""",
        (d,),
    )

    brief["inbox"] = db.rows(
        """select id, title, created_at from pa.v_task
            where status = 'inbox' order by created_at, id limit 15"""
    )

    brief["stalled_projects"] = cap(db.rows(
        "select id, name, path, kind, importance, open_tasks, last_activity_at from pa.v_stalled_projects"
        " order by importance desc, coalesce(last_activity_at, '-infinity'::timestamptz), id"
    ), 8)

    brief["review_due"] = cap(db.rows(
        """select id, name, path, kind, review_overdue_days, never_reviewed,
                  last_reviewed_on, review_cadence_days, open_tasks
             from pa.v_review_due
            order by never_reviewed desc, review_overdue_days desc nulls last, id"""
    ), 8)

    brief["connection_due"] = cap(connection_due(db=db), 8)

    brief["neglected"] = db.rows(
        """select id, title, project_name, defer_count, due_on, importance
             from pa.v_task
            where status not in ('done','dropped') and defer_count >= 3
            order by defer_count desc, id limit 8"""
    )

    brief["counts"] = db.row(
        """select
             (select count(*) from pa.v_task where status not in ('done','dropped')) as open_total,
             (select count(*) from pa.v_actionable) as actionable,
             (select count(*) from pa.v_task where status = 'inbox') as inbox,
             (select count(*) from pa.v_task where status = 'waiting') as waiting,
             (select count(*) from pa.v_overdue) as overdue,
             (select count(*) from pa.task where status = 'done'
                and completed_at >= %s::date - 7) as done_last_7d,
             (select count(*) from pa.task where status = 'done'
                and completed_at::date = %s::date) as done_today""",
        (d, d),
    )

    if record:
        db.execute(
            "insert into pa.brief_run(for_date, payload, delivered_via) values (%s::date, %s, %s)",
            (d, __import__("json").dumps(brief["counts"] or {}), "mcp"),
        )
    return brief


def recent_activity(days: int = 7, limit: int = 60, db: Db | None = None) -> list[dict]:
    db = db or get_db()
    return db.rows(
        """select e.at, e.actor, e.entity_type, e.entity_id, e.action,
                  e.from_value, e.to_value, e.note
             from pa.event e
            where e.at >= now() - make_interval(days => %s)
            order by e.at desc limit %s""",
        (days, limit),
    )
