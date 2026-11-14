"""Unit checks for assistant/ledger.py (task 213): the code-owned record of what
the check-in asked and when Andre replied.

No database: a fake db answers the ledger's two queries. The context store is
swapped for context_store.MemoryBackend and the suite refuses to run unless that
took. Run on the server, a write to the real store would overwrite the live
agent's state, so the swap is checked, not assumed. The memory backend refuses
a code-owned key written without code=True, as the database does, so the
ledger's own declaration is under test here too.

The assistant package is loaded without running its __init__, which calls
load_dotenv() and set_db() (handoff §9: that silently swaps database handles).

    python tests/test_ledger.py        # expect "N passed, 0 failed"
"""
from __future__ import annotations

import importlib
import logging
import pathlib
import sys
import types
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

HERE = pathlib.Path(__file__).resolve().parent
PKG_DIR = HERE.parent / "assistant"
PKG = "_assistant_under_test"

_pkg = types.ModuleType(PKG)
_pkg.__path__ = [str(PKG_DIR)]
sys.modules[PKG] = _pkg
context_store = importlib.import_module(f"{PKG}.context_store")
task_ids = importlib.import_module(f"{PKG}.task_ids")

REAL_BACKEND = context_store.backend()
context_store.use_backend(context_store.MemoryBackend())
if not isinstance(context_store.backend(), context_store.MemoryBackend):
    sys.exit("REFUSING: context store redirect did not take; would write the real store")

ledger = importlib.import_module(f"{PKG}.ledger")
if ledger.context_store is not context_store:
    sys.exit("REFUSING: ledger is bound to a different context_store module")

TZ = ZoneInfo("America/New_York")
passed = 0
failed: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    global passed
    if cond:
        passed += 1
        print(f"  ok   {label}")
    else:
        failed.append(f"{label} {detail}")
        print(f"  FAIL {label} {detail}")


def section(t: str) -> None:
    print(f"\n== {t}")


def reset(state: dict | None = None) -> None:
    context_store.use_backend(context_store.MemoryBackend())
    for k, v in (state or {}).items():
        context_store.set("task_agent", k, v, code=True)


def state() -> dict:
    return context_store.get("task_agent")


class FakeDb:
    """Answers the ledger's two queries the way pa.db.Db.rows() would:
    timestamps as ISO strings, NULL columns dropped."""

    def __init__(self, tasks: dict[int, dict]):
        self.tasks = tasks  # id -> {"status":..., "last_event": datetime|None}

    def rows(self, sql, params=()):
        ids = params[0]
        out = []
        for i in ids:
            if i not in self.tasks:
                continue
            t = self.tasks[i]
            r = {"id": i}
            if "last_event" in sql:
                r["status"] = t.get("status", "next")
                if t.get("last_event") is not None:
                    r["last_event"] = t["last_event"].isoformat()
            out.append(r)
        return out


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


cap = Capture()
logging.getLogger("futile-assistant.ledger").addHandler(cap)
logging.getLogger("futile-assistant.ledger").setLevel(logging.INFO)

T0 = datetime(2026, 11, 10, 7, 49, tzinfo=TZ)

# ---------------------------------------------------------------------------
section("Task id extraction (shared with the sensitivity filter)")
ex = task_ids.extract_task_ids
check("parenthesised id", ex("Cord 3 of 7 (171) was due Sunday?") == {171})
check("'task N' and 'task #N'", ex("about task 12 and Task #13") == {12, 13})
check("numbered by id, line start", ex("171) Cord 3 — done?\n  208) Papa's visit?") == {171, 208})
check("the id-numbered form mid-line is not an id", ex("we did 3) things") == set())
check("plain numbers are not ids", ex("2 of 7 done, 15 minutes") == set())
check("'(2026)' parses, existence filter must drop it", 2026 in ex("since (2026)"))

# ---------------------------------------------------------------------------
section("record_questions")
reset()
db = FakeDb({171: {}, 208: {}, 12: {}})
got = ledger.record_questions("171) Cord 3?\nPapa (208) — still Friday?\nSince (2026), anything else?", db, now=T0)
s = state()
check("records only ids that exist", got == [171, 208], str(got))
check("asked_recently has ISO timestamps", s["asked_recently"] == {"171": T0.isoformat(), "208": T0.isoformat()},
      str(s.get("asked_recently")))
check("open_questions mirrors the ask", set(s["open_questions"]) == {"171", "208"})
check("one delivered run is one strike", s["unanswered_runs"] == 1, str(s.get("unanswered_runs")))

ledger.record_questions("Anything else done, started, or newly on your plate?", db, now=T0 + timedelta(hours=4))
check("a run with no ids still counts as a strike", state()["unanswered_runs"] == 2)
check("…and records nothing as asked", set(state()["asked_recently"]) == {"171", "208"})

cap.lines.clear()
ledger.record_questions("171) Still cord 3?", db, now=T0 + timedelta(hours=8))
check("a repeat inside 24h is logged", any("re-asked within 24h" in m and "171" in m for m in cap.lines), str(cap.lines))
check("…and the stamp moves to the new ask",
      state()["asked_recently"].get("171") == (T0 + timedelta(hours=8)).isoformat())
cap.lines.clear()
ledger.record_questions("208) Papa?", db, now=T0 + timedelta(hours=25))
check("an ask after 24h is not a repeat", not any("re-asked" in m for m in cap.lines), str(cap.lines))

ledger.record_questions("(12) gutters?", db, now=T0 + timedelta(days=9))
check("asked entries older than a week are pruned",
      set(state()["asked_recently"]) == {"12"}, str(state()["asked_recently"]))

# ---------------------------------------------------------------------------
section("record_reply")
ledger.record_reply(now=T0 + timedelta(days=9, hours=1))
s = state()
check("last_reply_at is an ISO timestamp", s["last_reply_at"] == (T0 + timedelta(days=9, hours=1)).isoformat())
check("a reply resets strikes to 0", s["unanswered_runs"] == 0)
check("a reply does not clear open questions (which one he answered is unknown)",
      "12" in s["open_questions"])

# ---------------------------------------------------------------------------
section("prune_open_questions")
reset({"open_questions": {"1": T0.isoformat(), "2": T0.isoformat(), "3": T0.isoformat(),
                          "4": T0.isoformat(), "5": (T0 - timedelta(days=8)).isoformat()}})
db = FakeDb({
    1: {"last_event": T0 + timedelta(hours=2)},   # touched after the ask -> answered
    2: {"last_event": T0 - timedelta(hours=2)},   # touched before -> still open
    3: {"status": "done", "last_event": T0 - timedelta(days=1)},
    # 4: missing (deleted)
    5: {},                                        # never touched, but a week old
})
dropped = ledger.prune_open_questions(db, now=T0 + timedelta(hours=3))
check("drops touched-after, done, missing and week-old", dropped == [1, 3, 4, 5], str(dropped))
check("keeps the one touched only before the ask", state()["open_questions"] == {"2": T0.isoformat()},
      str(state()["open_questions"]))
reset()
check("nothing open is a no-op", ledger.prune_open_questions(FakeDb({}), now=T0) == [])

# ---------------------------------------------------------------------------
section("Adopting the pre-213 prose (shape copied from the live store, 2026-11-11)")
LIVE_SHAPE = {
    "last_run_at": "2026-11-11 ~15:48 run. Reconciled via get_context...",
    "unanswered_runs": 0,
    "open_questions": {"195": "someday-task overdue-display bug ... standing note, not a live question"},
    "asked_recently": {"28": "someday since 2026-11-05, never ask again",
                       "91": "someday since 2026-10-28, never ask again"},
    "last_reply_at": "2026-11-10 ~11:38 — Andre has been highly active ...",
    "standing_reminders": [],
}
reset(LIVE_SHAPE)
ledger.record_reply(now=T0)
s = state()
legacy = s.get("notes_pre_213", {})
moved = next(iter(legacy.values()), {}) if legacy else {}
check("prose moved, not deleted", moved.get("asked_recently", {}).get("28", "").startswith("someday since"), str(moved))
check("…including the prose open question and reply", "195" in moved.get("open_questions", {})
      and "highly active" in moved.get("last_reply_at", ""))
check("code-owned keys now hold only machine values",
      s["asked_recently"] == {} and s["open_questions"] == {} and s["last_reply_at"] == T0.isoformat())
check("the model's own keys are untouched",
      s["last_run_at"] == LIVE_SHAPE["last_run_at"] and s["standing_reminders"] == [])
ledger.record_reply(now=T0 + timedelta(minutes=1))
check("adoption happens once", len(state()["notes_pre_213"]) == 1, str(state()["notes_pre_213"]))
reset({"unanswered_runs": "three, probably"})
ledger.record_reply(now=T0)
check("a non-integer strike count is moved and reset", state()["unanswered_runs"] == 0
      and "three" in next(iter(state()["notes_pre_213"].values()))["unanswered_runs"])

# ---------------------------------------------------------------------------
section("The model may not write the code-owned keys")
for k in ("asked_recently", "open_questions", "last_reply_at", "unanswered_runs"):
    r = ledger.refuse_model_write("task_agent", k)
    check(f"task_agent.{k} refused", isinstance(r, dict) and "written by code" in r.get("error", ""))
for cat, k in (("task_agent", "last_run_at"), ("task_agent", "notes"),
               ("task_agent_watchdog", "last_run_at"), ("other", "asked_recently")):
    check(f"{cat}.{k} allowed", ledger.refuse_model_write(cat, k) is None)

section("The MCP server's refusal (agent_keys.refusal, loaded by path in server.py)")
agent_keys = importlib.import_module(f"{PKG}.agent_keys")
check("the ledger uses the shared list", ledger.CODE_OWNED_KEYS is agent_keys.CODE_OWNED_KEYS)
check("deleting all of task_agent is refused",
      "Refused" in (agent_keys.refusal("task_agent", None, whole_category=True) or ""))
check("deleting one code-owned key is refused", agent_keys.refusal("task_agent", "unanswered_runs") is not None)
check("deleting a model key is allowed", agent_keys.refusal("task_agent", "notes") is None)
check("deleting another whole category is allowed",
      agent_keys.refusal("health", None, whole_category=True) is None)

# ---------------------------------------------------------------------------
section("Isolation")
check("the real context store was not touched (its database service was never created)",
      getattr(REAL_BACKEND, "_svc", "absent") is None, repr(getattr(REAL_BACKEND, "_svc", "absent")))

print(f"\n{'=' * 60}")
print(f"{passed} passed, {len(failed)} failed")
for f in failed:
    print(f"  - {f}")
sys.exit(1 if failed else 0)
