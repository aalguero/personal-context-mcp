"""The assistant's tool registries: the pa_* tools plus get_context/set_context,
reshaped for the Anthropic Messages API (`input_schema`, not MCP's
`inputSchema`).

Two registries, because the two callers have different jobs (task 212, phase 2):

- ALL_TOOLS / dispatch — the unattended check-in (checkin.py). No shopping
  tools: nothing in its brief needs them until phase 3 asks about staples, and
  an unattended run that can write lists is one more thing to go wrong with
  nobody watching.
- QUICKASK_TOOLS / quickask_dispatch — on-demand replies over Signal
  (quickask.py). Adds the five pa_shop_* tools from pa/shop.py, so "add milk to
  the grocery list" has somewhere to go other than pa_task_add (task 217).

The shop tools are imported from pa.shop by name, never via pa.tools, which
the check-in path takes wholesale. Each registry and its dispatcher agree: the
check-in's dispatch() does not route shop names, so even a hallucinated
pa_shop_* call on that path is an unknown tool, not a write.

list_categories and delete_context are left out on purpose — neither brief
(task-agent-brief.md or quick-ask-brief.md) has a documented use for them, and
an unused tool is one more thing the model could reach for by mistake.
"""
from __future__ import annotations

from typing import Any

from pa.shop import SHOP_TOOL_NAMES as _SHOP_NAMES
from pa.shop import SHOP_TOOLS as _SHOP_TOOLS
from pa.shop import dispatch as _shop_dispatch
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

def _anthropic(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"name": t["name"], "description": t["description"], "input_schema": t["inputSchema"]}
            for t in tools]


ALL_TOOLS: list[dict[str, Any]] = _anthropic(_PA_TOOLS) + _CONTEXT_TOOLS

QUICKASK_TOOLS: list[dict[str, Any]] = ALL_TOOLS + _anthropic(_SHOP_TOOLS)

# A shop tool that shadowed a pa_* or context tool would be silently routed to
# the wrong handler by one of the two dispatchers. Fail at import instead.
_overlap = _SHOP_NAMES & {t["name"] for t in ALL_TOOLS}
if _overlap:
    raise RuntimeError(f"shop tool names collide with the base registry: {sorted(_overlap)}")


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


def quickask_dispatch(name: str, args: dict[str, Any] | None = None) -> Any:
    """dispatch() plus the shop tools. Only quickask.py uses this."""
    if name in _SHOP_NAMES:
        return _shop_dispatch(name, dict(args or {}))
    return dispatch(name, args)
