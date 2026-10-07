"""Database access for the personal-assistant schema.

Deliberately small. An MCP server serving one person does maybe a few hundred
queries a day, so there is no case for an ORM or a heavyweight pool: the cost of
the abstraction would exceed the cost of the queries. What matters instead is

  * connections come back as dicts, because everything here ends up as JSON
    returned to a model;
  * every write records *who* did it, via a Postgres session variable the
    triggers read (see pa.current_actor() in migration 001);
  * dates and Decimals survive the trip to JSON without special-casing at every
    call site.

Pooling: psycopg_pool is used when installed, otherwise a connection is opened
per call. Both are correct; the pool just avoids a TCP + auth round trip on each
tool invocation, which is noticeable when a brief fires a dozen queries.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from contextlib import contextmanager
from decimal import Decimal
from typing import Any, Iterable, Iterator, Sequence

import psycopg
from psycopg.rows import dict_row

try:  # optional
    from psycopg_pool import ConnectionPool  # type: ignore
except Exception:  # pragma: no cover
    ConnectionPool = None  # type: ignore


DEFAULT_SEARCH_PATH = "pa, public"

# Any SELECT run through sql_read is capped, so a careless query cannot wedge
# the server that the rest of the assistant depends on.
READ_TIMEOUT_MS = int(os.environ.get("PA_READ_TIMEOUT_MS", "5000"))


def dsn() -> str:
    """Connection string, from PA_DATABASE_URL or DATABASE_URL.

    Kept as a function rather than a module constant so the environment can be
    set up after import — which is what happens when an MCP server loads its
    config file before wiring tools.
    """
    url = os.environ.get("PA_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "No database configured. Set PA_DATABASE_URL, e.g. "
            "postgresql://user:pass@localhost:5432/futile"
        )
    return url


class Db:
    """Thin handle over a Postgres connection or pool."""

    def __init__(self, url: str | None = None, actor: str = "claude", use_pool: bool = True):
        self.url = url or dsn()
        self.actor = actor
        self._want_pool = use_pool and ConnectionPool is not None
        self._pool = None
        self._tx: psycopg.Connection | None = None   # set inside transaction()

    def _get_pool(self):
        """Create the pool on first use, not in __init__.

        Constructing Db must not start threads or touch the network. It did
        originally, and that had a consequence nobody would predict from reading
        this file: the MCP server calls set_db(Db(...)) during startup, before it
        validates its own configuration. When validation then failed and the
        process called sys.exit(1), the pool's worker threads were already running
        and kept the interpreter alive — so a deliberately-refused start hung
        instead of exiting, and had to be killed.

        Deferring construction also means a bad DSN surfaces on the first query,
        where the error can be reported to the caller, rather than at import time
        where it takes the whole server down.
        """
        if self._pool is None and self._want_pool:
            self._pool = ConnectionPool(
                self.url, min_size=1, max_size=4, open=True,
                kwargs={"row_factory": dict_row},
            )
        return self._pool

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None

    @contextmanager
    def transaction(self, actor: str | None = None) -> Iterator[psycopg.Connection]:
        """Run several helper calls as one atomic unit.

        Without this, each rows()/execute() call takes its own connection and
        therefore its own transaction — so an operation like "insert a task, then
        its context tags" could half-succeed, leaving an untagged task behind.
        Inside this block every call reuses the same connection, so the whole
        sequence commits or rolls back together.

            with db.transaction():
                repo.task_add(...)          # task + contexts + dependencies
        """
        if self._tx is not None:          # already inside one; join it
            yield self._tx
            return
        with self.conn(actor) as c:
            self._tx = c
            try:
                yield c
            except Exception:
                c.rollback()
                raise
            finally:
                self._tx = None

    @contextmanager
    def conn(self, actor: str | None = None) -> Iterator[psycopg.Connection]:
        """Yield a connection with search_path and actor set for this session.

        `SET LOCAL pa.actor` is what makes the event log say "andre" rather than
        "system": the triggers read it through current_setting(). LOCAL scopes it
        to the transaction, so a pooled connection cannot leak an actor into the
        next caller's writes.
        """
        if self._tx is not None:
            yield self._tx
            return
        who = actor or self.actor
        pool = self._get_pool()
        if pool is not None:
            with pool.connection() as c:
                with c.cursor() as cur:
                    cur.execute(f"set local search_path to {DEFAULT_SEARCH_PATH}")
                    cur.execute("select set_config('pa.actor', %s, true)", (who,))
                yield c
        else:
            with psycopg.connect(self.url, row_factory=dict_row) as c:
                with c.cursor() as cur:
                    cur.execute(f"set local search_path to {DEFAULT_SEARCH_PATH}")
                    cur.execute("select set_config('pa.actor', %s, true)", (who,))
                yield c

    # -- query helpers ------------------------------------------------------

    def rows(self, sql: str, params: Sequence[Any] | None = None,
             actor: str | None = None) -> list[dict]:
        with self.conn(actor) as c, c.cursor() as cur:
            cur.execute(sql, params or ())
            return [jsonable(r) for r in cur.fetchall()]

    def row(self, sql: str, params: Sequence[Any] | None = None,
            actor: str | None = None) -> dict | None:
        out = self.rows(sql, params, actor)
        return out[0] if out else None

    def value(self, sql: str, params: Sequence[Any] | None = None,
              actor: str | None = None) -> Any:
        r = self.row(sql, params, actor)
        if r is None:
            return None
        return next(iter(r.values()))

    def execute(self, sql: str, params: Sequence[Any] | None = None,
                actor: str | None = None) -> int:
        with self.conn(actor) as c, c.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.rowcount

    def read_only(self, sql: str, params: Sequence[Any] | None = None,
                  limit: int = 200) -> list[dict]:
        """Run a caller-supplied SELECT with the brakes on.

        Three independent guards, because one is not enough:
          1. the transaction is declared READ ONLY, so Postgres itself refuses
             any write regardless of what the statement text looks like;
          2. a statement timeout, so a cartesian join cannot hang the server;
          3. a row cap applied after the fact.

        Statement-text inspection is *not* one of the guards. Blacklisting
        keywords is easy to fool; a read-only transaction is enforced by the
        database and is not.
        """
        # A dedicated connection, not one from conn(): read_only can only be set
        # before the transaction opens, and conn() has already issued its
        # search_path and actor statements by the time it yields.
        with psycopg.connect(self.url, row_factory=dict_row) as c:
            c.read_only = True
            with c.cursor() as cur:
                cur.execute(f"set local search_path to {DEFAULT_SEARCH_PATH}")
                cur.execute(f"set local statement_timeout = {READ_TIMEOUT_MS}")
                cur.execute(sql, params or ())
                rows = cur.fetchmany(limit)
                return [jsonable(r) for r in rows]


# ---------------------------------------------------------------------------
# JSON coercion
# ---------------------------------------------------------------------------

def _scalar(v: Any) -> Any:
    if isinstance(v, (_dt.datetime,)):
        return v.isoformat()
    if isinstance(v, (_dt.date,)):
        return v.isoformat()
    if isinstance(v, _dt.timedelta):
        return v.total_seconds()
    if isinstance(v, Decimal):
        # float, not str: these are scores and counts, consumed as numbers.
        return float(v)
    if isinstance(v, (list, tuple)):
        return [_scalar(x) for x in v]
    if isinstance(v, dict):
        return {k: _scalar(x) for k, x in v.items()}
    return v


def jsonable(row: dict) -> dict:
    """Coerce a row into something json.dumps will accept, dropping NULLs.

    Dropping NULLs is not cosmetic. These rows are read by a language model
    inside a token budget, and a task row has ~28 columns of which a dozen are
    typically empty. Omitting them roughly halves the payload and removes a pile
    of `"due_at": null` noise that invites the model to comment on absence.
    """
    return {k: _scalar(v) for k, v in row.items() if v is not None}


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


# ---------------------------------------------------------------------------
# Module-level default handle, so tool functions need no plumbing
# ---------------------------------------------------------------------------

_db: Db | None = None


def get_db() -> Db:
    global _db
    if _db is None:
        _db = Db()
    return _db


def set_db(db: Db) -> None:
    """Injection point for tests and for a host that manages its own config."""
    global _db
    _db = db
