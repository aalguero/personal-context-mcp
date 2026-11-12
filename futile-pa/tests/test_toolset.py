#!/usr/bin/env python3
"""Shopping lists, phase 2 (task 212): which agent path holds the shop tools.

The decision under test, made by Andre 2026-11-12: on-demand Signal replies
(quickask.py) get all five pa_shop_* tools; the unattended check-in
(checkin.py) gets none. test_shopping.py proves the tools behave; this proves
they are wired to the right caller and to nobody else, and that a shopping
request on the Signal path lands in pa.shopping_item and not in pa.task
(task 217).

Positive controls are inline: each property is checked by a predicate, and the
same predicate is shown to fail on input that violates it.

Run:  PA_DATABASE_URL=... python3 tests/test_toolset.py
Writes, so it refuses any database but pa_verify / pa_scratch*. Loads the
assistant package without its __init__ (handoff §9: that calls load_dotenv()
and set_db() and would swap this suite's database handle), and redirects the
context store to a temp directory, refusing to run if the redirect did not take.
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
            f"test_toolset.py: refusing to run against database {name or '(none)'!r} — "
            "this suite writes data. Point PA_DATABASE_URL at a scratch "
            "restore (pa_verify, or pa_scratch*) and try again."
        )
    return name


_require_scratch_database()

from pa import shop as pa_shop                       # noqa: E402
from pa.db import Db, set_db                         # noqa: E402

PKG = "_assistant_under_test"
_pkg = types.ModuleType(PKG)
_pkg.__path__ = [str(HERE.parent / "assistant")]
sys.modules[PKG] = _pkg
context_store = importlib.import_module(f"{PKG}.context_store")
_tmp = tempfile.TemporaryDirectory(prefix="toolset-test-")
context_store.STORE_PATH = pathlib.Path(_tmp.name) / "context.json"
if not str(context_store.STORE_PATH).startswith(_tmp.name):
    sys.exit("REFUSING: context store redirect did not take; would write the real store")

toolset = importlib.import_module(f"{PKG}.toolset")
agent_loop = importlib.import_module(f"{PKG}.agent_loop")
quickask = importlib.import_module(f"{PKG}.quickask")
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

# Everything this suite writes has an id above these floors, and is removed at
# exit, so it can be rerun and run beside test_shopping.py on one scratch
# database. pa.task's dependents cascade; pa.event has no foreign key.
_FLOORS = {t: db.value(f"select coalesce(max(id), 0) from pa.{t}")
           for t in ("task", "shopping_item", "shopping_list", "event")}


def _cleanup() -> None:
    db.execute("delete from pa.shopping_item where id > %s", (_FLOORS["shopping_item"],))
    db.execute("delete from pa.shopping_list where id > %s", (_FLOORS["shopping_list"],))
    db.execute("delete from pa.task where id > %s", (_FLOORS["task"],))
    db.execute("delete from pa.event where id > %s", (_FLOORS["event"],))


atexit.register(_cleanup)

# A prefix no real item carries, so a scratch restore of production, with his
# actual grocery list in it, cannot collide with or be matched by these probes.
P = "zqprobe"

SHOP = {"pa_shop_add", "pa_shop_list", "pa_shop_update", "pa_shop_bought", "pa_shop_remove"}


def names(reg: list[dict]) -> set[str]:
    return {t["name"] for t in reg}


def has_shop(reg: list[dict]) -> bool:
    return any("shop" in n for n in names(reg))


def counts() -> dict:
    return {
        "task": db.value("select count(*) from pa.task"),
        "item": db.value("select count(*) from pa.shopping_item"),
        "event": db.value("select count(*) from pa.event"),
    }


# ---------------------------------------------------------------------------
section("The check-in registry has no shop tools")
check("ALL_TOOLS holds no tool with 'shop' in its name", not has_shop(toolset.ALL_TOOLS),
      str(sorted(n for n in names(toolset.ALL_TOOLS) if "shop" in n)))
check("positive control: the same predicate fires on QUICKASK_TOOLS", has_shop(toolset.QUICKASK_TOOLS))
check("pa.shop really exposes the five tools this suite expects", pa_shop.SHOP_TOOL_NAMES == SHOP,
      str(sorted(pa_shop.SHOP_TOOL_NAMES)))

# ---------------------------------------------------------------------------
section("The quick-ask registry is the check-in registry plus exactly the five")
q, a = names(toolset.QUICKASK_TOOLS), names(toolset.ALL_TOOLS)
check("QUICKASK_TOOLS minus ALL_TOOLS is exactly the five shop tools", q - a == SHOP, str(sorted(q - a)))
check("nothing from the check-in registry is missing on the quick-ask path", a <= q, str(sorted(a - q)))
for label, reg in (("ALL_TOOLS", toolset.ALL_TOOLS), ("QUICKASK_TOOLS", toolset.QUICKASK_TOOLS)):
    check(f"{label}: names are unique", len(reg) == len(names(reg)))
    bad = [t["name"] for t in reg
           if set(t) != {"name", "description", "input_schema"}
           or t["input_schema"].get("type") != "object"]
    check(f"{label}: every entry is Anthropic-shaped", not bad, str(bad))
check("positive control: an MCP-shaped entry is caught by that predicate",
      set({"name": "x", "description": "", "inputSchema": {}}) != {"name", "description", "input_schema"})
add_desc = next(t["description"] for t in toolset.QUICKASK_TOOLS if t["name"] == "pa_shop_add")
check("pa_shop_add's description tells the model shopping is not pa_task_add",
      "never" in add_desc and "pa_task_add" in add_desc)

# ---------------------------------------------------------------------------
section("Each caller is wired to its own registry and dispatcher")
captured: list[tuple] = []
_real_run = agent_loop.run
agent_loop.run = lambda system, msg, tools, dispatch, **kw: captured.append((tools, dispatch)) or "ok"
try:
    quickask.ask("hello")
    quickask.ask_with_history([{"role": "user", "content": "hello"}])
finally:
    agent_loop.run = _real_run
check("quickask.ask passes QUICKASK_TOOLS and quickask_dispatch",
      bool(captured) and captured[0][0] is toolset.QUICKASK_TOOLS
      and captured[0][1] is toolset.quickask_dispatch)
check("quickask.ask_with_history does too (the path Signal actually uses)",
      len(captured) == 2 and captured[1][0] is toolset.QUICKASK_TOOLS
      and captured[1][1] is toolset.quickask_dispatch)
# checkin.run_once sends Signal and writes the ledger, so it is read, not run.
src = (HERE.parent / "assistant" / "checkin.py").read_text(encoding="utf-8")
check("checkin.py hands the model ALL_TOOLS with dispatch",
      "toolset.ALL_TOOLS, toolset.dispatch" in src)
check("checkin.py never names the quick-ask registry",
      "QUICKASK_TOOLS" not in src and "quickask_dispatch" not in src)

# ---------------------------------------------------------------------------
section("The check-in dispatcher refuses a shop call, and writes nothing")
before = counts()
try:
    r = toolset.dispatch("pa_shop_add", {"name": f"{P} milk", "category": "grocery"})
    check("dispatch('pa_shop_add') raises", False, f"returned {str(r)[:120]}")
except KeyError:
    check("dispatch('pa_shop_add') raises KeyError (an unknown tool on this path)", True)
check("no task, item or event was written", counts() == before, f"{before} -> {counts()}")

# ---------------------------------------------------------------------------
section("On the quick-ask path, 'add milk' is an item and never a task (task 217)")
before = counts()
r = toolset.quickask_dispatch("pa_shop_add", {"name": f"{P} milk", "category": "grocery",
                                              "subcategory": "dairy", "quantity": "2"})
after = counts()
check("pa_shop_add via quickask_dispatch adds the item", r.get("action") == "added", str(r)[:160])
check("exactly one shopping_item row more", after["item"] == before["item"] + 1, f"{before} -> {after}")
check("zero pa.task rows more", after["task"] == before["task"], f"{before} -> {after}")
check("positive control: counts() does see a task write",
      (toolset.quickask_dispatch("pa_task_add", {"title": "toolset suite probe"}) or True)
      and counts()["task"] == before["task"] + 1)

toolset.quickask_dispatch("pa_shop_add", {"name": f"{P} coffee", "category": "grocery", "quantity": "2 lb"})
toolset.quickask_dispatch("pa_shop_add", {"name": f"{P} coffee filters", "category": "grocery"})
r = toolset.quickask_dispatch("pa_shop_list", {"category": "grocery"})
rendered = r.get("rendered", "")
check("pa_shop_list via quickask_dispatch returns the rendered list",
      f"{P} milk" in rendered and f"{P} coffee — 2 lb" in rendered, rendered[:200])
check("the rendering carries no task-id shape the outbound filter would act on",
      task_ids.extract_task_ids(rendered) == set(), str(task_ids.extract_task_ids(rendered)))
check("positive control: a numbered or bracketed list would be caught",
      task_ids.extract_task_ids("1) milk\ncoffee (2)") == {1, 2})

before = counts()
bought_before = db.value("select count(*) from pa.shopping_item where bought_at is not null")
r = toolset.quickask_dispatch("pa_shop_bought", {"items": [f"{P} coffee"]})
check("an ambiguous name via quickask_dispatch marks nothing",
      r.get("marked") == [] and r.get("ambiguous"), str(r)[:200])
check("and writes nothing", counts()["item"] == before["item"]
      and db.value("select count(*) from pa.shopping_item where bought_at is not null") == bought_before)

r = toolset.quickask_dispatch("pa_task_list", {})
check("non-shop tools still route on the quick-ask path", isinstance(r, (list, dict)) and "error" not in (r if isinstance(r, dict) else {}),
      str(r)[:120])
r = toolset.quickask_dispatch("get_context", {"category": "task_agent"})
check("context tools still route on the quick-ask path", isinstance(r, dict), str(r)[:120])

# ---------------------------------------------------------------------------
section("The name-collision guard fires")
_real_names = pa_shop.SHOP_TOOL_NAMES
pa_shop.SHOP_TOOL_NAMES = _real_names | {"pa_task_add"}
try:
    importlib.reload(toolset)
    check("a shop name shadowing pa_task_add stops the import", False, "reload succeeded")
except RuntimeError as exc:
    check("a shop name shadowing pa_task_add stops the import", "pa_task_add" in str(exc), str(exc))
finally:
    pa_shop.SHOP_TOOL_NAMES = _real_names
    importlib.reload(toolset)
check("and with the real names the module loads again", toolset.QUICKASK_TOOLS and not has_shop(toolset.ALL_TOOLS))

print(f"\n{'=' * 60}")
print(f"{passed} passed, {len(failed)} failed")
for f in failed:
    print(f"  - {f}")
sys.exit(1 if failed else 0)
