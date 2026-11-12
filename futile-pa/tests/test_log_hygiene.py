#!/usr/bin/env python3
"""What the agent writes to the journal (hard rule 8).

The journal is not filtered the way outbound Signal is, and model text, tool
data and exception messages can all carry task content: a question about a
task in a health, finance or estate project, a shopping item, a title quoted
in an error. Three places wrote such content until 2026-11-12, and each is a
section here:

1. agent_loop.py logged the model's side text alongside a tool call.
2. checkin.py logged the whole check-in reply, before filter_outbound.
3. main.py and watchdog.py used log.exception, so a traceback (and the
   exception's message) reached the journal.

No database and no network: the Anthropic client, the tool dispatcher,
Signal and the ledger are replaced by fakes. Each section plants marker
strings where content flows and asserts none reaches any record, formatted
the way a handler formats it (so a traceback attached by exc_info counts).
Each has a positive control showing the capture would see a leak.

The assistant package is loaded without running its __init__, which calls
load_dotenv() and set_db() (handoff §9), and the context store is redirected
to a temp directory before anything that could write it is imported.

    python tests/test_log_hygiene.py        # expect "N passed, 0 failed"
"""
from __future__ import annotations

import importlib
import logging
import os
import pathlib
import sys
import tempfile
import types

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
PKG = "_assistant_under_test"
_pkg = types.ModuleType(PKG)
_pkg.__path__ = [str(HERE.parent / "assistant")]
sys.modules[PKG] = _pkg

context_store = importlib.import_module(f"{PKG}.context_store")
_tmp = tempfile.TemporaryDirectory(prefix="log-hygiene-test-")
context_store.STORE_PATH = pathlib.Path(_tmp.name) / "context.json"
if not str(context_store.STORE_PATH).startswith(_tmp.name):
    sys.exit("REFUSING: context store redirect did not take; would write the real store")

agent_loop = importlib.import_module(f"{PKG}.agent_loop")
checkin = importlib.import_module(f"{PKG}.checkin")
watchdog = importlib.import_module(f"{PKG}.watchdog")
main = importlib.import_module(f"{PKG}.main")

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


class Capture(logging.Handler):
    """Every record, formatted as a real handler would: message plus any
    traceback exc_info attaches. Also notes whether exc_info was set at all."""

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
        self.records: list[str] = []
        self.with_exc_info = 0

    def emit(self, record):
        self.records.append(self.format(record))
        if record.exc_info:
            self.with_exc_info += 1


class capturing:
    """Attach a Capture to the root logger at DEBUG for the with-block."""

    def __enter__(self) -> Capture:
        self.cap, self.root = Capture(), logging.getLogger()
        self.level = self.root.level
        self.root.addHandler(self.cap)
        self.root.setLevel(logging.DEBUG)
        return self.cap

    def __exit__(self, *exc):
        self.root.removeHandler(self.cap)
        self.root.setLevel(self.level)
        return False


def block(**kw):
    return types.SimpleNamespace(**kw)


# ===========================================================================
# 1. agent_loop: the model's side text
# ===========================================================================
SIDE = "zqprobe-sidetext beans for the oncologist"
FINAL = "zqprobe-final answer naming task 4242"
ARG = "zqprobe-arg colonoscopy prep"
RESULT = "zqprobe-result estate account"


class FakeClient:
    """Two turns: text + a tool call, then a final answer."""

    def __init__(self, *a, **kw):
        self.calls = 0
        self.messages = self

    def create(self, **kw):
        self.calls += 1
        if self.calls == 1:
            return block(stop_reason="tool_use", content=[
                block(type="text", text=SIDE),
                block(type="tool_use", id="tu1", name="pa_shop_add", input={"name": ARG}),
            ])
        return block(stop_reason="end_turn", content=[block(type="text", text=FINAL)])


section("1. agent_loop: the scripted conversation ran as intended")
dispatched: list[tuple] = []
real_client = agent_loop.anthropic.Anthropic
agent_loop.anthropic.Anthropic = FakeClient
os.environ.setdefault("ANTHROPIC_API_KEY", "not-a-key")
try:
    with capturing() as cap:
        out = agent_loop.run("system", "hello", [],
                             lambda n, a: dispatched.append((n, a)) or {"ok": RESULT})
finally:
    agent_loop.anthropic.Anthropic = real_client
check("the final answer is returned", out == FINAL, repr(out))
check("the tool was dispatched with its argument", dispatched == [("pa_shop_add", {"name": ARG})],
      repr(dispatched))
check("the loop logged something (DEBUG included)", len(cap.records) >= 2, repr(cap.records))

section("1. agent_loop: no model text or tool data reaches the log")
joined = "\n".join(cap.records)
for label, s in (("side text", SIDE), ("final answer", FINAL),
                 ("tool argument", ARG), ("tool result", RESULT)):
    check(f"{label} is not in any record", s not in joined)
check("the side-text line still says it happened, with the length",
      any(f"produced {len(SIDE)} chars of text alongside a tool call" in r for r in cap.records),
      repr(cap.records))

# ===========================================================================
# 2. checkin: the reply
# ===========================================================================
Q_REPLY = ("A couple of things:\n"
           "- zqprobe-question about the biopsy results (4101)\n"
           "- zqprobe-question about the estate filing, task 4102")
QUIET_REPLY = "Nothing to record.\nzqprobe-reasoning that mentions the mammogram"


def run_checkin(reply: str) -> tuple[str, Capture, list[str]]:
    """checkin.run_once with every collaborator faked; returns the reply,
    the capture, and what was sent."""
    store: dict = {}
    sent: list[str] = []

    def fake_dispatch(name, args=None):
        args = args or {}
        if name == "get_context":
            return dict(store)
        if name == "set_context":
            store[args["key"]] = args["value"]
            return {"ok": True}
        raise KeyError(name)

    saved = {k: getattr(checkin, k) for k in
             ("agent_loop", "toolset", "notify", "ledger", "get_db", "filter_outbound", "heartbeat_line")}
    checkin.agent_loop = types.SimpleNamespace(run=lambda *a, **k: reply)
    checkin.toolset = types.SimpleNamespace(dispatch=fake_dispatch, ALL_TOOLS=[])
    checkin.notify = types.SimpleNamespace(send=lambda m, **k: sent.append(m) or True)
    checkin.ledger = types.SimpleNamespace(prune_open_questions=lambda db: None,
                                           record_questions=lambda text, db: None)
    checkin.get_db = lambda: None
    checkin.filter_outbound = lambda t: t
    checkin.heartbeat_line = lambda: "heartbeat"
    try:
        with capturing() as cap:
            out = checkin.run_once()
    finally:
        for k, v in saved.items():
            setattr(checkin, k, v)
    return out, cap, sent


section("2. checkin: a run that asks questions")
out, cap, sent = run_checkin(Q_REPLY)
joined = "\n".join(cap.records)
check("the run completed and sent the reply", out == Q_REPLY and sent == [Q_REPLY], repr(sent)[:120])
check("no question text is in any record", "zqprobe-question" not in joined, joined[:300])
check("the evidence line gives length, outcome and the task ids",
      any(f"Check-in reply: {len(Q_REPLY)} chars, questions, task ids [4101, 4102]" in r
          for r in cap.records), repr(cap.records))

section("2. checkin: a quiet run")
out, cap, sent = run_checkin(QUIET_REPLY)
joined = "\n".join(cap.records)
check("the quiet run sent the heartbeat, not the reply", sent == ["heartbeat"], repr(sent))
check("its reasoning is not in any record", "zqprobe-reasoning" not in joined, joined[:300])
check("the evidence line says nothing to record, with no ids",
      any(f"Check-in reply: {len(QUIET_REPLY)} chars, nothing to record, task ids none" in r
          for r in cap.records), repr(cap.records))

# ===========================================================================
# 3. main and watchdog: exceptions
# ===========================================================================
EXC = "zqprobe-exception message quoting task title 'divorce papers'"


def boom():
    raise ValueError(EXC)


section("3. main._guarded: a failure logs the class name, no message, no traceback")
with capturing() as cap:
    main._guarded("Check-in run", boom)
joined = "\n".join(cap.records)
check("the failure is logged with its class name",
      any("Check-in run failed (ValueError)" in r for r in cap.records), repr(cap.records))
check("the exception message is not in any record", EXC not in joined, joined[:300])
check("no record carries exc_info (no traceback)", cap.with_exc_info == 0)
check("no 'Traceback' text in any record", "Traceback" not in joined)
with capturing() as cap:
    main._guarded("Check-in run", lambda: None)
check("a success logs nothing", cap.records == [], repr(cap.records))

section("3. watchdog.run_checks: same, and one failure doesn't stop the other check")
ran: list[str] = []
saved = (watchdog._check_backup, watchdog._check_checkin)
watchdog._check_backup = boom
watchdog._check_checkin = lambda: ran.append("checkin")
try:
    with capturing() as cap:
        watchdog.run_checks()
finally:
    watchdog._check_backup, watchdog._check_checkin = saved
joined = "\n".join(cap.records)
check("the backup failure is logged with its class name",
      any("Backup staleness check failed (ValueError)" in r for r in cap.records), repr(cap.records))
check("the check-in check still ran after the backup check raised", ran == ["checkin"])
check("the exception message is not in any record", EXC not in joined, joined[:300])
check("no record carries exc_info (no traceback)", cap.with_exc_info == 0)

# ===========================================================================
section("Positive controls: the capture would see each kind of leak")
lg = logging.getLogger("futile-assistant.test")
with capturing() as cap:
    lg.info("turn 1 produced text alongside a tool call: %s", SIDE)
    lg.info("Check-in reply in full:\n%s", Q_REPLY)
    try:
        boom()
    except ValueError:
        lg.exception("Check-in run failed")
joined = "\n".join(cap.records)
check("a side-text leak is caught", SIDE in joined)
check("a full-reply leak is caught", "zqprobe-question" in joined)
check("log.exception is caught: message present", EXC in joined)
check("log.exception is caught: exc_info set and a traceback formatted",
      cap.with_exc_info == 1 and "Traceback" in joined)

print(f"\n{'=' * 60}")
print(f"{passed} passed, {len(failed)} failed")
for f in failed:
    print(f"  - {f}")
sys.exit(1 if failed else 0)
