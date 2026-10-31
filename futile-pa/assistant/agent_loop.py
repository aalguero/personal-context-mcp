"""Shared ReAct loop: one Anthropic Messages API tool-use loop, run to a final
text answer.

Not an MCP client — callers hand it a tool registry (Anthropic-shaped tool
definitions plus a dispatch(name, args) function) built from whatever mix of
pa_* tools and context-store tools suits them. The check-in job and on-demand
replies call this same function with different prompts and different tool
registries, rather than each implementing its own loop.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable

import anthropic

MODEL = "claude-sonnet-5"

# A loop that never produces a final answer is a bug to see, not something to
# retry silently — hence a hard cap rather than an unbounded while loop.
MAX_TOOL_TURNS = 20

# The fix-4 deploy found the actual cause of the empty-reply bug the previous
# fix only made loud, not correct: a real run hit stop_reason='max_tokens'
# with block_types=['thinking'] — the model's thinking used the *entire*
# 4096-token budget for that turn, leaving zero room for the answer itself.
# No `thinking` parameter is passed here, so there's no budget to bound
# directly; the robust fix is headroom, not a guess at undocumented defaults.
MAX_TOKENS = 16000

log = logging.getLogger("futile-assistant.agent_loop")


def run(
    system_prompt: str,
    user_message: str | list[dict[str, Any]],
    tools: list[dict[str, Any]],
    dispatch: Callable[[str, dict[str, Any]], Any],
    *,
    max_turns: int = MAX_TOOL_TURNS,
) -> str:
    """Run one conversation turn to completion, dispatching any tool calls.

    user_message is either a single string (the common case: one question,
    no history — checkin.py and quickask.ask() both use this) or a pre-built
    list of {"role", "content"} turns, for a caller that maintains its own
    conversation across calls (the Signal bridge, so "add milk too" after
    "add eggs" has the earlier exchange to refer to). Either way, this
    function's own tool-call turns are local to this one call and never
    leak into what the caller persists.

    Returns the final assistant text. Raises RuntimeError if max_turns is
    exceeded, if the model ends with no text at all, and lets an Anthropic API
    error propagate — all genuine faults the caller should see, not something
    to paper over.

    The empty-final-text case was found by the first real deploy: a run made
    several rounds of tool calls and then stopped with zero text in its last
    turn. checkin.py's own skip check treated that empty string as "nothing to
    record" — a real fault read as a clean no-op, silence as both the success
    and the failure signal. Raising here means the caller's existing exception
    handling (main.py logs and moves on to the next scheduled slot) sees it
    instead of swallowing it.
    """
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    if isinstance(user_message, str):
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
    else:
        messages = list(user_message)

    for turn in range(1, max_turns + 1):
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system_prompt,
            tools=tools,
            messages=messages,
        )
        block_types = [b.type for b in response.content]
        turn_text = "".join(b.text for b in response.content if b.type == "text")
        log.debug("turn %d: stop_reason=%s block_types=%s", turn, response.stop_reason, block_types)
        if turn_text and response.stop_reason == "tool_use":
            # Text accompanying a tool call isn't the final answer, but it's
            # real content a future reader might want — logged, not returned,
            # so the function's contract (one final answer) doesn't change.
            log.info("turn %d produced text alongside a tool call: %s", turn, turn_text)
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            if not turn_text:
                raise RuntimeError(
                    f"Model ended with no text (stop_reason={response.stop_reason!r}, "
                    f"block_types={block_types}) after {turn} turn(s) — treat as a fault, "
                    "not as a deliberate empty answer."
                )
            return turn_text

        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            try:
                result = dispatch(block.name, block.input)
            except Exception as exc:  # genuine fault: surface it to the model, don't crash the loop
                result = {"error": str(exc)}
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result, ensure_ascii=False, default=str),
            })
        messages.append({"role": "user", "content": results})

    raise RuntimeError(f"Exceeded {max_turns} tool-use turns without a final answer")
