"""Which task_agent keys code owns (task 213), and the one refusal message.

Two processes write the context store: the agent (futile-assistant.service)
and the MCP server (personal-context-mcp.service, whose set_context and
delete_context the claude.ai chat uses). Both must refuse the same keys, so
the list lives here, once. Since migration 009 the database refuses them too
(pa.agent_state_code_key); tests/test_profile.py asserts the two lists agree.

No imports, on purpose. server.py loads this file by path rather than as
`assistant.agent_keys`, because importing the assistant package runs its
__init__, which calls load_dotenv() and set_db() and would swap the
server's database handle (handoff §9).
"""

CATEGORY = "task_agent"
# The agents' own state: these names route to pa.agent_state, not to the
# personal context in schema profile (pa/profile.py). A new agent namespace
# needs a line here; anything else becomes a personal context category.
AGENT_NAMESPACES = frozenset({"task_agent", "task_agent_watchdog", "recurrence_runner"})
CODE_OWNED_KEYS = frozenset({"asked_recently", "open_questions", "last_reply_at", "unanswered_runs"})


def refusal(category, key=None, *, whole_category=False):
    """The refusal text for a write or delete that would touch a code-owned
    key, or None if it is allowed. whole_category=True is delete_context with
    no key, which would remove all four at once."""
    if category != CATEGORY:
        return None
    if whole_category:
        return (f"Refused: deleting the whole {CATEGORY} category would remove keys "
                f"the check-in agent's code maintains ({', '.join(sorted(CODE_OWNED_KEYS))}). "
                "Delete individual keys instead (task 213).")
    if key in CODE_OWNED_KEYS:
        return (f"{CATEGORY}.{key} is written by code, not by you (task 213): it is "
                "recorded automatically when a check-in is delivered or Andre replies. "
                f"Read it; do not write it. Put your own observations in {CATEGORY}.notes.")
    return None
