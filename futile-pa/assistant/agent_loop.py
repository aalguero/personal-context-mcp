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
import os
from typing import Any, Callable

import anthropic

MODEL = "claude-sonnet-5"

# A loop that never produces a final answer is a bug to see, not something to
# retry silently — hence a hard cap rather than an unbounded while loop.
MAX_TOOL_TURNS = 20


def run(
    system_prompt: str,
    user_message: str,
    tools: list[dict[str, Any]],
    dispatch: Callable[[str, dict[str, Any]], Any],
    *,
    max_turns: int = MAX_TOOL_TURNS,
) -> str:
    """Run one conversation turn to completion, dispatching any tool calls.

    Returns the final assistant text. Raises RuntimeError if max_turns is
    exceeded, and lets an Anthropic API error propagate — both are genuine
    faults the caller should see, not something to paper over.
    """
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]

    for _ in range(max_turns):
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=system_prompt,
            tools=tools,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            return "".join(b.text for b in response.content if b.type == "text")

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
