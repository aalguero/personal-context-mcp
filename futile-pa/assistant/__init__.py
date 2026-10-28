"""Standalone assistant backend: the periodic check-in and on-demand replies,
sharing one Anthropic tool-use loop and one Postgres connection.

Importing this package wires up pa.db the same way server.py does at startup
(load .env, add futile-pa/ to sys.path, set_db), so pa.tools.dispatch() works
immediately for anything that imports from here. Deliberately duplicated from
server.py's startup sequence rather than importing server.py itself — see
docs/architecture/standalone-assistant.md — to keep this package off the MCP
SDK's own dependency set.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_REPO_ROOT / ".env")

_FUTILE_PA_PATH = os.environ.get("FUTILE_PA_PATH", str(_REPO_ROOT / "futile-pa"))
if _FUTILE_PA_PATH not in sys.path:
    sys.path.insert(0, _FUTILE_PA_PATH)

from pa.db import Db, set_db  # noqa: E402 — must follow the sys.path fix above

_dsn = os.environ.get("PA_DATABASE_URL")
if not _dsn:
    raise RuntimeError(
        "PA_DATABASE_URL is not set — the assistant has nothing to talk to. "
        "Unlike server.py's context-only fallback, both check-in and on-demand "
        "modes need the database for every call, so this fails at import time "
        "rather than on the first tool call."
    )
set_db(Db(url=_dsn, actor="andre"))
