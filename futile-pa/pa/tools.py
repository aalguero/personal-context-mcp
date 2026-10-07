"""MCP tool definitions and dispatch, with no dependency on any MCP framework.

The point of the split: `TOOLS` is a plain list of JSON-Schema descriptors and
`dispatch(name, args)` is a plain function returning JSON-able Python. Whatever
the existing futileMCP server is built on — FastMCP, the low-level
`mcp.server.Server`, or something hand-rolled over stdio — wiring it up is a
dozen lines in an adapter (see server_fastmcp.py and server_lowlevel.py) rather
than a rewrite.

A note on tool descriptions. These are not documentation for a human; they are
the only thing the model reads when deciding which tool to reach for. So each
one says *when to use it*, not just what it does, and names the common mistake
where there is one. Descriptions that merely restate the function name are how
you end up with an assistant that lists tasks when you asked it to suggest one.
"""

from __future__ import annotations

from typing import Any, Callable

from . import recurrence as _rec
from . import repo
from .db import get_db

TOOL_PREFIX = "pa_"


def _t(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {
        "name": TOOL_PREFIX + name,
        "description": description.strip(),
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required or [],
            "additionalProperties": False,
        },
    }


# Reusable schema fragments -------------------------------------------------

_REF = {"type": ["string", "integer"],
        "description": "Name, slug or numeric id. Partial names work if unambiguous."}
_DATE = {"type": "string", "description": "ISO date, YYYY-MM-DD."}
_ENERGY = {"type": "string", "enum": ["low", "medium", "high"]}
_CONTEXTS = {"type": "array", "items": {"type": "string"},
             "description": "Context names such as phone, computer, field, errand, lowenergy. "
                            "The @ is optional. Unknown names are created."}
_STATUS = {"type": "string",
           "enum": ["inbox", "next", "scheduled", "in_progress", "waiting", "someday", "done", "dropped"]}


TOOLS: list[dict] = [

    # -- the two that matter most ------------------------------------------
    _t("daily_brief", """
       The morning summary. Call this at the start of a day, or whenever asked
       what is going on, what needs attention, or how things stand. Returns, in
       one round trip: overdue items, what is due or scheduled today, what lands
       in the next week, a ranked shortlist of other candidates, what is being
       waited on, untriaged inbox items, active projects with no next action,
       projects overdue for review, people past their contact cadence, tasks
       deferred three or more times, and summary counts.
       Prefer this over assembling the same picture from several task_list calls.
       """, {
        "for_date": _DATE,
        "horizon_days": {"type": "integer", "default": 7,
                         "description": "How far ahead the 'soon' section looks."},
    }),

    _t("suggest_now", """
       Answers "what should I do right now, given where I am and what I have".
       This is the tool for any request shaped by circumstance rather than by
       deadline: sitting at an EV charger, about to walk the dog, twenty minutes
       before a call, rained off the garden, too tired to think.
       Pass a situation name when one fits (see pa_vocabulary), otherwise pass
       minutes and energy and any contexts directly. A task is only suggested if
       the circumstances supply every context it requires, it fits the time, and
       it does not need more energy than is available.
       """, {
        "situation": {"type": "string",
                      "description": "A named situation, e.g. ev_charging, dog_walk, desk_block, "
                                     "town_trip, rainy_day, evening_low. See pa_vocabulary."},
        "minutes": {"type": "integer", "description": "Time available. Overrides the situation's default."},
        "energy": dict(_ENERGY, description="Energy available. Overrides the situation's default."),
        "contexts": _CONTEXTS,
        "include_untagged": {"type": "boolean", "default": True,
                             "description": "Include tasks with no context requirements, ranked below tagged matches."},
        "limit": {"type": "integer", "default": 10},
    }),

    # -- tasks --------------------------------------------------------------
    _t("task_add", """
       Capture a task. Cheap and expected — capturing something imperfectly is
       far better than not capturing it.
       Fill in effort_minutes, energy and contexts whenever they can reasonably
       be inferred: those three fields are what make pa_suggest_now able to
       offer this task later. A task with none of them will only ever surface by
       deadline or importance.
       Set hard_deadline only for a real external constraint (a filing date, a
       pickup window, someone else waiting). A date you would like to hit is not
       a hard deadline, and marking it as one devalues the ones that are.
       """, {
        "title": {"type": "string"},
        "project": _REF,
        "notes": {"type": "string"},
        "status": dict(_STATUS, default="next",
                       description="'next' if actionable now, 'inbox' if it needs triage later, "
                                   "'someday' if it is a maybe, 'waiting' if blocked on a person."),
        "importance": {"type": "integer", "minimum": 1, "maximum": 5,
                       "description": "1 trivial to 5 critical. Independent of urgency."},
        "effort_minutes": {"type": "integer", "description": "Realistic estimate."},
        "energy": _ENERGY,
        "due_on": dict(_DATE, description="Deadline day."),
        "due_at": {"type": "string", "description": "ISO timestamp, when a time of day matters."},
        "hard_deadline": {"type": "boolean", "default": False},
        "defer_until": dict(_DATE, description="Hide until this date — not yet possible or not yet worth thinking about."),
        "scheduled_for": dict(_DATE, description="The day you intend to do it. An intention, not a deadline."),
        "contexts": _CONTEXTS,
        "person": dict(_REF, description="Who this is about, e.g. a call to your father."),
        "waiting_on": dict(_REF, description="Who you are waiting on. Sets status to waiting."),
        "parent_task_id": {"type": "integer"},
        "depends_on": {"type": "array", "items": {"type": "integer"},
                       "description": "Task ids that must finish first. Blocked tasks are hidden from suggestions."},
        "external_url": {"type": "string"},
    }, ["title"]),

    _t("task_list", """
       Query tasks with filters. Use for "show me everything on X", "what is
       waiting on my sister", "anything due this month".
       For "what should I do now" use pa_suggest_now instead — this tool does not
       reason about time available or energy.
       Default ordering is by computed score (importance + urgency + neglect +
       age + quick-win bonus); the score components are returned so the ranking
       can be explained rather than asserted.
       """, {
        "status": {"type": "array", "items": _STATUS},
        "project": dict(_REF, description="Includes everything beneath it in the hierarchy by default."),
        "include_subtree": {"type": "boolean", "default": True},
        "contexts": _CONTEXTS,
        "person": _REF,
        "due_before": _DATE,
        "due_after": _DATE,
        "search": {"type": "string", "description": "Substring match on title and notes."},
        "unfiled": {"type": "boolean", "description": "Only tasks with no project."},
        "include_deferred": {"type": "boolean", "default": False},
        "open_only": {"type": "boolean", "default": True},
        "order": {"type": "string", "enum": ["score", "due", "importance", "created", "updated", "effort"],
                  "default": "score"},
        "limit": {"type": "integer", "default": 50},
    }),

    _t("task_update", """
       Change fields on an existing task. Use to reschedule, refile, re-estimate,
       or add the effort/energy/context tags that a task was captured without.
       To push something into the future use pa_task_defer instead, which records
       the deferral — deferrals are counted and a task pushed repeatedly gets
       surfaced deliberately.
       """, {
        "task_id": {"type": "integer"},
        "title": {"type": "string"},
        "notes": {"type": "string"},
        "status": _STATUS,
        "importance": {"type": "integer", "minimum": 1, "maximum": 5},
        "effort_minutes": {"type": "integer"},
        "energy": _ENERGY,
        "due_on": _DATE,
        "due_at": {"type": "string"},
        "hard_deadline": {"type": "boolean"},
        "scheduled_for": _DATE,
        "project": _REF,
        "person": _REF,
        "waiting_on": _REF,
        "waiting_on_note": {"type": "string"},
        "contexts": _CONTEXTS,
        "replace_contexts": {"type": "boolean", "default": False,
                             "description": "True replaces the context set; false adds to it."},
    }, ["task_id"]),

    _t("task_complete", """
       Mark a task done. If it belongs to a recurring series that repeats a fixed
       interval after completion (furnace filter, gutters), the next occurrence is
       created automatically and returned — so do not also create a follow-up by
       hand.
       """, {
        "task_id": {"type": "integer"},
        "note": {"type": "string", "description": "What happened, appended to the task's notes and logged."},
    }, ["task_id"]),

    _t("task_defer", """
       Push a task out to a date, recording it as a decision. Deferrals are
       counted; three or more puts the task in the daily brief's 'neglected'
       section, which is the prompt to either do it or admit it is not happening.
       """, {
        "task_id": {"type": "integer"},
        "until": _DATE,
        "reason": {"type": "string", "description": "Worth supplying — this is the useful part six weeks later."},
    }, ["task_id", "until"]),

    _t("task_drop", """
       Abandon a task, with a reason. Preferred over deleting: the row stays for
       the record, and a reason is more useful than a gap.
       """, {"task_id": {"type": "integer"}, "reason": {"type": "string"}},
       ["task_id", "reason"]),

    _t("task_history", """
       The event log for one task: created, status changes, deferrals, edits,
       notes. Use when the question is about behaviour over time rather than
       current state — how long something has been sitting, how often it has
       slipped.
       """, {"task_id": {"type": "integer"}, "limit": {"type": "integer", "default": 30}},
       ["task_id"]),

    # -- projects -----------------------------------------------------------
    _t("project_tree", """
       The project hierarchy with health metrics per node: open task count,
       actionable count, overdue count, last activity, review status.
       next_action_count = 0 on an active project is the signal worth acting on —
       it means something being cared about has no physically next step and has
       quietly stopped moving.
       Kinds: 'area' never completes (House & Land, Health, Finances); 'program'
       is multi-year but ends (Native Plant Garden); 'project' has a definable
       done state; 'person_plan' is one family member's container.
       """, {
        "root": dict(_REF, description="Limit to this project and its descendants."),
        "include_done": {"type": "boolean", "default": False},
    }),

    _t("project_add", """
       Create a project, program, area or person plan. Nest it under an existing
       one with `parent` — depth is unlimited, and the multi-year programs are
       expected to be several levels deep.
       Set review_cadence_days on anything that would rot unnoticed; areas in
       particular, since they never complete and so never force attention.
       """, {
        "name": {"type": "string"},
        "kind": {"type": "string", "enum": ["area", "program", "project", "person_plan"], "default": "project"},
        "parent": _REF,
        "status": {"type": "string", "enum": ["planning", "active", "paused", "blocked", "someday", "done", "abandoned"],
                   "default": "active"},
        "horizon": {"type": "string", "enum": ["now", "short", "medium", "long"],
                    "description": "now: this week. short: <3 months. medium: 3-12 months. long: >12 months."},
        "importance": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
        "description": {"type": "string"},
        "target_date": _DATE,
        "review_cadence_days": {"type": "integer"},
        "person": _REF,
        "slug": {"type": "string"},
    }, ["name"]),

    _t("project_update", """
       Change a project's fields — status, horizon, parent, target date,
       importance. Setting status to 'done' on an area is rejected by design:
       areas are ongoing responsibilities, not things that finish.
       """, {
        "ref": _REF,
        "name": {"type": "string"},
        "status": {"type": "string", "enum": ["planning", "active", "paused", "blocked", "someday", "done", "abandoned"]},
        "horizon": {"type": "string", "enum": ["now", "short", "medium", "long"]},
        "importance": {"type": "integer", "minimum": 1, "maximum": 5},
        "description": {"type": "string"},
        "notes": {"type": "string"},
        "target_date": _DATE,
        "completed_on": _DATE,
        "review_cadence_days": {"type": "integer"},
        "parent": _REF,
        "person": _REF,
    }, ["ref"]),

    _t("project_review", """
       Record that a project has been reviewed, which resets its review clock and
       clears it from the brief's review_due list. Attach what was decided as a
       note — that note is the review.
       """, {"ref": _REF, "on": _DATE, "note": {"type": "string"}}, ["ref"]),

    # -- people and connection ---------------------------------------------
    _t("person_list", """
       Family, friends, neighbours and professionals, with contact cadence and
       how overdue each is. Pass due_only for just those past their cadence —
       the working list for "who should I call".
       """, {"due_only": {"type": "boolean", "default": False}}),

    _t("person_upsert", """
       Create or update a person. Use it to put real names on the seeded
       placeholder records (Daughter, Son, Sister, Father), and to set
       contact_cadence_days — the cadence is what turns staying in touch from
       good intentions into something the brief can surface.
       """, {
        "ref": dict(_REF, description="Omit to create; supply to update."),
        "name": {"type": "string"},
        "relationship": {"type": "string"},
        "household": {"type": "string"},
        "location": {"type": "string"},
        "email": {"type": "string"},
        "phone": {"type": "string"},
        "birthday": _DATE,
        "contact_cadence_days": {"type": "integer",
                                 "description": "Target days between contact. Null means no expectation."},
        "has_plan": {"type": "boolean"},
        "notes": {"type": "string"},
    }),

    _t("log_contact", """
       Record having been in touch, which resets that person's cadence clock.
       Call this whenever a conversation is mentioned in passing — "spoke to my
       sister yesterday" is a fact worth writing down, and doing so is what keeps
       the connection list honest rather than nagging.
       """, {
        "person": _REF,
        "on": dict(_DATE, description="Defaults to today."),
        "how": {"type": "string", "description": "call, text, email, visit"},
        "note": {"type": "string"},
    }, ["person"]),

    # -- goals --------------------------------------------------------------
    _t("goal_list", """
       Outcomes, distinct from the projects that deliver them, with the projects
       linked to each and how many actionable tasks currently serve them. A goal
       with zero actionable tasks is a stated intention with nothing behind it.
       """, {
        "horizon": {"type": "string", "enum": ["short", "medium", "long"]},
        "status": {"type": "string", "enum": ["active", "paused", "achieved", "abandoned"], "default": "active"},
    }),

    _t("goal_add", """
       Add a goal. `measure` is the important field — how you will know it has
       been achieved. A goal without one cannot be reported on and tends to
       quietly become a mood.
       """, {
        "name": {"type": "string"},
        "horizon": {"type": "string", "enum": ["short", "medium", "long"]},
        "description": {"type": "string"},
        "target_date": _DATE,
        "measure": {"type": "string"},
        "importance": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
        "person": _REF,
        "projects": {"type": "array", "items": _REF, "description": "Projects that serve this goal."},
    }, ["name"]),

    # -- recurrence ---------------------------------------------------------
    _t("recurrence_add", """
       Define a repeating task. Choose the mode carefully, because this is the
       thing Google Tasks gets wrong:
         mode='fixed' with an RRULE — occurrences fall on calendar dates
           regardless of when the last was done. Bills, quarterly taxes, bin day,
           an annual physical. Missing one does not move the next.
         mode='after_completion' with interval_days — the next is N days after
           the last was actually finished. Furnace filter, gutters, oil change.
           Doing it late legitimately shifts the series, and modelling these as
           fixed generates a pile of false overdue items.
       RRULE examples: 'FREQ=MONTHLY;BYMONTHDAY=1', 'FREQ=WEEKLY;BYDAY=TU',
       'FREQ=YEARLY;BYMONTH=10;BYMONTHDAY=15', 'FREQ=MONTHLY;BYDAY=-1SU'.
       lead_time_days makes the task appear early so it can be planned rather
       than discovered on the day.
       """, {
        "title": {"type": "string"},
        "mode": {"type": "string", "enum": ["fixed", "after_completion"], "default": "fixed"},
        "rrule": {"type": "string", "description": "Required for fixed mode."},
        "interval_days": {"type": "integer", "description": "Required for after_completion mode."},
        "project": _REF,
        "person": _REF,
        "notes": {"type": "string"},
        "dtstart": dict(_DATE, description="Series anchor. Defaults to today."),
        "until_date": _DATE,
        "lead_time_days": {"type": "integer", "default": 0},
        "effort_minutes": {"type": "integer"},
        "energy": _ENERGY,
        "importance": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
        "hard_deadline": {"type": "boolean", "default": False},
        "contexts": _CONTEXTS,
    }, ["title"]),

    _t("recurrence_list", """
       All repeating series with their rules, modes, lead times and contexts. Use
       when asked what repeats, before adding a series that may already exist, or
       when a task keeps reappearing and the reason needs explaining.
       """, {"active_only": {"type": "boolean", "default": True}}),

    _t("recurrence_run", """
       Materialise task rows for occurrences now due to exist. Idempotent — safe
       to call repeatedly, including alongside a daily brief. Normally driven by
       cron; call it manually after adding a series to see its first instance.
       """, {
        "as_of": _DATE,
        "max_backfill_days": {"type": "integer", "default": 30,
                              "description": "On a series' first run, how far back to reach. "
                                             "Stops an old dtstart generating years of overdue items."},
    }),

    _t("recurrence_preview", """
       The next few occurrence dates for a series, creating nothing. Use to check
       an RRULE means what was intended before letting it generate tasks.
       """, {"recurrence_id": {"type": "integer"}, "count": {"type": "integer", "default": 6}},
       ["recurrence_id"]),

    # -- reference and escape hatch ----------------------------------------
    _t("vocabulary", """
       The available contexts and situations, with what each situation supplies
       in the way of contexts, time and energy. Read this before guessing context
       names or inventing a situation, and when asked what situations exist.
       """, {}),

    _t("activity", """
       Recent events across everything — completions, status changes, deferrals,
       contacts, reviews. Use for "what have I actually been doing", weekly
       reviews, and noticing where the work really went.
       """, {"days": {"type": "integer", "default": 7}, "limit": {"type": "integer", "default": 60}}),

    _t("sql", """
       Run one read-only SELECT against the pa schema. The escape hatch for
       questions no other tool covers — cross-cutting counts, ad hoc groupings,
       anything the fixed tool surface did not anticipate.
       Enforced read-only by the database itself (read-only transaction), with a
       statement timeout and a row cap, so it cannot write or hang.
       Useful tables and views: task, project, person, goal, context, situation,
       recurrence, event, v_task, v_scored, v_actionable, v_project_health,
       v_connection_due, v_overdue, v_today.
       """, {
        "query": {"type": "string", "description": "A single SELECT (or WITH ... SELECT) statement."},
        "limit": {"type": "integer", "default": 100, "maximum": 500},
    }, ["query"]),
]


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def _brief(args: dict) -> Any:
    return repo.daily_brief(for_date=args.get("for_date"),
                            horizon_days=args.get("horizon_days", 7))


def _person_upsert(args: dict) -> Any:
    ref = args.pop("ref", None)
    if ref:
        return repo.person_update(ref, **args)
    if not args.get("name"):
        raise ValueError("name is required when creating a person")
    return repo.person_add(**args)


def _vocabulary(args: dict) -> Any:
    return {"situations": repo.situations(), "contexts": repo.contexts_list()}


def _sql(args: dict) -> Any:
    rows = get_db().read_only(args["query"], limit=min(int(args.get("limit", 100)), 500))
    return {"row_count": len(rows), "rows": rows}


HANDLERS: dict[str, Callable[[dict], Any]] = {
    "daily_brief":        _brief,
    "suggest_now":        lambda a: repo.suggest(**a),
    "task_add":           lambda a: repo.task_add(**a),
    "task_list":          lambda a: {"tasks": repo.task_list(**a)},
    "task_update":        lambda a: repo.task_update(a.pop("task_id"), **a),
    "task_complete":      lambda a: repo.task_complete(a["task_id"], a.get("note")),
    "task_defer":         lambda a: repo.task_defer(a["task_id"], a["until"], a.get("reason")),
    "task_drop":          lambda a: repo.task_drop(a["task_id"], a["reason"]),
    "task_history":       lambda a: {"events": repo.task_history(a["task_id"], a.get("limit", 30))},
    "project_tree":       lambda a: {"projects": repo.project_tree(**a)},
    "project_add":        lambda a: repo.project_add(**a),
    "project_update":     lambda a: repo.project_update(a.pop("ref"), **a),
    "project_review":     lambda a: repo.project_review(a["ref"], a.get("on"), a.get("note")),
    "person_list":        lambda a: {"people": repo.person_list(**a)},
    "person_upsert":      _person_upsert,
    "log_contact":        lambda a: repo.log_contact(**a),
    "goal_list":          lambda a: {"goals": repo.goal_list(**a)},
    "goal_add":           lambda a: repo.goal_add(**a),
    "recurrence_add":     lambda a: repo.recurrence_add(**a),
    "recurrence_list":    lambda a: {"recurrences": repo.recurrence_list(**a)},
    "recurrence_run":     lambda a: _rec.run(**a),
    "recurrence_preview": lambda a: {"next": _rec.preview(a["recurrence_id"], a.get("count", 6))},
    "vocabulary":         _vocabulary,
    "activity":           lambda a: {"events": repo.recent_activity(**a)},
    "sql":                _sql,
}


def dispatch(name: str, args: dict | None = None) -> Any:
    """Run a tool by name. Accepts names with or without the pa_ prefix.

    Resolution and validation errors come back as `{"error": ...}` rather than as
    exceptions, because they are usually recoverable by the caller — "project
    'garden' matches several" is an instruction to try again with an id, and the
    model can act on that. Genuine faults propagate.
    """
    key = name[len(TOOL_PREFIX):] if name.startswith(TOOL_PREFIX) else name
    handler = HANDLERS.get(key)
    if handler is None:
        raise KeyError(f"Unknown tool '{name}'. Known: {', '.join(sorted(HANDLERS))}")
    try:
        return handler(dict(args or {}))
    except (repo.Ambiguous, repo.NotFound, ValueError) as exc:
        return {"error": str(exc), "tool": TOOL_PREFIX + key}


def tool_names() -> list[str]:
    return [t["name"] for t in TOOLS]
