"""Genericizes outbound Signal messages that reference a task filed under a
sensitive project, so a compromised or stolen phone doesn't expose medical,
financial or estate specifics sitting in the Signal conversation itself.

Per-line, not per-message: a check-in asking three questions where only one
touches a sensitive project should still send the other two legibly, rather
than redacting the whole reply because of one line. The brief's own
convention is to cite a task id in parentheses or as "task N" next to each
item (see any real check-in reply), which is the anchor this uses — not
keyword-scanning the prose itself, which would be both less reliable and
more likely to over-redact.

Heuristic, not exact: matches keywords against each referenced task's
project_path rather than an explicit per-project flag, because that's
cheaper to ship now and extensible later (add a keyword) without a schema
change. Review SENSITIVE_PATH_KEYWORDS periodically — Rosco's care sits
under "Family & Connection / Rosco — Senior Care", not a project literally
named "Health", which is exactly the kind of case a literal top-level-area
match would miss.
"""
from __future__ import annotations

import re

from pa.db import get_db

from .task_ids import extract_task_ids

SENSITIVE_PATH_KEYWORDS = ("health", "finances", "estate", "senior care", "medical")

_LEADING_MARKER = re.compile(r"^(\s*(?:\d+\.|[-*])\s*)")


def filter_outbound(text: str) -> str:
    cache: dict[int, bool] = {}
    lines = []
    for line in text.splitlines():
        ids = _extract_task_ids(line)
        sensitive_ids = sorted(i for i in ids if _is_sensitive(i, cache))
        lines.append(_redact(line, sensitive_ids) if sensitive_ids else line)
    return "\n".join(lines)


def _extract_task_ids(line: str) -> set[int]:
    return extract_task_ids(line)


def _is_sensitive(task_id: int, cache: dict[int, bool]) -> bool:
    if task_id not in cache:
        row = get_db().row("select project_path from pa.v_task where id = %s", (task_id,))
        path = (row or {}).get("project_path") or ""
        cache[task_id] = any(kw in path.lower() for kw in SENSITIVE_PATH_KEYWORDS)
    return cache[task_id]


def _redact(line: str, sensitive_ids: list[int]) -> str:
    marker_match = _LEADING_MARKER.match(line)
    marker = marker_match.group(1) if marker_match else ""
    id_list = ", ".join(str(i) for i in sensitive_ids)
    plural = len(sensitive_ids) > 1
    return f"{marker}Task{'s' if plural else ''} {id_list} need{'' if plural else 's'} an update — reply when you can."
