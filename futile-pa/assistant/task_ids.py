"""The one definition of "a task id in an outbound message".

The brief's convention is an id in parentheses, "(171)", or "task 171", and
since 2026-11-05 a list numbered by id, "171) Cord 3 of 7 ...". The
sensitivity filter redacts by it and the ledger records asked questions by it
(task 213), so they share it: if they parsed differently, a redacted line
could be asked without being recorded. Kept free of imports so that both,
and the tests, can use it without a database.

The line-start form was added with task 213. Before it, "171) ..." with no
parenthesised id anywhere on the line was invisible to the filter, so a
sensitive task numbered that way went out unredacted. For the filter the
change only widens redaction. A bare ordinal "1)" will read as task 1,
which is why the brief forbids ordinals.
"""
from __future__ import annotations

import re

TASK_ID_PATTERN = re.compile(
    r"\((\d+)\)|\btask\s+#?(\d+)\b|^[ \t]*(\d+)\)", re.IGNORECASE | re.MULTILINE)


def extract_task_ids(text: str) -> set[int]:
    ids: set[int] = set()
    for m in TASK_ID_PATTERN.finditer(text):
        for group in m.groups():
            if group:
                ids.add(int(group))
    return ids
