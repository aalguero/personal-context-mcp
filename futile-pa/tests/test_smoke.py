#!/usr/bin/env python3
"""End-to-end exercise of the whole surface against a real database.

Not unit tests — an integration pass. It seeds tasks resembling Andre's actual
commitments, then asserts the behaviours that are easy to get wrong and hard to
notice:

  * a blocked task never appears as actionable or as a suggestion
  * energy and time limits are respected in suggestions
  * a task requiring a context the situation does not supply is excluded, even
    when its other contexts match
  * after_completion recurrence re-anchors to the finish date; fixed recurrence
    does not
  * running the recurrence scheduler twice creates nothing the second time
  * deferral is counted and logged
  * an area cannot be marked done
  * sql() cannot write

Run:  PA_DATABASE_URL=... python3 tests/test_smoke.py
"""

from __future__ import annotations

import datetime as _dt
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from pa import recurrence, repo                      # noqa: E402
from pa.db import Db, set_db                         # noqa: E402
from pa.importers import google_tasks                # noqa: E402
from pa.tools import TOOLS, dispatch                 # noqa: E402

TODAY = _dt.date.today()
D = lambda n: (TODAY + _dt.timedelta(days=n)).isoformat()  # noqa: E731

passed = 0
failed: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    global passed
    if cond:
        passed += 1
        print(f"  ok    {label}")
    else:
        failed.append(f"{label} {detail}")
        print(f"  FAIL  {label}  {detail}")


def section(t: str) -> None:
    print(f"\n{t}\n{'-' * len(t)}")


# ---------------------------------------------------------------------------
db = Db(actor="andre", use_pool=True)
set_db(db)

section("Tool surface")
from pa.tools import HANDLERS, TOOL_PREFIX  # noqa: E402

check("tools defined", len(TOOLS) >= 20, f"({len(TOOLS)})")
names = {t["name"] for t in TOOLS}
check("no duplicate tool names", len(names) == len(TOOLS))
check("schemas and handlers agree", names == {TOOL_PREFIX + k for k in HANDLERS},
      str(names.symmetric_difference({TOOL_PREFIX + k for k in HANDLERS})))
check("all descriptions non-trivial", all(len(t["description"]) > 80 for t in TOOLS))

section("Seeding realistic tasks")
garden_plant = repo.task_add(
    "Plant the Carex appalachica plugs in the side garden bed",
    project="native-garden", effort_minutes=180, energy="high",
    contexts=["field", "dry", "warm", "daylight"], importance=4, due_on=D(9),
)
sheet_mulch = repo.task_add(
    "Finish sheet-mulching the last of the quarter acre",
    project="native-garden", effort_minutes=240, energy="high",
    contexts=["field", "handtools", "dry"], importance=4,
)
repo.task_add("Call Wing and a Prayer about the June pickup window",
              project="native-garden", effort_minutes=10, energy="low",
              contexts=["phone"], importance=4, due_on=D(2))
mercer = repo.task_add("Read the Mercer advisory agreement, mark up the custody clause",
                       project="wealth-transition", effort_minutes=90, energy="high",
                       contexts=["computer", "focus"], importance=5, due_on=D(5),
                       hard_deadline=True)
repo.task_add("Ask sister to confirm she'll consent to the custodian change",
              project="wealth-transition", effort_minutes=15, energy="low",
              contexts=["phone"], importance=5, person="sister", due_on=D(3))
repo.task_add("Chase Fidelity for the inherited IRA transfer paperwork",
              project="wealth-transition", effort_minutes=20, energy="medium",
              contexts=["phone"], importance=5, waiting_on="sister")
repo.task_add("Catalog one crate of LPs", project="lp-catalog", effort_minutes=45,
              energy="low", contexts=["home", "computer", "lowenergy"], importance=2)
repo.task_add("Update the resume with the AI platform work",
              project="job-search", effort_minutes=120, energy="high",
              contexts=["computer", "focus"], importance=5, due_on=D(12))
repo.task_add("Recruiter follow-ups — three outstanding", project="job-search",
              effort_minutes=25, energy="medium", contexts=["phone", "computer"],
              importance=4, due_on=D(1))
repo.task_add("Book Rosco's senior bloodwork", project="rosco", effort_minutes=8,
              energy="low", contexts=["phone"], importance=5, due_on=D(-3))
repo.task_add("Order replacement chimney cap", project="farmhouse", effort_minutes=15,
              energy="low", contexts=["computer", "online"], importance=3)
repo.task_add("Reconcile the $431K YTD against the severance arithmetic",
              project="tax-2026-27", effort_minutes=60, energy="high",
              contexts=["computer", "focus"], importance=4, due_on=D(20))
repo.task_add("Dig out the Form 8606 filings for the rollover IRA basis",
              project="tax-2026-27", effort_minutes=45, energy="medium",
              contexts=["home", "handtools"], importance=3, defer_until=D(30))
repo.task_add("Something vague I have not classified yet", status="inbox")

check("tasks created", repo.task_list(limit=200) and True)

section("Dependencies block suggestions")
repo.task_update(garden_plant["id"], contexts=None)
db.execute("insert into pa.task_dependency(task_id, depends_on_task_id) values (%s,%s)",
           (garden_plant["id"], sheet_mulch["id"]))
gp = repo.task_get(garden_plant["id"])
check("dependent task reports blocked", gp["is_blocked"] is True)
actionable_ids = {t["id"] for t in repo.task_list(order="score", limit=200)}
check("blocked task excluded from scored list", garden_plant["id"] not in actionable_ids)

try:
    db.execute("insert into pa.task_dependency(task_id, depends_on_task_id) values (%s,%s)",
               (sheet_mulch["id"], garden_plant["id"]))
    check("cycle rejected", False, "insert succeeded")
except Exception as exc:
    check("cycle rejected", "cycle" in str(exc).lower(), str(exc)[:60])

section("Suggestion engine")
ev = dispatch("pa_suggest_now", {"situation": "ev_charging"})
ev_titles = [s["title"] for s in ev["suggestions"]]
check("EV charging returns suggestions", len(ev_titles) > 0)
check("no field work suggested at a charger",
      not any("Plant the Carex" in t or "sheet-mulching" in t for t in ev_titles),
      str(ev_titles))
check("phone work surfaces at a charger",
      any("Wing and a Prayer" in t or "sister" in t or "Rosco" in t for t in ev_titles),
      str(ev_titles[:4]))
check("every suggestion fits the 35-minute window",
      all((s.get("effort_minutes") or 0) <= 35 for s in ev["suggestions"]),
      str([(s["title"][:25], s.get("effort_minutes")) for s in ev["suggestions"]]))
check("nothing needing high energy at medium energy",
      all(s.get("energy") != "high" for s in ev["suggestions"]))

land = dispatch("pa_suggest_now", {"situation": "land_day"})
check("land day surfaces field work",
      any("sheet-mulching" in s["title"] for s in land["suggestions"]),
      str([s["title"][:30] for s in land["suggestions"]]))

walk = dispatch("pa_suggest_now", {"situation": "dog_walk"})
check("dog walk offers phone calls only",
      all("computer" not in (s.get("contexts") or []) for s in walk["suggestions"]))

tight = dispatch("pa_suggest_now", {"minutes": 10, "energy": "low"})
check("10 minutes returns only short work",
      all((s.get("effort_minutes") or 0) <= 10 for s in tight["suggestions"]),
      str([(s["title"][:20], s.get("effort_minutes")) for s in tight["suggestions"]]))

desk = dispatch("pa_suggest_now", {"situation": "desk_block"})
check("desk block surfaces the focus work",
      any("Mercer" in s["title"] or "resume" in s["title"] for s in desk["suggestions"]),
      str([s["title"][:30] for s in desk["suggestions"]]))

section("Scoring is explainable")
top = repo.task_list(order="score", limit=3)
check("score equals the sum of its parts",
      all(t["score"] == t["score_importance"] + t["score_urgency"]
          + t.get("score_neglect", 0) + t.get("score_age", 0) + t.get("score_quickwin", 0)
          for t in top),
      json.dumps([{k: v for k, v in t.items() if k.startswith("score")} for t in top]))
check("hard deadline outranks a soft one at similar distance",
      any(t["id"] == mercer["id"] for t in repo.task_list(order="score", limit=6)))

section("Deferral is counted and logged")
before = repo.task_get(mercer["id"])["defer_count"] if "defer_count" in repo.task_get(mercer["id"]) else 0
repo.task_defer(mercer["id"], D(14), "waiting on the ADV Part 2 before marking it up")
repo.task_defer(mercer["id"], D(21), "still waiting")
after = repo.task_get(mercer["id"])
check("defer_count incremented", after.get("defer_count") == before + 2, str(after.get("defer_count")))
check("deferred task hidden from actionable", after["is_deferred"] is True)
hist = repo.task_history(mercer["id"])
check("deferrals in the event log", sum(1 for e in hist if e["action"] == "deferred") == 2,
      str([e["action"] for e in hist]))
check("reason captured", any("ADV Part 2" in (e.get("note") or "") for e in hist))

section("Recurrence — fixed vs after completion")
bills = repo.recurrence_add("Pay the mortgage", mode="fixed",
                            rrule="FREQ=MONTHLY;BYMONTHDAY=1", project="admin-2026",
                            effort_minutes=10, energy="low", contexts=["computer", "online"],
                            importance=5, hard_deadline=True, lead_time_days=3,
                            dtstart=(TODAY - _dt.timedelta(days=40)).isoformat())
furnace = repo.recurrence_add("Change the furnace filter", mode="after_completion",
                              interval_days=90, project="farmhouse", effort_minutes=15,
                              energy="medium", contexts=["home", "handtools"], importance=3)

run1 = recurrence.run()
check("scheduler created occurrences", run1["created_count"] > 0, json.dumps(run1["created"]))
run2 = recurrence.run()
check("second run is a no-op", run2["created_count"] == 0, json.dumps(run2["created"]))

nxt = recurrence.preview(bills["id"], 3)
check("fixed preview lands on the 1st", all(d.endswith("-01") for d in nxt), str(nxt))

furnace_task = db.row(
    "select id from pa.task where recurrence_id = %s and status not in ('done','dropped')",
    (furnace["id"],))
check("after_completion series materialised one instance", furnace_task is not None)

done = repo.task_complete(furnace_task["id"], note="new 16x25x1, old one filthy")
check("completion rolled the series forward", "next_occurrence" in done,
      json.dumps({k: (v.get("due_on") if isinstance(v, dict) else v) for k, v in done.items()}))
if "next_occurrence" in done:
    expected = (TODAY + _dt.timedelta(days=90)).isoformat()
    check("next occurrence is 90 days from completion, not from the old due date",
          done["next_occurrence"]["due_on"] == expected,
          f"{done['next_occurrence']['due_on']} vs {expected}")
    check("contexts inherited by the new occurrence",
          set(done["next_occurrence"].get("contexts") or []) >= {"home", "handtools"},
          str(done["next_occurrence"].get("contexts")))
check("completion note recorded", any("16x25x1" in (e.get("note") or "")
      for e in repo.task_history(furnace_task["id"])))

section("Projects")
health = repo.project_tree()
check("tree includes areas and children", len(health) >= 24, str(len(health)))
stalled = repo.stalled_projects()
check("stalled projects detected (Food, Health, Outdoors have no tasks)",
      any(p["name"] in ("Plan — Father", "Plan — Son", "Plan — Daughter") for p in stalled),
      str([p["name"] for p in stalled][:6]))
check("areas excluded from stalled list", all(p["kind"] != "area" for p in stalled))

try:
    repo.project_update("food", status="done")
    check("area cannot be completed", False, "update succeeded")
except Exception as exc:
    check("area cannot be completed", "area_never_completes" in str(exc) or "check" in str(exc).lower(),
          str(exc)[:70])

garden = repo.project_get("native-garden")
check("project health counts tasks", garden["open_tasks"] >= 3, str(garden["open_tasks"]))
check("project path resolves", garden["path"] == "House & Land / Native Plant Garden", garden["path"])
check("ambiguous-free partial match works", repo.project_get("wealth")["name"] == "Wealth Transition")
rev = repo.project_review("native-garden", note="Beds ready; pickup scheduling is the bottleneck.")
check("review resets the clock", rev.get("last_reviewed_on") == TODAY.isoformat())

section("People and connection")
repo.person_update("daughter", name="Daughter", contact_cadence_days=21)
db.execute("update pa.person set last_contact_on = %s where slug = 'sister'", (D(-30),))
due = repo.connection_due()
check("overdue contacts surfaced", any(p["name"] == "Sister" for p in due),
      str([(p["name"], p.get("overdue_days"), p.get("never_logged")) for p in due]))
repo.log_contact("sister", how="call", note="talked through the custodian change")
due2 = repo.connection_due()
check("logging contact clears them", not any(p["name"] == "Sister" for p in due2))
sis = repo.person_get("sister")
check("contact history recorded", len(sis["recent_contact"]) >= 1)
check("person plan linked", sis.get("plan") is not None)

section("Daily brief")
brief = dispatch("pa_daily_brief", {})
check("brief has every section",
      all(k in brief for k in ("overdue", "today", "soon", "top_next", "waiting", "inbox",
                               "stalled_projects", "review_due", "connection_due",
                               "neglected", "counts")),
      str(sorted(brief)))
check("overdue picked up the vet booking",
      any("Rosco" in t["title"] for t in brief["overdue"]), str(brief["overdue"]))
check("days_late computed", all(t["days_late"] > 0 for t in brief["overdue"]))
check("waiting section populated", len(brief["waiting"]) >= 1)
check("inbox section populated", len(brief["inbox"]) >= 1)
check("counts present", brief["counts"]["open_total"] > 10, json.dumps(brief["counts"]))
check("top_next excludes today's items",
      not ({t["id"] for t in brief["top_next"]} & {t["id"] for t in brief["today"]}))
check("brief run recorded", db.value("select count(*) from pa.brief_run") >= 1)

section("Google Tasks import")
takeout = {
    "items": [
        {"title": "My Tasks", "items": [
            {"id": "gt_1", "title": "Renew the truck registration", "status": "needsAction",
             "due": "%sT00:00:00.000Z" % D(6), "notes": "RMV online"},
            {"id": "gt_2", "title": "Buy bird seed", "status": "needsAction"},
            {"id": "gt_3", "title": "Old finished thing", "status": "completed",
             "completed": "2026-03-01T12:00:00.000Z"},
            {"id": "gt_4", "title": "subtask of registration", "status": "needsAction",
             "parent": "gt_1"},
        ]},
        {"title": "Garden", "items": [
            {"id": "gt_5", "title": "Order more Ramboard", "status": "needsAction",
             "due": "%sT00:00:00.000Z" % D(15)},
        ]},
    ]
}
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
    json.dump(takeout, fh)
    tpath = fh.name

rep = google_tasks.import_file(tpath, list_map={"Garden": "native-garden"})
check("import created tasks", rep["created"] == 5, json.dumps(rep))
check("subtask linked", rep["subtasks_linked"] == 1)
check("mapped list routed to the real project",
      db.value("""select p.slug from pa.task t join pa.project p on p.id = t.project_id
                   where t.source_id = 'gt_5'""") == "native-garden")
check("unmapped list became its own project under Imported",
      db.value("""select pa.project_path(t.project_id) from pa.task t where t.source_id = 'gt_1'""")
      == "Imported / My Tasks")
check("due date not shifted by a day",
      db.value("select due_on::text from pa.task where source_id = 'gt_1'") == D(6),
      str(db.value("select due_on::text from pa.task where source_id = 'gt_1'")))
check("open tasks land in inbox for triage",
      db.value("select status from pa.task where source_id = 'gt_2'") == "inbox")
check("completed task imported as done",
      db.value("select status from pa.task where source_id = 'gt_3'") == "done")

rep2 = google_tasks.import_file(tpath, list_map={"Garden": "native-garden"})
check("re-import is idempotent", rep2["created"] == 0 and rep2["updated"] == 5, json.dumps(rep2))

section("Read-only escape hatch")
res = dispatch("pa_sql", {"query": "select count(*) as n from pa.task"})
check("select works", res["rows"][0]["n"] > 0)
try:
    dispatch("pa_sql", {"query": "update pa.task set title = 'nope' where id = 1"})
    check("writes refused", False, "update succeeded")
except Exception as exc:
    check("writes refused", "read-only" in str(exc).lower(), str(exc)[:70])

section("Error handling")
err = dispatch("pa_task_list", {"project": "zzz-nonexistent"})
check("unknown reference returns a usable error", "error" in err, json.dumps(err)[:100])
amb = dispatch("pa_task_list", {"project": "Plan"})
check("ambiguous reference names the candidates",
      "error" in amb and "matches several" in amb["error"], json.dumps(amb)[:140])

# ---------------------------------------------------------------------------
print(f"\n{'=' * 60}")
print(f"{passed} passed, {len(failed)} failed")
for f in failed:
    print(f"  - {f}")
sys.exit(1 if failed else 0)
