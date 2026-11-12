"""The combined tool registry for the assistant: the 25 pa_* tools plus
get_context/set_context, reshaped for the Anthropic Messages API
(`input_schema`, not MCP's `inputSchema`) and routed through one dispatch().

list_categories and delete_context are left out on purpose — neither brief
(task-agent-brief.md or quick-ask-brief.md) has a documented use for them, and
an unused tool is one more thing the model could reach for by mistake.
"""
from __future__ import annotations

from typing import Any

from pa.tools import TOOLS as _PA_TOOLS
from pa.tools import dispatch as _pa_dispatch

from . import context_store as _ctx
from . import ledger as _ledger

_CONTEXT_TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_context",
        "description": (
            "Retrieve personal context about the user. Call with no arguments "
            "to get everything, or pass a category (e.g. 'task_agent') to get "
            "only that section."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"category": {"type": "string"}},
            "required": [],
        },
    },
    {
        "name": "set_context",
        "description": (
            "Store or update a piece of personal context. Provide a category "
            "(e.g. 'task_agent'), a key, and the value. Creates the category "
            "if it doesn't exist."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string"},
                "key": {"type": "string"},
                "value": {},
            },
            "required": ["category", "key", "value"],
        },
    },
]

ALL_TOOLS: list[dict[str, Any]] = [
    {"name": t["name"], "description": t["description"], "input_schema": t["inputSchema"]}
    for t in _PA_TOOLS
] + _CONTEXT_TOOLS


def dispatch(name: str, args: dict[str, Any] | None = None) -> Any:
    args = dict(args or {})
    if name == "get_context":
        return _ctx.get(args.get("category"))
    if name == "set_context":
        # dispatch() is the model's route to the store. Code writes the
        # ledger's keys through context_store directly, so this refusal
        # applies only to the model (task 213).
        refused = _ledger.refuse_model_write(args.get("category"), args.get("key"))
        if refused:
            return refused
        _ctx.set(args["category"], args["key"], args["value"])
        return {"ok": True}
    return _pa_dispatch(name, args)
