#!/usr/bin/env python3
"""The context store in Postgres (migration 009): pa/profile.py, the wall
around schema profile, history, tiers, agent state, the import script and the
external watchdog's new read path.

Every property that matters has a positive control: the same check run with
the controlling thing removed, which must fail. A test that passes either way
proves nothing (handoff §4).

profile.history is append-only and cannot be cleaned up, so this suite makes
its own database: it connects to the server named by PA_DATABASE_URL (whose
database must be pa_verify or pa_scratch*), creates pa_scratch_profile_<random>,
migrates it, runs, and drops it. Needs CREATEDB, which the app role has.

    PA_DATABASE_URL=postgresql://...@host:port/pa_scratch_x python3 tests/test_profile.py

Values in this file are synthetic and carry the marker zqsecret, so the
no-values-in-output checks have something specific to look for.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import pathlib
import secrets
import stat
import subprocess
import sys
import tempfile
import threading
import time
import types
from urllib.parse import urlparse, urlunparse

HERE = pathlib.Path(__file__).resolve().parent
PA_ROOT = HERE.parent
sys.path.insert(0, str(PA_ROOT))

BASE_URL = os.environ.get("PA_DATABASE_URL") or ""
_base_name = urlparse(BASE_URL).path.lstrip("/")
if not (_base_name == "pa_verify" or _base_name.startswith("pa_scratch")):
    sys.exit(f"test_profile.py: refusing to run against database {_base_name or '(none)'!r}.")

import psycopg                                  # noqa: E402
from psycopg.rows import dict_row               # noqa: E402

DB_NAME = f"pa_scratch_profile_{secrets.token_hex(4)}"
_u = urlparse(BASE_URL)
URL = urlunparse(_u._replace(path=f"/{DB_NAME}"))

with psycopg.connect(BASE_URL, autocommit=True) as c:
    c.execute(f'create database "{DB_NAME}"')


def _drop() -> None:
    from pa import db as _pdb
    if _pdb._db is not None:
        _pdb._db.close()
    with psycopg.connect(BASE_URL, autocommit=True) as c:
        c.execute(f'drop database if exists "{DB_NAME}" with (force)')


import atexit                                    # noqa: E402
atexit.register(_drop)

env = dict(os.environ, PA_DATABASE_URL=URL)
mig = subprocess.run([sys.executable, str(PA_ROOT / "scripts" / "migrate.py")],
                     env=env, capture_output=True, text=True)
if mig.returncode != 0 or "009_profile_store.sql ... ok" not in mig.stdout:
    sys.exit(f"migration failed:\n{mig.stdout}\n{mig.stderr}")
os.environ["PA_DATABASE_URL"] = URL

from pa.db import Db, set_db                    # noqa: E402
from pa.profile import AGENT, CHAT, ContextError, ContextService, Withheld  # noqa: E402

db = Db(url=URL, actor="andre", use_pool=False)
set_db(db)

_spec = importlib.util.spec_from_file_location("_agent_keys", PA_ROOT / "assistant" / "agent_keys.py")
agent_keys = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agent_keys)

chat = ContextService(CHAT, agent_keys, db=db)
agent = ContextService(AGENT, agent_keys, db=db)

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


def raises(fn, exc=Exception) -> str | None:
    """The exception's class name and text if fn raised exc, else None."""
    try:
        fn()
    except exc as e:
        return f"{type(e).__name__}: {e}"
    return None


def raw(sql: str, params=(), writer: str | None = None):
    """A direct connection as the app role: no service, no role switch."""
    with psycopg.connect(URL, row_factory=dict_row) as c, c.cursor() as cur:
        if writer:
            cur.execute("select set_config('pa.writer', %s, true)", (writer,))
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else cur.rowcount


# ===========================================================================
section("pa_sql is walled off from schema profile")
chat.set("zq_wall", "k", "zqsecret wall value")
e = raises(lambda: db.read_only("select value from profile.entry"))
check("pa_sql (Db.read_only) is refused on profile.entry", e is not None and "permission denied" in e, str(e))
e = raises(lambda: db.read_only("select * from profile.history"))
check("pa_sql is refused on profile.history", e is not None and "permission denied" in e, str(e))
check("pa_sql still reads pa (pa.task, pa.agent_state)",
      isinstance(db.read_only("select count(*) as n from pa.task"), list)
      and isinstance(db.read_only("select count(*) as n from pa.agent_state"), list))
check("positive control: the same SELECT on a direct connection succeeds",
      raw("select count(*) as n from profile.entry")[0]["n"] >= 1)
# The role switch is the control: run read_only's statements minus `set role`.
with psycopg.connect(URL, row_factory=dict_row) as c:
    c.read_only = True
    with c.cursor() as cur:
        cur.execute("set local search_path to pa, public")
        cur.execute("select count(*) as n from profile.entry")
        n_without_role = cur.fetchone()["n"]
check("positive control: read_only's guards minus `set local role` CAN read profile",
      n_without_role >= 1)

# ===========================================================================
section("Round trip: every JSON shape survives")
shapes = {"s": "zqsecret ünïcödé — em dash", "i": 42, "f": 3.25, "b": True, "n": None,
          "l": [1, "two", {"three": 3}], "o": {"nested": {"deep": ["x", None, 1.5]}},
          "empty_s": "", "empty_l": [], "empty_o": {}}
for k, v in shapes.items():
    chat.set("zq_shapes", k, v)
got = chat.get("zq_shapes")
check("get returns exactly what was set, for every type", got == shapes,
      str({k: (got.get(k), v) for k, v in shapes.items() if got.get(k) != v}))
check("an unknown category is {}", chat.get("zq_never") == {})

# ===========================================================================
section("History: every version, append-only")
chat.set("zq_hist", "balance", "zqsecret v1")
chat.set("zq_hist", "balance", "zqsecret v2")
chat.set("zq_hist", "balance", "zqsecret v2")        # unchanged: no row
h = chat.history("zq_hist", "balance")
check("two changes -> two history rows (an unchanged write adds none)", len(h) == 2, str(len(h)))
check("newest first, with old and new values",
      h[0].get("old_value") == "zqsecret v1" and h[0].get("new_value") == "zqsecret v2"
      and h[1].get("new_value") == "zqsecret v1" and "old_value" not in h[1], str(h))
check("history records the scope as actor", {r["actor"] for r in h} == {"chat"}, str(h))
chat.delete("zq_hist", "balance")
h = chat.history("zq_hist", "balance")
check("a deleted key's value is in its history", h[0]["action"] == "delete"
      and h[0]["old_value"] == "zqsecret v2", str(h[0]))
check("deleting the last key keeps the category (it carries the tier)",
      any(c["category"] == "zq_hist" for c in chat.list_categories()))
for what, sql in (("UPDATE", "update profile.history set actor = 'x'"),
                  ("DELETE", "delete from profile.history"),
                  ("TRUNCATE", "truncate profile.history")):
    e = raises(lambda sql=sql: raw(sql))
    check(f"history refuses {what}, even on a direct connection",
          e is not None and "append-only" in e, str(e))
# Positive control: the trigger is what refuses. Disable it in a transaction
# that is rolled back, and the UPDATE goes through.
with psycopg.connect(URL) as c, c.cursor() as cur:
    cur.execute("alter table profile.history disable trigger history_no_update")
    cur.execute("update profile.history set actor = actor")
    n = cur.rowcount
    c.rollback()
check("positive control: with the trigger disabled, UPDATE succeeds (then rolled back)", n > 0, str(n))
check("...and the trigger is back after the rollback",
      raises(lambda: raw("update profile.history set actor = 'x'")) is not None)

chat.set("zq_gone", "a", "zqsecret a")
chat.set("zq_gone", "b", "zqsecret b")
chat.delete("zq_gone")
h = chat.history("zq_gone")
acts = sorted(r["action"] for r in h)
check("deleting a category logs each entry and the category itself",
      acts == ["category_delete", "category_set", "delete", "delete", "set", "set"], str(acts))
check("the category is gone", chat.get("zq_gone") == {}
      and not any(c["category"] == "zq_gone" for c in chat.list_categories()))

# ===========================================================================
section("Tiers: scoping, enforced in one place for both callers")
chat.set("zq_health", "condition", "zqsecret condition")
chat.set("zq_hobby", "fishing", "zqhobby value")
r = chat.describe("zq_health", description="medical facts", tier="sensitive")
check("context_category sets a tier and description", r.get("tier") == "sensitive"
      and r.get("description") == "medical facts", str(r))
allc = chat.get_all()
check("unscoped get_all leaves the sensitive category's content out",
      "zq_health" not in allc["contexts"] and "zqsecret condition" not in json.dumps(allc))
check("...and lists it in the index, with its description",
      {"category": "zq_health", "description": "medical facts"} in allc["sensitive"])
check("...while normal categories are in", allc["contexts"].get("zq_hobby") == {"fishing": "zqhobby value"})
check("chat reads a sensitive category by name", chat.get("zq_health") == {"condition": "zqsecret condition"})
e = raises(lambda: agent.get("zq_health"), Withheld)
check("agent scope is refused a sensitive category by name", e is not None, str(e))
check("...and the refusal carries no value", e is not None and "zqsecret" not in e)
check("agent get_all leaves it out too", "zq_health" not in agent.get_all()["contexts"])
check("agent may WRITE to a sensitive category (exposes nothing; history keeps the old value)",
      raises(lambda: agent.set("zq_health", "new_med", "zqsecret med")) is None
      and chat.get("zq_health").get("new_med") == "zqsecret med")
check("agent cannot read a sensitive category's history",
      raises(lambda: agent.history("zq_health"), Withheld) is not None)
check("agent cannot change a tier", raises(lambda: agent.describe("zq_health", tier="normal"),
                                           ContextError) is not None
      and chat.describe("zq_health").get("tier") == "sensitive")
chat.describe("zq_health", tier="normal")
check("positive control: the same category set normal IS readable by the agent",
      agent.get("zq_health").get("condition") == "zqsecret condition")
chat.describe("zq_health", tier="sensitive")
check("a tier change is itself in the history",
      sum(1 for r in chat.history("zq_health") if r["action"] == "category_set") == 4)
chat.delete("zq_health", "condition"); chat.delete("zq_health", "new_med")
chat.set("zq_health", "condition", "zqsecret again")
check("emptying a sensitive category and refilling it keeps it sensitive",
      raises(lambda: agent.get("zq_health"), Withheld) is not None)
e = raises(lambda: chat.history("zq_deleted_sensitive"))
chat.set("zq_deleted_sensitive", "x", "zqsecret x")
chat.describe("zq_deleted_sensitive", tier="sensitive")
chat.delete("zq_deleted_sensitive")
check("a deleted sensitive category's history stays sensitive to the agent",
      raises(lambda: agent.history("zq_deleted_sensitive"), Withheld) is not None
      and len(chat.history("zq_deleted_sensitive")) >= 3)

# ===========================================================================
section("Names")
for bad in ("Bad Name", "1abc", "", "a-b", "x" * 64):
    check(f"category {bad[:12]!r} refused", raises(lambda b=bad: chat.set(b, "k", 1), ContextError) is not None)
check("a 201-character key refused", raises(lambda: chat.set("zq_names", "k" * 201, 1), ContextError) is not None)
check("a 200-character key accepted", raises(lambda: chat.set("zq_names", "k" * 200, 1)) is None)
e = raises(lambda: raw("update profile.category set slug = 'zq_renamed' where slug = 'zq_names'"))
check("a category cannot be renamed (identity lives in the slug)", e is not None and "renamed" in e, str(e))

# ===========================================================================
section("Agent state: its own table, code-owned keys enforced by the database")
db_keys = {r["key"] for r in raw("select key from pa.agent_state_code_key where namespace = 'task_agent'")}
check("agent_keys.CODE_OWNED_KEYS and pa.agent_state_code_key agree",
      db_keys == set(agent_keys.CODE_OWNED_KEYS), f"{db_keys} vs {set(agent_keys.CODE_OWNED_KEYS)}")
chat.set("task_agent", "notes", "the model's own notes")
check("task_agent routes to pa.agent_state, not profile",
      raw("select count(*) as n from pa.agent_state where namespace='task_agent' and key='notes'")[0]["n"] == 1
      and raw("select count(*) as n from profile.category where slug='task_agent'")[0]["n"] == 0)
check("get('task_agent') reads it back", chat.get("task_agent") == {"notes": "the model's own notes"})
e = raises(lambda: chat.set("task_agent", "last_reply_at", "2026-01-01T00:00:00-05:00"), ContextError)
check("chat set_context on a code-owned key: refused by agent_keys", e is not None and "task 213" in e, str(e))
check("chat delete of the whole task_agent namespace: refused",
      raises(lambda: chat.delete("task_agent"), ContextError) is not None)
e = raises(lambda: raw("insert into pa.agent_state(namespace, key, value) values ('task_agent','last_reply_at','\"x\"')"))
check("the database refuses a code-owned key from a writer that is not code",
      e is not None and "code-owned" in e, str(e))
check("positive control: the same insert declared as code succeeds",
      raw("insert into pa.agent_state(namespace, key, value) values ('task_agent','last_reply_at','\"x\"')",
          writer="code") == 1)
check("the database refuses deleting it as non-code too",
      raises(lambda: raw("delete from pa.agent_state where key = 'last_reply_at'")) is not None)
check("service code=True writes a code-owned key",
      raises(lambda: agent.set("task_agent", "unanswered_runs", 2, code=True)) is None
      and agent.get("task_agent")["unanswered_runs"] == 2)
# A route that skipped the Python refusal still hits the database's.
class _NoRefusal(types.SimpleNamespace):
    pass
lax = _NoRefusal(AGENT_NAMESPACES=agent_keys.AGENT_NAMESPACES, refusal=lambda *a, **k: None)
e = raises(lambda: ContextService(CHAT, lax, db=db).set("task_agent", "open_questions", {}))
check("with the Python refusal removed, the database still refuses", e is not None and "code-owned" in e, str(e))
check("agent state keeps no history",
      raises(lambda: chat.history("task_agent"), ContextError) is not None
      and raw("select count(*) as n from profile.history where category = 'task_agent'")[0]["n"] == 0)
check("list_categories shows agent namespaces as agent_state",
      any(c["category"] == "task_agent" and c["tier"] == "agent_state" for c in chat.list_categories()))
check("unscoped get_all indexes agent state rather than including it",
      "task_agent" in chat.get_all()["agent_state"] and "task_agent" not in chat.get_all()["contexts"])

# ===========================================================================
section("No lost updates under concurrent writers (task 223)")
N, W = 40, 4


def hammer(store_set, worker: int) -> None:
    for i in range(N):
        store_set(f"w{worker}_k{i}", i)


# Control: context.json's algorithm (read the whole blob, modify, write back)
# without a cross-process lock, which is what four processes had.
blob: dict = {}
blob_lock = threading.Lock()   # makes each read and write atomic, not the pair


def blob_set(key, value):
    with blob_lock:
        snapshot = dict(blob)
    time.sleep(0.0005)
    snapshot[key] = value
    with blob_lock:
        blob.clear(); blob.update(snapshot)


ts = [threading.Thread(target=hammer, args=(blob_set, w)) for w in range(W)]
[t.start() for t in ts]; [t.join() for t in ts]
check("positive control: whole-file read-modify-write loses keys under the same load",
      len(blob) < N * W, f"{len(blob)} of {N * W}")

stores = [ContextService(CHAT, agent_keys, db=Db(url=URL, actor="andre", use_pool=False)) for _ in range(W)]
ts = [threading.Thread(target=hammer, args=(lambda k, v, s=stores[w]: s.set("zq_race", k, v), w))
      for w in range(W)]
[t.start() for t in ts]; [t.join() for t in ts]
check("row upserts from four separate connections lose nothing", len(chat.get("zq_race")) == N * W,
      f"{len(chat.get('zq_race'))} of {N * W}")

# ===========================================================================
section("The agent's context_store over the real database")
PKG = "_assistant_profile_test"
_pkg = types.ModuleType(PKG)
_pkg.__path__ = [str(PA_ROOT / "assistant")]
sys.modules[PKG] = _pkg
cs = importlib.import_module(f"{PKG}.context_store")
toolset = importlib.import_module(f"{PKG}.toolset")
check("the default backend is the database", type(cs.backend()).__name__ == "_DbBackend")
check("context_store.get('zq_health') raises Withheld for the agent",
      raises(lambda: cs.get("zq_health"), ContextError) is not None)
r = toolset.dispatch("get_context", {"category": "zq_health"})
check("toolset get_context on a sensitive category returns an error, no value",
      isinstance(r, dict) and "error" in r and "zqsecret" not in json.dumps(r), str(r))
r = toolset.dispatch("get_context", {})
check("toolset get_context with no category has no sensitive content",
      "zqsecret again" not in json.dumps(r) and "zq_hobby" in r, str(list(r))[:200])
r = toolset.dispatch("set_context", {"category": "task_agent", "key": "asked_recently", "value": {}})
check("toolset set_context on a code-owned key is refused", isinstance(r, dict) and "error" in r, str(r))
r = toolset.dispatch("set_context", {"category": "Bad Name", "key": "k", "value": 1})
check("toolset set_context with a bad category returns the validation error",
      isinstance(r, dict) and "error" in r, str(r))
ledger = importlib.import_module(f"{PKG}.ledger")
ledger.record_reply()
st = cs.get("task_agent")
check("ledger.record_reply writes code-owned keys through the real database",
      st.get("unanswered_runs") == 0 and isinstance(st.get("last_reply_at"), str), str(st))
check("...recorded with actor 'code'",
      raw("select updated_by from pa.agent_state where key='last_reply_at'")[0]["updated_by"] == "code")
broken = types.SimpleNamespace(get=lambda c: (_ for _ in ()).throw(psycopg.OperationalError("down")))
cs.use_backend(broken)
check("a database failure on read degrades to {} (a watchdog then alerts)", cs.get("task_agent") == {})
cs.use_backend(cs._DbBackend())

# ===========================================================================
section("The external watchdog reads pa.agent_state")
ew_spec = importlib.util.spec_from_file_location("_ew", PA_ROOT / "scripts" / "external_watchdog.py")
ew = importlib.util.module_from_spec(ew_spec)
ew_spec.loader.exec_module(ew)
agent.set("task_agent_watchdog", "last_run_at", "2026-11-13T19:48:00-05:00")
check("a stamp in pa.agent_state is read", ew.read_watchdog_field("last_run_at") == "2026-11-13T19:48:00-05:00")
check("an absent key is MISSING_KEY", ew.read_watchdog_field("nope") is ew.MISSING_KEY)
from datetime import datetime, timezone  # noqa: E402
now = datetime(2026, 11, 14, 3, 20, tzinfo=timezone.utc)
check("a fresh stamp from the database is ok",
      ew.check_checkin(ew.read_watchdog_field("last_run_at"), now)[0] == "ok",
      str(ew.check_checkin(ew.read_watchdog_field("last_run_at"), now)))
import pa.db as _pdb  # noqa: E402
_saved = _pdb._db
_pdb._db = Db(url=urlunparse(_u._replace(path="/pa_scratch_does_not_exist")), use_pool=False)
v = ew.read_watchdog_field("last_run_at")
_pdb._db = _saved
check("an unreachable database reads as STORE_UNREADABLE", v is ew.STORE_UNREADABLE)
check("...and STORE_UNREADABLE alerts", ew.check_checkin(v, now)[0] == "alert", str(ew.check_checkin(v, now)))
with tempfile.TemporaryDirectory() as d:
    f = pathlib.Path(d) / "ctx.json"
    f.write_text(json.dumps({"contexts": {"task_agent_watchdog": {"last_run_at": "2026-11-13T19:48:00-05:00"}}}))
    check("--context-file still reads a synthetic file", ew.read_watchdog_field("last_run_at", str(f))
          == "2026-11-13T19:48:00-05:00")
    check("a missing synthetic file is STORE_UNREADABLE",
          ew.read_watchdog_field("last_run_at", str(f) + ".absent") is ew.STORE_UNREADABLE)

# ===========================================================================
section("Import: once, all or nothing, values never printed")
IMP = PA_ROOT / "scripts" / "import_context_json.py"
DB2 = f"pa_scratch_profile_{secrets.token_hex(4)}"
URL2 = urlunparse(_u._replace(path=f"/{DB2}"))
with psycopg.connect(BASE_URL, autocommit=True) as c:
    c.execute(f'create database "{DB2}"')
atexit.register(lambda: psycopg.connect(BASE_URL, autocommit=True).execute(
    f'drop database if exists "{DB2}" with (force)'))
env2 = dict(os.environ, PA_DATABASE_URL=URL2)
subprocess.run([sys.executable, str(PA_ROOT / "scripts" / "migrate.py")], env=env2, check=True,
               capture_output=True)
src = {"meta": {"version": 1}, "contexts": {
    "identity": {"name": "zqsecret name", "list": [1, 2, {"x": None}]},
    "health": {"conditions": ["zqsecret c1", "zqsecret c2"]},
    "investments": {"rules": {"zqsecret": 1.5}},
    "hobbies": {"fishing": "zqsecret fishing"},
    "task_agent": {"last_reply_at": "2026-11-13T10:00:00-05:00", "unanswered_runs": 1,
                   "asked_recently": {"149": "2026-11-13T07:48:00-05:00"}, "open_questions": {},
                   "notes": "zqsecret notes"},
    "task_agent_watchdog": {"last_run_at": "2026-11-13T19:48:00-05:00"},
    "recurrence_runner": {"last_check": {"at": "x", "behind": []}, "last_run_at": "y"},
}}
with tempfile.TemporaryDirectory() as d:
    f = pathlib.Path(d) / "context.json"
    f.write_text(json.dumps(src))

    def imp(*args, url=URL2):
        return subprocess.run([sys.executable, str(IMP), *args], capture_output=True, text=True,
                              env=dict(os.environ, PA_DATABASE_URL=url))

    out = imp("--check", "--from", str(f))
    with psycopg.connect(URL2) as c2:
        n_after_check = c2.execute("select count(*) from profile.category").fetchone()[0]
    check("--check plans and writes nothing", out.returncode == 0 and "nothing written" in out.stdout
          and n_after_check == 0, out.stdout + out.stderr)
    out = imp("--apply", "--from", str(f))
    check("--apply commits", out.returncode == 0 and "committed:" in out.stdout, out.stdout + out.stderr)
    check("--apply reports the read-back matched", "matched the file value-for-value" in out.stdout)
    with psycopg.connect(URL2, row_factory=dict_row) as c2:
        tiers = {r["slug"]: r["tier"] for r in c2.execute("select slug, tier from profile.category")}
        n_agent = c2.execute("select count(*) as n from pa.agent_state").fetchone()["n"]
        actors = {r["actor"] for r in c2.execute("select distinct actor from profile.history")}
        code_by = c2.execute("select updated_by from pa.agent_state where key='last_reply_at'").fetchone()
    check("health and investments are sensitive; others normal",
          tiers.get("health") == tiers.get("investments") == "sensitive"
          and tiers.get("identity") == tiers.get("hobbies") == "normal", str(tiers))
    check("employment_situation (absent from the file) was created empty and sensitive",
          tiers.get("employment_situation") == "sensitive")
    check("agent namespaces went to pa.agent_state (8 rows), not profile",
          n_agent == 8 and "task_agent" not in tiers, f"{n_agent} {sorted(tiers)}")
    check("every history row is by 'import'", actors == {"import"}, str(actors))
    check("code-owned keys got past the database's guard (written by the import, as code)",
          code_by and code_by["updated_by"] == "import", str(code_by))
    out2 = imp("--apply", "--from", str(f))
    check("a second --apply refuses (store not empty)", out2.returncode != 0 and "REFUSING" in out2.stdout + out2.stderr,
          out2.stdout + out2.stderr)
    exp = pathlib.Path(d) / "export.json"
    out3 = imp("--export", str(exp))
    check("--export writes the file", out3.returncode == 0 and exp.exists(), out3.stdout + out3.stderr)
    check("the export is mode 600", exp.exists() and stat.S_IMODE(exp.stat().st_mode) == 0o600,
          oct(stat.S_IMODE(exp.stat().st_mode)) if exp.exists() else "missing")
    exported = json.loads(exp.read_text())["contexts"] if exp.exists() else {}
    want = dict(src["contexts"], employment_situation={})
    check("the export equals the imported file (plus the empty sensitive category)", exported == want,
          str(sorted(set(exported) ^ set(want))))
    check("--export refuses to overwrite", imp("--export", str(exp)).returncode != 0)
    printed = "".join(o.stdout + o.stderr for o in (out, out2, out3))
    check("no value was printed by --check/--apply/--export", "zqsecret" not in printed)
    check("positive control: the marker is in the source file", "zqsecret" in f.read_text())

    # All or nothing: a value Postgres's jsonb rejects (a NUL character) on
    # the last category must leave nothing behind from the earlier ones.
    DB3 = f"pa_scratch_profile_{secrets.token_hex(4)}"
    URL3 = urlunparse(_u._replace(path=f"/{DB3}"))
    with psycopg.connect(BASE_URL, autocommit=True) as c:
        c.execute(f'create database "{DB3}"')
    try:
        subprocess.run([sys.executable, str(PA_ROOT / "scripts" / "migrate.py")],
                       env=dict(os.environ, PA_DATABASE_URL=URL3), check=True, capture_output=True)
        bad = json.loads(json.dumps(src))
        bad["contexts"]["zz_last"] = {"poison": "zqsecret \u0000 nul"}
        fb = pathlib.Path(d) / "bad.json"
        fb.write_text(json.dumps(bad))
        out4 = imp("--apply", "--from", str(fb), url=URL3)
        with psycopg.connect(URL3) as c3:
            left = c3.execute("select (select count(*) from profile.entry) + (select count(*) from pa.agent_state)"
                              " + (select count(*) from profile.history)").fetchone()[0]
        check("a failure on the last value rolls back every earlier write", out4.returncode != 0 and left == 0,
              f"rc={out4.returncode} left={left} {out4.stdout[-200:]}")
        check("...and says nothing was committed, without the value",
              "nothing was committed" in out4.stdout and "zqsecret" not in out4.stdout + out4.stderr,
              out4.stdout[-200:])
    finally:
        with psycopg.connect(BASE_URL, autocommit=True) as c:
            c.execute(f'drop database if exists "{DB3}" with (force)')

    # Read-back mismatch: run apply() in-process with a service whose get()
    # drops a key, and confirm it rolls back.
    DB4 = f"pa_scratch_profile_{secrets.token_hex(4)}"
    URL4 = urlunparse(_u._replace(path=f"/{DB4}"))
    with psycopg.connect(BASE_URL, autocommit=True) as c:
        c.execute(f'create database "{DB4}"')
    try:
        subprocess.run([sys.executable, str(PA_ROOT / "scripts" / "migrate.py")],
                       env=dict(os.environ, PA_DATABASE_URL=URL4), check=True, capture_output=True)
        imp_spec = importlib.util.spec_from_file_location("_imp", IMP)
        impmod = importlib.util.module_from_spec(imp_spec)
        imp_spec.loader.exec_module(impmod)
        _real_get = ContextService.get
        ContextService.get = lambda self, c: {k: v for k, v in _real_get(self, c).items() if k != "fishing"}
        os.environ["PA_DATABASE_URL"] = URL4
        import contextlib, io  # noqa: E401,E402
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = impmod.apply(f)
        ContextService.get = _real_get
        os.environ["PA_DATABASE_URL"] = URL
        with psycopg.connect(URL4) as c4:
            left = c4.execute("select count(*) from profile.entry").fetchone()[0]
        check("positive control: a read-back that differs rolls the import back",
              rc == 1 and left == 0 and "ROLLED BACK" in buf.getvalue() and "fishing" in buf.getvalue(),
              f"rc={rc} left={left} {buf.getvalue()[-200:]}")
    finally:
        ContextService.get = _real_get if "_real_get" in dir() else ContextService.get
        os.environ["PA_DATABASE_URL"] = URL
        with psycopg.connect(BASE_URL, autocommit=True) as c:
            c.execute(f'drop database if exists "{DB4}" with (force)')

print(f"\n{'=' * 60}")
print(f"{passed} passed, {len(failed)} failed")
for f_ in failed:
    print(f"  - {f_}")
sys.exit(1 if failed else 0)
