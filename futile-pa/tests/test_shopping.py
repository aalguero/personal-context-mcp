#!/usr/bin/env python3
"""Shopping lists, phase 1 (task 212), against a real database.

The property this suite exists for is the first section: twenty shopping items
must change nothing the backlog, the brief or the suggester can see. The rest
pins down the behaviours most likely to be built wrong — dedupe as a database
guarantee, the category/list cross-axis, ambiguous names marking nothing, and
a read-back with no task-id-shaped text in it.

Run:  PA_DATABASE_URL=... python3 tests/test_shopping.py
Writes, so it refuses any database but pa_verify / pa_scratch*, exactly as
test_smoke.py does.
"""

from __future__ import annotations

import atexit
import json
import os
import pathlib
import re
import sys
from urllib.parse import urlparse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


def _require_scratch_database() -> str:
    url = os.environ.get("PA_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""
    name = urlparse(url).path.lstrip("/")
    if not (name == "pa_verify" or name.startswith("pa_scratch")):
        sys.exit(
            f"test_shopping.py: refusing to run against database {name or '(none)'!r} — "
            "this suite writes data. Point PA_DATABASE_URL at a scratch "
            "restore (pa_verify, or pa_scratch*) and try again."
        )
    return name


_require_scratch_database()

from pa import repo, shop                            # noqa: E402
from pa.db import Db, set_db                         # noqa: E402
from pa.tools import TOOLS                           # noqa: E402

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


db = Db(actor="andre", use_pool=True)
set_db(db)
atexit.register(db.close)

FOR_DATE = "2026-11-10"

# The shopping half of this suite asserts on whole-table facts ("two lists
# created", "four rows bought in all", the milk row count), so it needs empty
# shopping tables. Until Andre had real items those were empty on a restore
# too; shop-phase3-report.md (2026-11-13) found two seed names colliding with
# real active items on a production restore. So: start empty. This deletes
# only shopping rows, only on a scratch database (checked at import above and
# again here, in the database itself, right before the delete), and the
# backlog half still runs on the restored tasks, which is the point of running
# it on a restore at all. Shopping rows never affect the backlog; that is the
# invariant this suite exists to prove.
_dbname = db.value("select current_database()")
if not (_dbname == "pa_verify" or _dbname.startswith("pa_scratch")):
    sys.exit(f"test_shopping.py: REFUSING to clear shopping tables on {_dbname!r}")
# shopping_staple_prompt exists from migration 008 on; an older schema has none.
_tables = [t for t in ("shopping_staple_prompt", "shopping_item", "shopping_list")
           if db.value("select to_regclass(%s) is not null", (f"pa.{t}",))]
_restored = {t: db.value(f"select count(*) from pa.{t}") for t in _tables}
if "shopping_staple_prompt" in _tables:
    db.execute("delete from pa.shopping_staple_prompt")
db.execute("delete from pa.shopping_item")
db.execute("delete from pa.shopping_list")
print(f"Cleared restored shopping rows on {_dbname}: "
      + ", ".join(f"{n} {t}" for t, n in _restored.items()))


def _tasks_only(result: dict) -> dict:
    """A suggest() result without its town_trip `shopping` attachment.

    Phase 3 (2026-11-13) attaches the lists to town_trip suggestions on
    purpose. The invariant this fingerprint guards is narrower and unchanged:
    items never move the TASKS the suggester returns. The attachment itself is
    asserted separately, below, so excluding it here hides nothing."""
    return {k: v for k, v in result.items() if k != "shopping"}


def backlog() -> dict:
    return {
        "task": db.value("select count(*) from pa.task"),
        "actionable": [r["id"] for r in db.rows("select id from pa.v_actionable order by id")],
        "scored": [r["id"] for r in db.rows("select id from pa.v_scored order by id")],
        "today": [r["id"] for r in db.rows("select id from pa.v_today order by id")],
        "event": db.value("select count(*) from pa.event"),
        "brief": json.dumps(repo.daily_brief(for_date=FOR_DATE, record=False), sort_keys=True, default=str),
        "suggest": json.dumps([_tasks_only(repo.suggest(limit=25)),
                               _tasks_only(repo.suggest(situation="town_trip", limit=25))],
                              sort_keys=True, default=str),
    }


def first_diff(a, b, path: str = "$") -> str:
    """Where two snapshot values first differ, as 'path: a != b'. Empty if equal.

    Before 2026-11-11 a failure printed the first 60 characters of each side.
    Both started '{"connection_due": [...', so a real failure (ledger-213-report
    §5.2) could not be read at all. JSON strings are parsed back so the path
    points inside them, e.g. $.top_next[7].id: 101 != 96.
    """
    if isinstance(a, str) and isinstance(b, str) and a[:1] in "[{" and b[:1] in "[{":
        try:
            a, b = json.loads(a), json.loads(b)
        except ValueError:
            pass
    if a == b:
        return ""
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                return f"{path}.{k}: present only in {'after' if k not in a else 'before'}"
            d = first_diff(a[k], b[k], f"{path}.{k}")
            if d:
                return d
    if isinstance(a, list) and isinstance(b, list):
        for i, (x, y) in enumerate(zip(a, b)):
            d = first_diff(x, y, f"{path}[{i}]")
            if d:
                return d
        if len(a) != len(b):
            return f"{path}: length {len(a)} != {len(b)}"
    return f"{path}: {json.dumps(a, default=str)[:200]} != {json.dumps(b, default=str)[:200]}"


call = shop.dispatch

# ---------------------------------------------------------------------------
section("Tool surface")
names = {t["name"] for t in shop.SHOP_TOOLS}
check("five shop tools", names == {"pa_shop_add", "pa_shop_list", "pa_shop_update",
                                   "pa_shop_bought", "pa_shop_remove"}, str(names))
check("schemas and handlers agree", names == set(shop.SHOP_TOOL_NAMES))
check("no clash with pa.tools", not names & {t["name"] for t in TOOLS})
check("agent's source list (pa.tools.TOOLS) has no shop tool",
      not any("shop" in t["name"] for t in TOOLS))
check("all descriptions non-trivial", all(len(t["description"]) > 80 for t in shop.SHOP_TOOLS))

# ---------------------------------------------------------------------------
section("Twenty items, and the backlog does not move")
before = backlog()
# Control, before any shopping write: if two snapshots of unchanged data
# differ, a later "backlog unchanged" failure would blame shopping for
# nondeterminism in the backlog itself. Ties at a LIMIT did exactly that
# until migration 006 and the id tie-breaks in repo.daily_brief.
again = backlog()
check("backlog() is deterministic on unchanged data", before == again,
      next((f"{k}: {first_diff(before[k], again[k])}" for k in before if before[k] != again[k]), ""))
seed = [
    ("milk", "grocery", "dairy", "1 gal", None, None, None),
    ("eggs", "grocery", "dairy", "a dozen", None, None, None),
    ("coffee", "grocery", "pantry", "2 lb", None, None, None),
    ("coffee filters", "grocery", "pantry", None, None, None, None),
    ("onions", "grocery", "produce", "3 lb", None, None, None),
    ("potato", "grocery", "produce", None, None, None, None),
    ("potatoes", "grocery", "produce", "5 lb", None, None, None),
    ("celery", "grocery", "produce", None, None, "for stuffing", None),
    ("turkey stock", "grocery", "pantry", "2 qt", None, None, None),
    ("butter", "grocery", "dairy", "1 lb", None, None, None),
    ("steak", "grocery", "butcher", "2", "Sutter's Meats", None, None),
    ("sourdough", "grocery", "bakery", None, None, None, None),
    ("pipe wrench", "hardware", "plumbing", None, None, None, "bathroom reno"),
    ("PEX crimp rings", "hardware", "plumbing", "1/2 inch, a bag", None, None, "bathroom reno"),
    ("grout", "hardware", None, None, "Home Depot", None, "bathroom reno"),
    ("deck screws", "hardware", "fasteners", "5 lb box", None, None, None),
    ("USB-C cable", "technology", None, "2", None, None, None),
    ("headlamp batteries", "outdoors", None, "AAA, 8", None, None, "camping trip"),
    ("tent stakes", "outdoors", None, None, None, None, "camping trip"),
    ("marshmallows", "grocery", "snacks", None, None, None, "camping trip"),
]
first = None
for name, cat, sub, qty, store, notes, lst in seed:
    r = call("pa_shop_add", {k: v for k, v in dict(
        name=name, category=cat, subcategory=sub, quantity=qty, store=store,
        notes=notes, list=lst).items() if v is not None})
    first = first or r
    if r.get("action") != "added":
        check(f"seed {name}", False, json.dumps(r)[:160])
check("twenty active items", db.value("select count(*) from pa.shopping_item where bought_at is null and removed_at is null") == 20)
check("three categories+", db.value("select count(distinct category) from pa.shopping_item") >= 3)
check("two lists created implicitly", db.value("select count(*) from pa.shopping_list") == 2)
after = backlog()
for k in before:
    check(f"backlog unchanged: {k}", before[k] == after[k], first_diff(before[k], after[k]))

# ---------------------------------------------------------------------------
section("Lists are created visibly")
r = call("pa_shop_add", {"name": "bug spray", "category": "outdoors", "list": "campng trip"})
check("a typo creates a new list and says so",
      r.get("created_list") == "campng trip" and "Created new list 'campng trip'" in r["message"], r.get("message", ""))
r2 = call("pa_shop_add", {"name": "sunscreen", "category": "outdoors", "list": "Camping Trip"})
check("an existing list matches case-insensitively, nothing created",
      r2.get("created_list") is None and r2["item"]["list_name"] == "camping trip", json.dumps(r2)[:160])
call("pa_shop_remove", {"items": ["bug spray"], "list": "campng trip", "reason": "test typo"})

# ---------------------------------------------------------------------------
section("Dedupe")
n0 = db.value("select count(*) from pa.shopping_item where normalized_name = 'milk'")
rs = [call("pa_shop_add", {"name": n, "category": "grocery", "notes": f"note {i}"})
      for i, n in enumerate(["milk", "Milk ", "  milk", "MILK."])]
check("milk / 'Milk ' / '  milk' / 'MILK.' all report an update",
      all(r.get("action") == "updated_existing" for r in rs), str([r.get("action") for r in rs]))
check("still one standing milk row",
      db.value("select count(*) from pa.shopping_item where normalized_name = 'milk' and list_id is null") == 1
      and n0 == 1)
check("notes appended, not replaced", rs[-1]["item"]["notes"].startswith("note 0; note 1"), rs[-1]["item"].get("notes", ""))
check("quantity kept when not supplied", rs[-1]["item"].get("quantity") == "1 gal")
r = call("pa_shop_add", {"name": "milk", "category": "grocery", "list": "camping trip"})
check("milk on a named list is a separate row", r.get("action") == "added"
      and db.value("select count(*) from pa.shopping_item where normalized_name = 'milk'") == 2)
r = call("pa_shop_add", {"name": "Milk", "category": "Grocery "})
check("category is case-folded into the same key", r.get("action") == "updated_existing", json.dumps(r)[:120])
check("internal whitespace collapses",
      call("pa_shop_add", {"name": "coffee   filters", "category": "grocery"}).get("action") == "updated_existing")
try:
    db.execute("insert into pa.shopping_item(category, name) values ('grocery', 'MILK')")
    check("unique index rejects it", False, "insert succeeded")
except Exception as exc:
    check("unique index rejects it", "shopping_item_active_dedupe" in str(exc), type(exc).__name__)

section("Normalization boundaries")
check("potato and potatoes stay two rows (deliberate)",
      db.value("select count(*) from pa.shopping_item where normalized_name in ('potato','potatoes')") == 2)
check("normalize: trailing punctuation", db.value("select pa.shop_normalize('Eggs!! ')") == "eggs")
check("normalize: keeps inner punctuation", db.value("select pa.shop_normalize('1/2\" PEX, rings')") == '1/2" pex, rings')
check("normalize: keeps a closing paren", db.value("select pa.shop_normalize('eggs (large)')") == "eggs (large)")
r = call("pa_shop_add", {"name": " ?! ", "category": "grocery"})
check("a name that is only punctuation is refused", "error" in r, json.dumps(r)[:120])

# ---------------------------------------------------------------------------
section("Read-back: the cross-axis")
g = call("pa_shop_list", {"category": "grocery"})
gnames = [i["name"] for i in g["items"]]
check("grocery list includes the camping list's marshmallows", "marshmallows" in gnames)
check("…annotated with the list name", "marshmallows — camping trip" in g["rendered"], g["rendered"][:300])
check("…and the camping milk alongside the standing milk", gnames.count("milk") == 2)
check("grocery excludes hardware", "pipe wrench" not in gnames)
h = call("pa_shop_list", {"category": "hardware"})
check("hardware includes the bathroom-reno pipe wrench, annotated",
      "pipe wrench — bathroom reno" in h["rendered"], h["rendered"])
c = call("pa_shop_list", {"list": "camping trip"})
check("camping list spans categories",
      {i["category"] for i in c["items"]} == {"outdoors", "grocery"}, str({i["category"] for i in c["items"]}))
check("camping list does not repeat its own name per line", "— camping trip" not in c["rendered"])

lines = g["rendered"].splitlines()
heads = [l for l in lines if not l.startswith("  ")][1:]
check("walk order: produce before bakery before butcher before dairy before pantry",
      [h for h in heads if h.rstrip(":").lower() in ("produce", "bakery", "butcher", "dairy", "pantry")]
      == ["Produce:", "Bakery:", "Butcher:", "Dairy:", "Pantry:"], str(heads))
check("store and quantity render with a dash, not parentheses",
      "steak — 2 · Sutter's Meats" in g["rendered"], g["rendered"])
e = call("pa_shop_list", {})
check("no filter gives everything active", e["count"] == db.value(
    "select count(*) from pa.shopping_item where bought_at is null and removed_at is null"))

section("Read-back: no task-id-shaped text")
# The filter's real pattern, not a copy: since task 213 it lives in
# assistant/task_ids.py, shared by the filter and the check-in ledger. Loaded
# by file path, because importing the assistant package runs its __init__,
# which calls set_db() and would silently swap this suite's database handle mid-run.
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location(
    "_task_ids", pathlib.Path(__file__).resolve().parent.parent / "assistant" / "task_ids.py")
_sf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sf)
_TASK_ID_PATTERN = _sf.TASK_ID_PATTERN
check("loading the filter did not swap the database handle", __import__("pa.db").db.get_db() is db)
call("pa_shop_add", {"name": "AA batteries (24)", "category": "technology", "notes": "see task 12"})
call("pa_shop_add", {"name": "jar lids", "category": "hardware", "quantity": "(12)"})
outs = [call("pa_shop_list", a)["rendered"] for a in
        ({}, {"category": "grocery"}, {"category": "hardware"}, {"category": "technology"},
         {"list": "camping trip"}, {"list": "bathroom reno"})]
hits = [m.group(0) for o in outs for m in _TASK_ID_PATTERN.finditer(o)]
check("no rendered output matches the outbound task-id pattern", not hits, str(hits))
check("…including user text that contained '(24)' and 'task 12'",
      "AA batteries 24" in outs[3] and "task no. 12" in outs[3], outs[3])
check("…while the stored name is untouched",
      db.value("select count(*) from pa.shopping_item where name = 'AA batteries (24)'") == 1)
check("no rendered line has any (digits)", not any(re.search(r"\(\s*\d+\s*\)", o) for o in outs))

# ---------------------------------------------------------------------------
section("Bought, with echo-back")
r = call("pa_shop_bought", {"items": ["coffee"]})
check("'coffee' vs coffee + coffee filters: ambiguous, nothing marked",
      r["marked"] == [] and len(r["ambiguous"]) == 1
      and {c["name"] for c in r["ambiguous"][0]["candidates"]} == {"coffee", "coffee filters"}, json.dumps(r)[:300])
check("…and nothing changed in the table",
      db.value("select count(*) from pa.shopping_item where bought_at is not null") == 0)
r = call("pa_shop_bought", {"items": ["milk"]})
check("milk on two lists: ambiguous", r["marked"] == [] and len(r["ambiguous"]) == 1, json.dumps(r)[:200])
r = call("pa_shop_bought", {"items": ["milk"], "list": "camping trip"})
check("scoping to the list resolves it", [m["name"] for m in r["marked"]] == ["milk"]
      and r["marked"][0].get("list") == "camping trip", json.dumps(r)[:200])
r = call("pa_shop_bought", {"items": ["tea"]})
check("'tea' never marks steak (whole words only)", r["marked"] == [] and r["not_found"], json.dumps(r)[:200])
r = call("pa_shop_bought", {"items": ["coffee beans"]})
check("'coffee beans' marks nothing, offers coffee as a near miss",
      r["marked"] == [] and any(c["name"] == "coffee" for c in r["not_found"][0]["did_you_mean"]), json.dumps(r)[:250])
cid = db.value("select id from pa.shopping_item where name = 'coffee' and bought_at is null")
r = call("pa_shop_bought", {"items": [cid, "eggs", "onions", "eggs", "zzz"]})
check("by id plus names: exactly coffee, eggs, onions — once each",
      sorted(m["name"] for m in r["marked"]) == ["coffee", "eggs", "onions"], json.dumps(r["marked"]))
check("the message names what was marked and what was not",
      "Marked bought: " in r["message"] and "zzz" in r["message"], r["message"])
check("four rows bought in all: camping milk plus these three",
      db.value("select count(*) from pa.shopping_item where bought_at is not null") == 4)
r = call("pa_shop_bought", {"items": [cid]})
check("an already-bought id is not found, not re-marked", r["marked"] == [] and r["not_found"])
g2 = call("pa_shop_list", {"category": "grocery"})
check("bought items leave the active list", "coffee" not in [i["name"] for i in g2["items"]])
gc = call("pa_shop_list", {"category": "grocery", "include_closed": True})
check("include_closed finds them", {"coffee", "eggs", "onions"} <= {i["name"] for i in gc["closed"]})

section("Remove never deletes")
total = db.value("select count(*) from pa.shopping_item")
r = call("pa_shop_remove", {"items": ["celery"], "reason": "out of stock"})
check("celery removed with reason", [m["name"] for m in r["marked"]] == ["celery"])
row = db.row("select bought_at, removed_at, removed_reason from pa.shopping_item where name = 'celery'")
check("row still there, removed_at set, bought_at null, reason kept",
      row and row.get("removed_at") and "bought_at" not in row and row.get("removed_reason") == "out of stock", str(row))
check("row count unchanged", db.value("select count(*) from pa.shopping_item") == total)
check("removed is not bought", db.value("select count(*) from pa.shopping_item where bought_at is not null") == 4)
check("removed item is in include_closed",
      "celery" in {i["name"] for i in call("pa_shop_list", {"category": "grocery", "include_closed": True})["closed"]})
try:
    db.execute("update pa.shopping_item set bought_at = now() where name = 'celery'")
    check("bought and removed cannot both be set", False, "update succeeded")
except Exception as exc:
    check("bought and removed cannot both be set", type(exc).__name__ == "CheckViolation", type(exc).__name__)
r = call("pa_shop_add", {"name": "celery", "category": "grocery"})
check("a removed item can be added again as a new row", r.get("action") == "added")

# ---------------------------------------------------------------------------
section("Update")
sid = db.value("select id from pa.shopping_item where name = 'steak'")
r = call("pa_shop_update", {"item_id": sid, "quantity": "4", "notes": "for the grill"})
check("happy path", r["item"].get("quantity") == "4" and r["item"].get("notes") == "for the grill", json.dumps(r)[:160])
r = call("pa_shop_update", {"item_id": sid, "list": "camping trip"})
check("move to a list", r["item"].get("list_name") == "camping trip")
r = call("pa_shop_update", {"item_id": sid, "clear_list": True})
check("clear_list", "list_id" not in r["item"])
bid = db.value("select id from pa.shopping_item where name = 'butter'")
r = call("pa_shop_update", {"item_id": bid, "name": "Milk"})
check("rename onto a duplicate is refused, not crashed", "error" in r and "duplicate" in r["error"], json.dumps(r)[:160])

# ---------------------------------------------------------------------------
section("Errors come back as errors")
cases = {
    "add without name":        ("pa_shop_add", {"category": "grocery"}),
    "add without category":    ("pa_shop_add", {"name": "bread"}),
    "add with empty name":     ("pa_shop_add", {"name": "  ", "category": "grocery"}),
    "list by both":            ("pa_shop_list", {"category": "grocery", "list": "camping trip"}),
    "list unknown list":       ("pa_shop_list", {"list": "nonesuch"}),
    "update without id":       ("pa_shop_update", {"name": "x"}),
    "update bad id":           ("pa_shop_update", {"item_id": 999999, "name": "x"}),
    "update nothing":          ("pa_shop_update", {"item_id": sid}),
    "update bad field":        ("pa_shop_update", {"item_id": sid, "bought_at": "2026-01-01"}),
    "update empty category":   ("pa_shop_update", {"item_id": sid, "category": " "}),
    "update unknown list":     ("pa_shop_update", {"item_id": sid, "list": "nonesuch"}),
    "bought without items":    ("pa_shop_bought", {}),
    "bought empty items":      ("pa_shop_bought", {"items": []}),
    "bought unknown list":     ("pa_shop_bought", {"items": ["milk"], "list": "nonesuch"}),
    "remove without items":    ("pa_shop_remove", {"reason": "x"}),
    "remove unknown list":     ("pa_shop_remove", {"items": ["milk"], "list": "nonesuch"}),
}
for label, (tool, args) in cases.items():
    try:
        r = call(tool, args)
        check(label, isinstance(r, dict) and "error" in r and "Traceback" not in r["error"], json.dumps(r)[:120])
    except Exception as exc:
        check(label, False, f"raised {type(exc).__name__}")
r = call("pa_shop_bought", {"items": [999999]})
check("bought bad id: not found, nothing marked", r["marked"] == [] and r["not_found"])
r = call("pa_shop_remove", {"items": [999999]})
check("remove bad id: not found, nothing marked", r["marked"] == [] and r["not_found"])
check("an implicit list has no default category, so one is still required",
      call("pa_shop_add", {"name": "fishing line", "list": "camping trip"}).get("error") is not None)

# ---------------------------------------------------------------------------
section("Phase 3: a town trip carries the lists, alongside and never inside")
tt = repo.suggest(situation="town_trip", limit=25)
active = db.value("select count(*) from pa.shopping_item where bought_at is null and removed_at is null")
check("town_trip carries a shopping section", "shopping" in tt, ", ".join(sorted(tt)))
check("it holds every active item, from every list and category",
      tt.get("shopping", {}).get("count") == active and active > 0,
      f"{tt.get('shopping', {}).get('count')} vs {active}")
check("its rendering has no task-id shape",
      not _TASK_ID_PATTERN.search(tt.get("shopping", {}).get("rendered", "")))
check("other situations carry no shopping section",
      "shopping" not in repo.suggest(situation="desk_block", limit=5) and "shopping" not in repo.suggest(limit=5))
check("positive control: the fingerprint WOULD see the attachment if it were not excluded",
      json.dumps(tt, sort_keys=True, default=str) != json.dumps(_tasks_only(tt), sort_keys=True, default=str))

# ---------------------------------------------------------------------------
section("And after all of that, the backlog still has not moved")
final = backlog()
for k in before:
    check(f"backlog unchanged: {k}", before[k] == final[k], first_diff(before[k], final[k]))

print(f"\n{'=' * 60}")
print(f"{passed} passed, {len(failed)} failed")
for f in failed:
    print(f"  - {f}")
sys.exit(1 if failed else 0)
