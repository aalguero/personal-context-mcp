#!/usr/bin/env python3
"""assistant/agent_loop.py: what the loop writes to the log.

No database and no network. The Anthropic client is replaced by a fake that
replays a scripted conversation, and every log record the loop emits is
captured. The property: text the model produces never reaches the log, only
its length — the journal is not filtered the way outbound Signal is, and that
text names tasks and shopping items (hard rule 8).

The assistant package is loaded without running its __init__, which calls
load_dotenv() and set_db() (handoff §9).

    python tests/test_agent_loop.py        # expect "N passed, 0 failed"
"""
from __future__ import annotations

import importlib
import logging
import os
import pathlib
import sys
import types

HERE = pathlib.Path(__file__).resolve().parent
PKG = "_assistant_under_test"
_pkg = types.ModuleType(PKG)
_pkg.__path__ = [str(HERE.parent / "assistant")]
sys.modules[PKG] = _pkg
agent_loop = importlib.import_module(f"{PKG}.agent_loop")

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


# Strings that must never appear in a log record: one per place model text or
# tool data passes through the loop.
SECRET_SIDE_TEXT = "zqprobe-sidetext beans for the oncologist"
SECRET_FINAL = "zqprobe-final answer naming task 4242"
SECRET_ARG = "zqprobe-arg colonoscopy prep"
SECRET_RESULT = "zqprobe-result estate account"


def block(**kw):
    return types.SimpleNamespace(**kw)


class FakeClient:
    """Two turns: text + a tool call, then a final answer."""

    def __init__(self, *a, **kw):
        self.calls = 0
        self.messages = self

    def create(self, **kw):
        self.calls += 1
        if self.calls == 1:
            return block(stop_reason="tool_use", content=[
                block(type="text", text=SECRET_SIDE_TEXT),
                block(type="tool_use", id="tu1", name="pa_shop_add", input={"name": SECRET_ARG}),
            ])
        return block(stop_reason="end_turn", content=[block(type="text", text=SECRET_FINAL)])


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list[str] = []

    def emit(self, record):
        self.records.append(record.getMessage())


def run_once() -> tuple[str, list[str], list[tuple]]:
    cap = Capture()
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(cap)
    root.setLevel(logging.DEBUG)
    dispatched: list[tuple] = []
    real = agent_loop.anthropic.Anthropic
    agent_loop.anthropic.Anthropic = FakeClient
    os.environ.setdefault("ANTHROPIC_API_KEY", "not-a-key")
    try:
        out = agent_loop.run("system", "hello", [],
                             lambda n, a: dispatched.append((n, a)) or {"ok": SECRET_RESULT})
    finally:
        agent_loop.anthropic.Anthropic = real
        root.removeHandler(cap)
        root.setLevel(old_level)
    return out, cap.records, dispatched


# ---------------------------------------------------------------------------
section("The scripted conversation ran as intended")
out, records, dispatched = run_once()
check("the final answer is returned", out == SECRET_FINAL, repr(out))
check("the tool was dispatched with its argument", dispatched == [("pa_shop_add", {"name": SECRET_ARG})],
      repr(dispatched))
check("the loop logged something (DEBUG included)", len(records) >= 2, repr(records))

# ---------------------------------------------------------------------------
section("No model text or tool data reaches the log")
joined = "\n".join(records)
for label, s in (("side text", SECRET_SIDE_TEXT), ("final answer", SECRET_FINAL),
                 ("tool argument", SECRET_ARG), ("tool result", SECRET_RESULT)):
    check(f"{label} is not in any record", s not in joined)
check("the side-text line still says it happened, with the length",
      any(f"produced {len(SECRET_SIDE_TEXT)} chars of text alongside a tool call" in r for r in records),
      repr(records))

# ---------------------------------------------------------------------------
section("Positive control: the capture would see a leak")
cap = Capture()
agent_loop.log.addHandler(cap)
agent_loop.log.setLevel(logging.DEBUG)
agent_loop.log.info("turn 1 produced text alongside a tool call: %s", SECRET_SIDE_TEXT)
agent_loop.log.removeHandler(cap)
check("the old line's shape, logged through the same logger, is caught",
      any(SECRET_SIDE_TEXT in r for r in cap.records))

print(f"\n{'=' * 60}")
print(f"{passed} passed, {len(failed)} failed")
for f in failed:
    print(f"  - {f}")
sys.exit(1 if failed else 0)
