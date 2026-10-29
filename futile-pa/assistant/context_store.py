"""Minimal get/set against the same context.json the MCP server reads and
writes (`get_context`/`set_context` are implemented in server.py, not pa/, so
there is no module to import them from).

Deliberately duplicated rather than imported from server.py — see
docs/architecture/standalone-assistant.md — to keep this package off the MCP
SDK's dependency set. Same atomic-write discipline as server.py's
ContextStore (temp file, fsync, os.replace, rolling backups), because two
separate processes now touch this file.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STORE_PATH = Path.home() / ".personal-context" / "context.json"

log = logging.getLogger("futile-assistant.context_store")


class CorruptStore(RuntimeError):
    """The store file exists but couldn't be parsed. Distinct from a missing
    file, which is safe to treat as empty — this is not, since treating a
    corrupt file as empty is how set() would overwrite real data (including
    medical and financial facts) with a near-empty one. See _read_or_default.
    """


def get(category: str | None = None) -> dict:
    """Read-only, so a corrupt store degrades to empty rather than raising —
    there's nothing to lose on a read. The corruption itself is still logged
    loudly; a caller silently getting {} for a category that should have data
    is a problem worth a human noticing, just not a reason to crash a read.
    """
    try:
        ctx = _read_or_default().get("contexts", {})
    except CorruptStore as exc:
        log.error("Context store unreadable on a read (%s) — returning empty "
                  "for this call only, not overwriting anything.", exc)
        ctx = {}
    return ctx.get(category, {}) if category else ctx


def set(category: str, key: str, value: Any) -> None:
    """Deliberately NOT forgiving like get() — if the store exists but can't
    be parsed, this must not proceed to write a merged "empty + new key"
    structure over it. _read_or_default raises in that case; let it propagate
    rather than catching and defaulting, which is the exact bug this guards
    against.
    """
    data = _read_or_default()
    data.setdefault("contexts", {}).setdefault(category, {})[key] = value
    data.setdefault("meta", {"version": 1})["updated_at"] = _now()
    _write(data)


def _read_or_default() -> dict:
    """Returns the empty default only when the file genuinely doesn't exist
    yet — nothing to lose there. If it exists but can't be read or parsed,
    raises CorruptStore instead of silently treating it as empty: every
    successful check-in now calls set() four times a day (the watchdog
    timestamp), so a single unreadable moment followed by that write would
    otherwise overwrite real data with a near-empty file, recoverable only
    from the rotated .1-.3 backups.
    """
    if not STORE_PATH.exists():
        return {"meta": {"version": 1, "updated_at": _now()}, "contexts": {}}
    try:
        return json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise CorruptStore(f"{STORE_PATH} exists but could not be read/parsed: {exc}") from exc


def _write(data: dict) -> None:
    _rotate_backups()
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    fd, tmp = tempfile.mkstemp(dir=str(STORE_PATH.parent), prefix=".context-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, STORE_PATH)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _rotate_backups(keep: int = 3) -> None:
    if not STORE_PATH.exists():
        return
    for i in range(keep - 1, 0, -1):
        src = STORE_PATH.with_suffix(STORE_PATH.suffix + f".{i}")
        dst = STORE_PATH.with_suffix(STORE_PATH.suffix + f".{i + 1}")
        if src.exists():
            os.replace(src, dst)
    shutil.copy2(STORE_PATH, STORE_PATH.with_suffix(STORE_PATH.suffix + ".1"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
