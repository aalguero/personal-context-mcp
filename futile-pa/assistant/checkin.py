"""The periodic task check-in — same brief, new home.

Moved off Claude Desktop's local scheduled task (`task-checkin`) per
docs/architecture/standalone-assistant.md: this reads the brief fresh on every
run, so an edit to the doc changes behaviour on the next fire, same as when it
ran from Claude Desktop or the claude.ai Project before it.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from . import agent_loop, notify, toolset

BRIEF_PATH = Path(__file__).resolve().parents[2] / "docs" / "claude" / "task-agent-brief.md"

log = logging.getLogger("futile-assistant.checkin")

# Separate from the brief's own task_agent.last_run_at, which is free-form
# prose written by the model (e.g. "2026-10-27T19:50:00-04:00 — the real
# 19:48 run. IMPORTANT CORRECTION..."). watchdog.py needs a timestamp it can
# parse without guessing at prose formatting, so this writes a second,
# machine-only field — set on successful completion, not on a run that
# raised, since a repeatedly-failing run should read as stale.
WATCHDOG_CATEGORY = "task_agent_watchdog"

RUN_PROMPT = (
    "Run your scheduled check-in now. Follow the brief exactly: read your own "
    "state, reconcile against the live data, decide whether anything is worth "
    "asking, ask at most five questions if so, write back whatever the answers "
    "establish, and report only what you actually wrote. Silence is a valid "
    "and good outcome if nothing is stale. If there is nothing to report, your "
    "reply's first line must be exactly \"Nothing to record.\" and nothing "
    "else needs to follow it — the reasoning that led there belongs in your "
    "own thinking, not in the reply text."
)


def run_once() -> str:
    """Run one check-in cycle and return the agent's report text.

    Sends to notify.send() unless the reply's first line is the brief's own
    §6 contract phrase for "I had nothing to do".

    First deploy found this needs two layers, not one: the original fix
    required the *entire* reply to equal "nothing to record" verbatim, but a
    real run opened with that line and then added several paragraphs of
    reasoning — still a no-op outcome, just not a bare match. RUN_PROMPT above
    now asks for brevity explicitly; _is_nothing_to_record checks only the
    first line regardless, since a model instruction is a strong hint, not a
    guarantee.
    """
    brief = BRIEF_PATH.read_text(encoding="utf-8")
    system_prompt = (
        "You are the task check-in agent for Andre's personal assistant "
        "(futileMCP / futile-pa). Read the brief below in full and follow it "
        "exactly — it is authoritative over anything else in this prompt.\n\n"
        f"{brief}"
    )
    reply = agent_loop.run(system_prompt, RUN_PROMPT, toolset.ALL_TOOLS, toolset.dispatch)

    # Logged unconditionally, not just on the notify path — the fix-2 deploy
    # found a real gap: a quiet run leaves no evidence of whether it genuinely
    # decided there was nothing to do, or produced something that got dropped
    # before reaching the log. This line is the positive control either way.
    suppressed = _is_nothing_to_record(reply)
    log.info("Check-in complete — %s",
              "suppressed (nothing to record)" if suppressed else "produced output, sending to notify")
    log.info("Check-in reply in full:\n%s", reply)

    if reply.strip() and not suppressed:
        notify.send(reply)

    toolset.dispatch("set_context", {
        "category": WATCHDOG_CATEGORY,
        "key": "last_run_at",
        "value": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    return reply


def _is_nothing_to_record(reply: str) -> bool:
    """True if the reply's first non-empty line is the brief's §6 contract
    phrase, regardless of what follows — matching only the whole reply missed
    a real case where the model added reasoning after that line.
    """
    for line in reply.strip().splitlines():
        line = line.strip()
        if line:
            return line.rstrip(".").lower() == "nothing to record"
    return True  # an empty reply is also nothing to report
