"""futile-pa — personal assistant backend for the futileMCP server.

Layering, outermost last:

    db.py          connection handling, JSON coercion
    repo.py        queries and commands — the behaviour
    recurrence.py  RRULE expansion and materialisation
    tools.py       MCP tool schemas + dispatch(), framework-agnostic
    server_*.py    thin adapters for whichever MCP framework is in use

Nothing below tools.py knows that MCP exists, so all of it is testable with a
database and no server, and usable from a cron script or psql.
"""

from .db import Db, get_db, set_db  # noqa: F401
from .tools import TOOLS, dispatch, tool_names  # noqa: F401

__version__ = "0.1.0"
