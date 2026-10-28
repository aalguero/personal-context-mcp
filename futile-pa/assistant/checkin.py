"""The periodic task check-in — same brief, new home.

Moved off Claude Desktop's local scheduled task (`task-checkin`) per
docs/architecture/standalone-assistant.md: this reads the brief fresh on every
run, so an edit to the doc changes behaviour on the next fire, same as when it
ran from Claude Desktop or the claude.ai Project before it.
"""
from __future__ import annotations

from pathlib import Path

from . import agent_loop, notify, toolset

BRIEF_PATH = Path(__file__).resolve().parents[2] / "docs" / "claude" / "task-agent-brief.md"

RUN_PROMPT = (
    "Run your scheduled check-in now. Follow the brief exactly: read your own "
    "state, reconcile against the live data, decide whether anything is worth "
    "asking, ask at most five questions if so, write back whatever the answers "
    "establish, and report only what you actually wrote. Silence is a valid "
    "and good outcome if nothing is stale."
)


def run_once() -> str:
    """Run one check-in cycle and return the agent's report text.

    Always sends whatever text comes back to notify.send() when non-empty —
    the brief's own §4/§8 rules decide whether that text is a real ask or just
    "nothing to record"; this function doesn't second-guess that.
    """
    brief = BRIEF_PATH.read_text(encoding="utf-8")
    system_prompt = (
        "You are the task check-in agent for Andre's personal assistant "
        "(futileMCP / futile-pa). Read the brief below in full and follow it "
        "exactly — it is authoritative over anything else in this prompt.\n\n"
        f"{brief}"
    )
    reply = agent_loop.run(system_prompt, RUN_PROMPT, toolset.ALL_TOOLS, toolset.dispatch)
    if reply.strip():
        notify.send(reply)
    return reply
