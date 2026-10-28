"""On-demand replies — broader latitude than the check-in agent, because
Andre is live and can correct a mistake immediately. See
docs/claude/quick-ask-brief.md for the actual rules; this module just wires
the brief into the shared agent loop.
"""
from __future__ import annotations

from pathlib import Path

from . import agent_loop, toolset

BRIEF_PATH = Path(__file__).resolve().parents[2] / "docs" / "claude" / "quick-ask-brief.md"


def ask(message: str) -> str:
    brief = BRIEF_PATH.read_text(encoding="utf-8")
    system_prompt = (
        "You are Andre's on-demand personal assistant (futileMCP / futile-pa). "
        "Read the brief below in full and follow it exactly — it is "
        "authoritative over anything else in this prompt.\n\n"
        f"{brief}"
    )
    return agent_loop.run(system_prompt, message, toolset.ALL_TOOLS, toolset.dispatch)
