#!/usr/bin/env python3
"""Shopping staples, task 212 phase 3, against a real database.

The rule (Andre, 2026-11-13), which lives only in pa.v_shopping_staple
(migration 008): a staple is anything bought on at least 3 distinct days; it is
due once the days since the last purchase reach 1.5 x its median gap, it is not
on a list, and it has not been asked about, or removed, within one median gap.

Each clause gets a pair of fixtures that differ only in that clause, one due
and one not, so every check is its own positive control. Then the code that
uses the rule: what the check-in is offered, and which staples a delivered
message starts the cooldown for.

Run:  PA_DATABASE_URL=... python3 tests/test_staples.py
Writes, so it refuses any database but pa_verify / pa_scratch*. Fixture names
start with "zqstaple", and every row it writes is removed at exit by id floor,
so it can be rerun and is safe on a restore of production.
"""
from __future__ import annotations

import atexit
import importlib
import os
import pathlib
import sys
import tempfile
import types
from urllib.parse import urlparse

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))


def _require_scratch_database() -> str:
    url = os.environ.get("PA_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""
    name = urlparse(url).path.lstrip("/")
    if not (name == "pa_verify" or name.startswith("pa_scratch")):
        sys.exit(
            f"test_staples.py: refusing to run against database {name or '(none)'!r} — "
            "this suite writes data. Point PA_DATABASE_URL at a scratch "
            "restore (pa_verify, or pa_scratch*) and try again."
        )
    return name


_require_scratch_database()

from pa import shop                                  # noqa: E402
from pa.db import Db, set_db                         # noqa: E402

PKG = "_assistant_under_test"
_pkg = types.ModuleType(PKG)
_pkg.__path__ = [str(HERE.parent / "assistant")]
sys.modules[PKG] = _pkg
context_store = importlib.import_module(f"{PKG}.context_store")
_tmp = tempfile.TemporaryDirectory(prefix="staples-test-")
context_store.STORE_PATH = pathlib.Path(_tmp.name) / "context.json"
if not str(context_store.STORE_PATH).startswith(_tmp.name):
    sys.exit("REFUSING: context store redirect did not take; would write the real store")
checkin = importlib.import_module(f"{PKG}.checkin")
task_ids = importlib.import_module(f"{PKG}.task_ids")

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

_FLOORS = {t: db.value(f"select coalesce(max(id), 0) from pa.{t}")
           for t in ("shopping_item", "shopping_staple_prompt")}


def _cleanup() -> None:
    db.execute("delete from pa.shopping_staple_prompt where id > %s", (_FLOORS["shopping_staple_prompt"],))
    db.execute("delete from pa.shopping_item where id > %s", (_FLOORS["shopping_item"],))


atexit.register(_cleanup)

P = "zqstaple"
# Noon, N days before today in America/New_York: the view's calendar.
DAY = "((now() at time zone 'America/New_York')::date - %s + time '12:00') at time zone 'America/New_York'"


def bought(name: str, days_ago: list[int], category: str = "grocery") -> None:
    for d in days_ago:
        db.execute(f"insert into pa.shopping_item (category, name, bought_at) values (%s, %s, {DAY})",
                   (category, f"{P} {name}", d))


def asked(name: str, days_ago: int, category: str = "grocery") -> None:
    db.execute(f"insert into pa.shopping_staple_prompt (category, normalized_name, asked_at) "
               f"values (%s, pa.shop_normalize(%s), {DAY})", (category, f"{P} {name}", days_ago))


def removed(name: str, days_ago: int, category: str = "grocery") -> None:
    db.execute(f"insert into pa.shopping_item (category, name, removed_at, removed_reason) "
               f"values (%s, %s, {DAY}, 'test')", (category, f"{P} {name}", days_ago))


def active(name: str, category: str = "grocery") -> None:
    db.execute("insert into pa.shopping_item (category, name) values (%s, %s)", (category, f"{P} {name}"))


def row(name: str, category: str = "grocery") -> dict:
    return db.row("select * from pa.v_shopping_staple where category = %s and name = %s",
                  (category, f"{P} {name}")) or {}


RHYTHM = [46, 36, 26, 16]          # gaps 10/10/10, median 10, 16 days since: 16 >= 15, due

# ---------------------------------------------------------------------------
section("Fixtures: each clause gets a due twin and a not-due twin")
bought("coffee", RHYTHM)                       # due: the base case
bought("tea", [44, 34, 24, 14])                # not: 14 days since < 1.5 x 10
bought("sugar", [100, 80])                     # not: only 2 purchase days
bought("flour", [120, 100, 80])                # due: 3 days, 80 >= 1.5 x 20
bought("milk", RHYTHM); active("milk")         # not: already on a list
bought("eggs", RHYTHM); asked("eggs", 5)       # not: asked 5 days ago < 10
bought("butter", RHYTHM); asked("butter", 11)  # due: asked 11 days ago >= 10
bought("rice", RHYTHM); removed("rice", 5)     # not: removed after its last buy, 5 < 10
bought("beans", RHYTHM); removed("beans", 30)  # due: removed BEFORE its last buy, so it doesn't count
bought("oats", [46, 46, 36, 26, 16])           # due: two receipts on one day are one purchase day
bought("nails", RHYTHM, category="hardware")   # due: a staple per category
bought("salt", [3, 2, 1])                      # not: gaps of 1 day; 1 day since < 1.5
check("fixtures written", db.value("select count(*) from pa.shopping_item where position(%s in name) = 1",
                                   (P,)) >= 40)

section("The rule, clause by clause")
expect = {"coffee": True, "tea": False, "sugar": False, "flour": True, "milk": False,
          "eggs": False, "butter": True, "rice": False, "beans": True, "oats": True, "salt": False}
for name, want in expect.items():
    r = row(name)
    check(f"{name}: due is {want}", r.get("due") is want,
          f"due={r.get('due')} buys={r.get('buy_count')} median={r.get('median_gap_days')} "
          f"since={r.get('days_since')} on_list={r.get('on_a_list')}")
check("nails (hardware) is due in its own category", row("nails", "hardware").get("due") is True)
check("coffee: 4 purchase days, median 10, 16 days since",
      (row("coffee").get("buy_count"), float(row("coffee").get("median_gap_days", 0)), row("coffee").get("days_since"))
      == (4, 10.0, 16), str(row("coffee"))[:200])
check("oats: the same-day double receipt counts as one day, so the median stays 10",
      (row("oats").get("buy_count"), float(row("oats").get("median_gap_days", 0))) == (4, 10.0),
      str(row("oats"))[:200])
check("sugar is in the view (bought) but has 2 purchase days", row("sugar").get("buy_count") == 2)
check("milk is flagged on_a_list", row("milk").get("on_a_list") is True)

# ---------------------------------------------------------------------------
section("What the check-in is offered")
due = [r for r in shop.staples_due(db) if r["name"].startswith(P)]
names = [r["name"] for r in due]
check("exactly the due fixtures are offered",
      sorted(names) == sorted(f"{P} {n}" for n in ("coffee", "flour", "butter", "beans", "oats", "nails")),
      str(names))
check("most overdue first: flour, 4x its gap, leads", names[:1] == [f"{P} flour"], str(names))
lines = shop.render_staples(due)
check("the prompt lines carry no task-id shape", task_ids.extract_task_ids(lines) == set(), lines[:200])
check("positive control: a bracketed or leading number would be caught",
      task_ids.extract_task_ids("- coffee (21)\n21) coffee") == {21})

# ---------------------------------------------------------------------------
section("Which staples a delivered message starts the cooldown for")
offered = due
text = (f"A couple of staples:\n{P.capitalize()} Coffee — usually every 10 days, last bought 16 days ago. "
        f"Add it?\nAnd {P} butter? Also {P} oatsy, which is not a staple.")
before = db.value("select count(*) from pa.shopping_staple_prompt")
named = shop.record_staples_asked(text, offered, db)
check("only the names the text contains, case-insensitively",
      sorted(named) == [f"{P} butter", f"{P} coffee"], str(named))
check("'oatsy' does not count as oats (whole words only)", f"{P} oats" not in named)
check("two prompt rows written", db.value("select count(*) from pa.shopping_staple_prompt") == before + 2)
check("coffee and butter are no longer due", row("coffee").get("due") is False and row("butter").get("due") is False)
check("flour, offered but not asked, is still due", row("flour").get("due") is True)

# ---------------------------------------------------------------------------
section("The check-in: offered in the prompt, recorded only on delivery")


def run_checkin(reply: str, delivered: bool) -> tuple[str, list[str]]:
    seen: dict = {}
    sent: list[str] = []
    store: dict = {}

    def fake_run(system, prompt, *a, **k):
        seen["prompt"] = prompt
        return reply

    def fake_dispatch(name, args=None):
        args = args or {}
        if name == "get_context":
            return dict(store)
        store[args.get("key")] = args.get("value")
        return {"ok": True}

    saved = {k: getattr(checkin, k) for k in ("agent_loop", "toolset", "notify", "ledger", "filter_outbound")}
    checkin.agent_loop = types.SimpleNamespace(run=fake_run)
    checkin.toolset = types.SimpleNamespace(dispatch=fake_dispatch, ALL_TOOLS=[])
    checkin.notify = types.SimpleNamespace(send=lambda m, **k: sent.append(m) or delivered)
    checkin.ledger = types.SimpleNamespace(prune_open_questions=lambda db: None,
                                           record_questions=lambda text, db: None)
    checkin.filter_outbound = lambda t: t
    try:
        checkin.run_once()
    finally:
        for k, v in saved.items():
            setattr(checkin, k, v)
    return seen.get("prompt", ""), sent


reply = f"Two quick ones:\n{P} flour — usually every 20 days, last bought 80 days ago. Add it to the grocery list?"
before = db.value("select count(*) from pa.shopping_staple_prompt")
prompt, sent = run_checkin(reply, delivered=False)
check("the prompt carries the STAPLES DUE block", "STAPLES DUE" in prompt, prompt[-300:])
check("and names the due staples", f"{P} flour" in prompt and f"{P} oats" in prompt)
check("and not the ones already asked", f"{P} coffee" not in prompt)
check("undelivered: no prompt row written", db.value("select count(*) from pa.shopping_staple_prompt") == before)
check("so flour is still due", row("flour").get("due") is True)
prompt, sent = run_checkin(reply, delivered=True)
check("delivered: exactly one prompt row, for flour",
      db.value("select count(*) from pa.shopping_staple_prompt") == before + 1
      and row("flour").get("due") is False)
check("oats, offered but not in the message, is still due", row("oats").get("due") is True)

db.execute("delete from pa.shopping_staple_prompt where id > %s", (_FLOORS["shopping_staple_prompt"],))
db.execute("delete from pa.shopping_item where id > %s and position(%s in name) = 1",
           (_FLOORS["shopping_item"], P))
real_due = shop.staples_due(db)
if real_due:
    # A production restore with a real buying history can have staples due;
    # the control below needs an empty list, so say so rather than fail.
    print(f"  skip  no-staples control: {len(real_due)} real staple(s) due on this database")
else:
    prompt, sent = run_checkin("Nothing to record.", delivered=True)
    check("with nothing due, the prompt has no STAPLES block (control for the check above)",
          "STAPLES DUE" not in prompt)

print(f"\n{'=' * 60}")
print(f"{passed} passed, {len(failed)} failed")
for f in failed:
    print(f"  - {f}")
sys.exit(1 if failed else 0)
