"""The check-in agent's record of what it asked and when Andre answered (task 213).

Before this module, the four facts the brief's rules depend on were written by
the model, in prose, when it remembered to. On 2026-11-10 it didn't: the 07:48
run asked about task 208 and recorded nothing, and the reply path never
updated last_reply_at, so the 24-hour no-repeat rule and the three-strikes
digest were both running on a record nothing was writing. The values that did
exist were sentences ("2026-11-10 ~11:38 — Andre has been highly active..."),
which no rule can compute from.

So code writes them, at the moment each one becomes true:

  asked_recently   {task_id: iso}    when Signal accepted a check-in that named
                                     the task. A question is asked when it
                                     leaves the machine, not when a model says so.
  open_questions   {task_id: iso}    same moment; pruned at the start of each
                                     check-in once the task has been touched
                                     (a pa.event after the ask), closed, or is a week old.
  last_reply_at    iso               when a message from Andre arrives.
  unanswered_runs  int               +1 per delivered question run, 0 on any reply.

All four live in the existing `task_agent` category under their existing
names, so the brief's rules read them where they always have. The model may
read them and may not write them: toolset.dispatch() asks refuse_model_write()
first. Everything else in `task_agent` (last_run_at, notes) stays the model's.

Deliberately imports nothing heavy: the db handle is passed in, so the tests
can run without Postgres. Task ids come from the same pattern the
sensitivity filter uses, applied to the text that was actually sent.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from . import context_store
from .task_ids import extract_task_ids

CATEGORY = "task_agent"
CODE_OWNED_KEYS = frozenset({"asked_recently", "open_questions", "last_reply_at", "unanswered_runs"})
# Where prose that the model wrote into a code-owned key before task 213 is
# moved on first contact, rather than deleted: it is the model's own notes.
LEGACY_KEY = "notes_pre_213"

KEEP_ASKED = timedelta(days=7)        # 24h is the rule; a week of history costs nothing
KEEP_OPEN = timedelta(days=7)         # after three strikes the agent is in digest mode anyway
REPEAT_WINDOW = timedelta(hours=24)   # the brief's no-repeat rule, checked after the fact

TZ = ZoneInfo("America/New_York")
log = logging.getLogger("futile-assistant.ledger")

# context_store.set() locks one key's write; these functions read-modify-write
# several keys, from two threads (scheduler and Signal bridge).
_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(TZ)


def _iso(dt: datetime) -> str:
    return dt.astimezone(TZ).isoformat(timespec="seconds")


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else None


def refuse_model_write(category: Any, key: Any) -> dict | None:
    """An error result for a model's set_context on a code-owned key, else None.

    Returned to the model as the tool result, not raised: the run should
    continue, and the model should learn from the message why the write was refused.
    """
    if category == CATEGORY and key in CODE_OWNED_KEYS:
        return {"error": (
            f"task_agent.{key} is written by code, not by you (task 213): it is "
            "recorded automatically when a check-in is delivered or Andre replies. "
            "Read it; do not write it. Put your own observations in task_agent.notes."
        )}
    return None


def _adopt_legacy(state: dict, now: datetime) -> dict:
    """Move pre-213 prose out of the code-owned keys, once. Returns the
    cleaned values for those keys. Caller holds _lock."""
    legacy: dict[str, Any] = {}
    clean: dict[str, Any] = {}

    for key in ("asked_recently", "open_questions"):
        raw = state.get(key)
        kept = {}
        if isinstance(raw, dict):
            for tid, ts in raw.items():
                if _parse(ts) is not None and str(tid).isdigit():
                    kept[str(tid)] = ts
                else:
                    legacy.setdefault(key, {})[str(tid)] = ts
        elif raw is not None:
            legacy[key] = raw
        clean[key] = kept

    reply = state.get("last_reply_at")
    if reply is None or _parse(reply) is not None:
        clean["last_reply_at"] = reply
    else:
        legacy["last_reply_at"] = reply
        clean["last_reply_at"] = None

    runs = state.get("unanswered_runs")
    if isinstance(runs, int) and not isinstance(runs, bool) and runs >= 0:
        clean["unanswered_runs"] = runs
    else:
        if runs is not None:
            legacy["unanswered_runs"] = runs
        clean["unanswered_runs"] = 0

    if legacy:
        existing = state.get(LEGACY_KEY)
        merged = dict(existing) if isinstance(existing, dict) else {}
        merged[_iso(now)] = legacy
        context_store.set(CATEGORY, LEGACY_KEY, merged)
        for key in clean:
            context_store.set(CATEGORY, key, clean[key])
        log.info("Moved pre-213 prose out of %d code-owned task_agent key(s) into %s",
                 len(legacy), LEGACY_KEY)
    return clean


def _existing_ids(db: Any, ids: Iterable[int]) -> set[int]:
    ids = sorted(set(ids))
    if not ids:
        return set()
    rows = db.rows("select id from pa.task where id = any(%s)", (ids,))
    return {int(r["id"]) for r in rows}


def record_questions(sent_text: str, db: Any, now: datetime | None = None) -> list[int]:
    """Record that a check-in naming these tasks reached signal-cli.

    Call only after the send succeeded, with the text as sent (post-filter).
    Ids are filtered to tasks that exist, because "(2026)" in prose matches
    the id pattern too. Returns the ids recorded.
    """
    now = now or _now()
    ids = sorted(_existing_ids(db, extract_task_ids(sent_text)))
    with _lock:
        state = _adopt_legacy(context_store.get(CATEGORY), now)
        asked = state["asked_recently"]
        repeats = [i for i in ids
                   if (prev := _parse(asked.get(str(i)))) and now - prev < REPEAT_WINDOW]
        if repeats:
            # After the fact, so it cannot prevent a repeat, only make one
            # visible. Ids are not content.
            log.warning("Check-in re-asked within 24h: tasks %s", ", ".join(map(str, repeats)))
        asked = {k: v for k, v in asked.items() if now - _parse(v) < KEEP_ASKED}
        opened = dict(state["open_questions"])
        for i in ids:
            asked[str(i)] = _iso(now)
            opened[str(i)] = _iso(now)
        context_store.set(CATEGORY, "asked_recently", asked)
        context_store.set(CATEGORY, "open_questions", opened)
        # Every delivered question run is a strike until he replies, ids or
        # not: three strikes counts runs that spoke and were not answered.
        context_store.set(CATEGORY, "unanswered_runs", state["unanswered_runs"] + 1)
    log.info("Recorded %d asked task id(s)", len(ids))
    return ids


def record_reply(now: datetime | None = None) -> None:
    """Andre sent a message. Any reply resets the strike count (brief §8)."""
    now = now or _now()
    with _lock:
        _adopt_legacy(context_store.get(CATEGORY), now)
        context_store.set(CATEGORY, "last_reply_at", _iso(now))
        context_store.set(CATEGORY, "unanswered_runs", 0)


def prune_open_questions(db: Any, now: datetime | None = None) -> list[int]:
    """Drop open questions that have been dealt with. Run at the start of a
    check-in, before the model reads its state. Returns the ids dropped.

    Answered means the task has a pa.event after the ask: whoever touched
    it, the data is no longer the stale thing that was asked about. A task
    that is done or dropped is answered too, and so is a week-old question.
    """
    now = now or _now()
    with _lock:
        state = _adopt_legacy(context_store.get(CATEGORY), now)
        opened = state["open_questions"]
        if not opened:
            return []
        ids = [int(k) for k in opened]
        rows = db.rows(
            """select t.id, t.status,
                      (select max(e.at) from pa.event e
                        where e.entity_type = 'task' and e.entity_id = t.id) as last_event
                 from pa.task t where t.id = any(%s)""", (ids,))
        info = {int(r["id"]): r for r in rows}
        dropped = []
        for k, ts in opened.items():
            asked_at = _parse(ts)
            r = info.get(int(k))
            # Db.rows() returns timestamps as ISO strings (pa.db.jsonable),
            # and drops NULLs, so a never-touched task has no key at all.
            last_event = _parse(r.get("last_event")) if r else None
            if (r is None or r.get("status") in ("done", "dropped")
                    or (last_event is not None and last_event > asked_at)
                    or now - asked_at >= KEEP_OPEN):
                dropped.append(int(k))
        if dropped:
            context_store.set(CATEGORY, "open_questions",
                              {k: v for k, v in opened.items() if int(k) not in dropped})
    if dropped:
        log.info("Closed %d open question(s)", len(dropped))
    return sorted(dropped)
