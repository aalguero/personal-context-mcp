"""The agent's handle on the context store: get/set, as before, now over
Postgres through pa/profile.py (migration 009).

Before 009 this was a second implementation of context.json's
read-modify-write, duplicated from server.py and locked only within this
process (task 223). Now both use the one ContextService. The agent's scope is
pa.profile.AGENT: it never reads a sensitive category, and the check-in and
the Signal replies both go through here, so neither can repeat health or
money into Signal history.

Code that owns a key says so: ledger.py passes code=True, which is the only
way past the database's own refusal of task 213's keys
(pa.agent_state_code_key). A model's set_context, through toolset.dispatch(),
never does.

MemoryBackend exists for the DB-free suites (ledger, toolset, staples,
log_hygiene), which install it with use_backend() and refuse to run unless it
took. pa is imported lazily, so those suites never need psycopg's database.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from . import agent_keys

log = logging.getLogger("futile-assistant.context_store")

# Kept for importers that catch it. A database store cannot be half-written
# the way a file could, so nothing raises it any more.
class CorruptStore(RuntimeError):
    pass


class MemoryBackend:
    """A dict standing in for the store, for tests. Same get/set shape, and
    the same refusal the database makes: a code-owned key written without
    code=True raises, so a suite on this backend still catches a ledger write
    that forgot to declare itself (pa.agent_state_guard in migration 009)."""

    def __init__(self) -> None:
        self.data: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()
        self.code_writes: list[tuple[str, str]] = []

    def get(self, category: str | None) -> dict:
        with self.lock:
            if category:
                return dict(self.data.get(category, {}))
            return {c: dict(v) for c, v in self.data.items()}

    def set(self, category: str, key: str, value: Any, code: bool) -> None:
        if not code and agent_keys.refusal(category, key):
            raise PermissionError(f"{category}.{key} is code-owned (task 213): refused")
        with self.lock:
            if code:
                self.code_writes.append((category, key))
            self.data.setdefault(category, {})[key] = value


class _DbBackend:
    def __init__(self) -> None:
        self._svc = None

    def svc(self):
        if self._svc is None:
            from pa.profile import AGENT, ContextService
            self._svc = ContextService(AGENT, agent_keys)
        return self._svc

    def get(self, category: str | None) -> dict:
        if category:
            return self.svc().get(category)
        return self.svc().get_all()["contexts"]

    def set(self, category: str, key: str, value: Any, code: bool) -> None:
        self.svc().set(category, key, value, code=code)


_backend: Any = _DbBackend()


def use_backend(backend: Any) -> None:
    """Injection point for tests."""
    global _backend
    _backend = backend


def backend() -> Any:
    return _backend


def get(category: str | None = None) -> dict:
    """A read degrades to {} on a database failure, as a corrupt file used
    to: a watchdog reading {} finds its stamp missing and alerts, which is the
    right outcome. A refusal (a sensitive category) is not a failure and is
    raised for the caller to word. Class names only in the log."""
    from_pa = _pa_errors()
    try:
        return _backend.get(category)
    except from_pa:
        raise
    except Exception as exc:
        log.error("Context store read failed (%s); returning empty for this call only",
                  type(exc).__name__)
        return {}


def set(category: str, key: str, value: Any, *, code: bool = False) -> None:
    """Raises on failure, and raises ContextError on a refusal."""
    _backend.set(category, key, value, code)


def _pa_errors() -> tuple[type[BaseException], ...]:
    if isinstance(_backend, MemoryBackend):
        return ()
    from pa.profile import ContextError
    return (ContextError,)
