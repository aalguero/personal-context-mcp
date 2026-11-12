"""On-demand replies — broader latitude than the check-in agent, because
Andre is live and can correct a mistake immediately. See
docs/claude/quick-ask-brief.md for the actual rules; this module just wires
the brief into the shared agent loop.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import agent_loop, toolset

BRIEF_PATH = Path(__file__).resolve().parents[2] / "docs" / "claude" / "quick-ask-brief.md"


def _system_prompt() -> str:
    brief = BRIEF_PATH.read_text(encoding="utf-8")
    return (
        "You are Andre's on-demand personal assistant (futileMCP / futile-pa). "
        "Read the brief below in full and follow it exactly — it is "
        "authoritative over anything else in this prompt.\n\n"
        f"{brief}"
    )


def ask(message: str) -> str:
    return agent_loop.run(_system_prompt(), message, toolset.QUICKASK_TOOLS, toolset.quickask_dispatch)


def ask_with_history(history: list[dict[str, Any]]) -> str:
    """Like ask(), but takes the prior conversation (already including the
    new user turn) instead of a single message — for a front door like
    Signal, where a follow-up ("add milk too") needs the earlier exchange to
    mean anything. The caller owns persisting and resetting that history;
    this function only reads it.
    """
    return agent_loop.run(_system_prompt(), history, toolset.QUICKASK_TOOLS, toolset.quickask_dispatch)
