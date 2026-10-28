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
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STORE_PATH = Path.home() / ".personal-context" / "context.json"


def get(category: str | None = None) -> dict:
    ctx = _read().get("contexts", {})
    return ctx.get(category, {}) if category else ctx


def set(category: str, key: str, value: Any) -> None:
    data = _read()
    data.setdefault("contexts", {}).setdefault(category, {})[key] = value
    data.setdefault("meta", {"version": 1})["updated_at"] = _now()
    _write(data)


def _read() -> dict:
    try:
        return json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"meta": {"version": 1, "updated_at": _now()}, "contexts": {}}


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
