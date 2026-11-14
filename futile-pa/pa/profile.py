"""The personal context store, in Postgres (migration 009). One implementation,
used by server.py's context tools and by the agent's assistant/context_store.py.

Before this, the store was ~/.personal-context/context.json: two
implementations, four writer processes, a per-process lock each (task 223),
and about one second of history in its .1-.3 rotations. Here every write is
one row upsert in a transaction, and every change to an entry or a category
leaves its old value in profile.history, written by a trigger, so no caller
can skip it.

Two kinds of thing live behind the same four tool names:

  * personal context, in schema `profile`: categories (with a description and
    a tier) and their entries;
  * the agents' own state, in pa.agent_state: the namespaces listed in
    assistant/agent_keys.py (task_agent, task_agent_watchdog,
    recurrence_runner). get_context('task_agent') keeps working, so the
    agent's brief did not have to change.

**Tiers are scoping, not security** (Andre, 2026-11-13). A sensitive category
is left out of an unscoped read and is never readable by the agent scope, so
the unattended check-in cannot repeat health or money into Signal history,
and an unscoped get_context does not drop 20k characters of investments into
a conversation about firewood. Anyone holding PA_AUTH_TOKEN can still ask by
name. Protection against a stolen token or phone is the token's rotation
(scripts/rotate_token.py), not this.

Not a pa.tools module on purpose: assistant/toolset.py takes pa.tools.TOOLS
wholesale, and this must not join the agent's registry by accident.

Values are never logged here. Category and key names are not content.
"""
from __future__ import annotations

import datetime as _dt
import re
from contextlib import contextmanager
from dataclasses import dataclass
from types import ModuleType
from typing import Any, Callable, Iterator

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .db import Db, get_db

SLUG = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
TIERS = ("normal", "sensitive")
MAX_KEY = 200


@dataclass(frozen=True)
class Scope:
    """Who is asking. `name` is recorded as the actor on every write."""
    name: str
    read_sensitive: bool   # by name only; an unscoped read never includes sensitive content
    manage: bool           # may change a category's description or tier


CHAT = Scope("chat", read_sensitive=True, manage=True)     # server.py, PA_AUTH_TOKEN
AGENT = Scope("agent", read_sensitive=False, manage=False)  # both agent paths: check-in and Signal replies
IMPORT = Scope("import", read_sensitive=True, manage=True)  # scripts/import_context_json.py


class ContextError(Exception):
    """A refusal or a validation failure, worded for the caller (a model or a
    person). Never carries a value, so it is safe to return and to log."""


class Withheld(ContextError):
    pass


def _iso(v: Any) -> Any:
    return v.isoformat(timespec="seconds") if isinstance(v, _dt.datetime) else v


class ContextService:
    def __init__(self, scope: Scope, agent_keys: ModuleType,
                 db: Callable[[], Db] | Db = get_db):
        self.scope = scope
        self.agent_keys = agent_keys
        self._db = db

    # -- plumbing -----------------------------------------------------------

    def db(self) -> Db:
        return self._db() if callable(self._db) else self._db

    @contextmanager
    def _cur(self, code: bool = False) -> Iterator[Any]:
        """One transaction. `code` declares the write as code's own, which is
        the only way past pa.agent_state's code-owned-key trigger (task 213)."""
        with self.db().transaction("code" if code else self.scope.name) as c:
            with c.cursor(row_factory=dict_row) as cur:
                if code:
                    cur.execute("select set_config('pa.writer', 'code', true)")
                yield cur

    def _is_agent(self, category: str) -> bool:
        return category in self.agent_keys.AGENT_NAMESPACES

    def _tier(self, cur: Any, category: str) -> str | None:
        cur.execute("select tier from profile.category where slug = %s", (category,))
        r = cur.fetchone()
        return r["tier"] if r else None

    def _check_read(self, category: str, tier: str | None) -> None:
        if tier == "sensitive" and not self.scope.read_sensitive:
            raise Withheld(f"'{category}' is a sensitive category and is not available here.")

    @staticmethod
    def _check_names(category: Any, key: Any = None) -> None:
        if not isinstance(category, str) or not SLUG.match(category):
            raise ContextError("A category name is lowercase letters, digits and underscores, "
                               "starting with a letter (at most 63 characters).")
        if key is not None and (not isinstance(key, str) or not 1 <= len(key) <= MAX_KEY):
            raise ContextError(f"A key is 1 to {MAX_KEY} characters.")

    # -- reads --------------------------------------------------------------

    def get(self, category: str) -> dict:
        """{key: value} for one category; {} if it does not exist."""
        with self._cur() as cur:
            if self._is_agent(category):
                cur.execute("select key, value from pa.agent_state where namespace = %s order by key",
                            (category,))
                return {r["key"]: r["value"] for r in cur.fetchall()}
            self._check_read(category, self._tier(cur, category))
            cur.execute("select key, value from profile.entry where category = %s order by key",
                        (category,))
            return {r["key"]: r["value"] for r in cur.fetchall()}

    def get_all(self) -> dict:
        """Every normal category's entries, plus an index of what an unscoped
        read leaves out: sensitive categories (ask by name) and agent state."""
        with self._cur() as cur:
            cur.execute("""select c.slug, c.tier, c.description, e.key, e.value
                             from profile.category c
                             left join profile.entry e on e.category = c.slug
                            order by c.slug, e.key""")
            contexts: dict[str, dict] = {}
            sensitive: dict[str, str | None] = {}
            for r in cur.fetchall():
                if r["tier"] == "sensitive":
                    sensitive[r["slug"]] = r["description"]
                    continue
                entries = contexts.setdefault(r["slug"], {})
                if r["key"] is not None:
                    entries[r["key"]] = r["value"]
            cur.execute("select distinct namespace from pa.agent_state order by namespace")
            agent = [r["namespace"] for r in cur.fetchall()]
        return {"contexts": contexts,
                "sensitive": [{"category": k, "description": v} for k, v in sensitive.items()],
                "agent_state": agent}

    def list_categories(self) -> list[dict]:
        with self._cur() as cur:
            cur.execute("""select c.slug as category, c.tier, c.description,
                                  count(e.key) as keys,
                                  greatest(c.updated_at, max(e.updated_at)) as updated_at
                             from profile.category c
                             left join profile.entry e on e.category = c.slug
                            group by c.slug order by c.slug""")
            out = [{k: _iso(v) for k, v in r.items()} for r in cur.fetchall()]
            cur.execute("""select namespace as category, count(*) as keys, max(updated_at) as updated_at
                             from pa.agent_state group by namespace order by namespace""")
            out += [{**{k: _iso(v) for k, v in r.items()}, "tier": "agent_state"}
                    for r in cur.fetchall()]
        return out

    def history(self, category: str, key: str | None = None, limit: int = 20) -> list[dict]:
        """Newest first. Category-level events (tier, description, deletion)
        have no key. Agent state keeps no history."""
        self._check_names(category, key)
        if self._is_agent(category):
            raise ContextError(f"'{category}' is agent state, which keeps no history.")
        limit = max(1, min(int(limit), 200))
        with self._cur() as cur:
            tier = self._tier(cur, category)
            if tier is None:
                # A deleted category: its last recorded tier decides.
                cur.execute("""select coalesce(old_value, new_value)->>'tier' as tier from profile.history
                                where category = %s and key is null order by id desc limit 1""",
                            (category,))
                r = cur.fetchone()
                tier = r["tier"] if r else None
            self._check_read(category, tier)
            if key is None:
                cur.execute("""select at, actor, key, action, old_value, new_value from profile.history
                                where category = %s order by id desc limit %s""", (category, limit))
            else:
                cur.execute("""select at, actor, key, action, old_value, new_value from profile.history
                                where category = %s and key = %s order by id desc limit %s""",
                            (category, key, limit))
            return [{k: _iso(v) for k, v in r.items() if v is not None} for r in cur.fetchall()]

    # -- writes -------------------------------------------------------------

    def set(self, category: str, key: str, value: Any, *, code: bool = False) -> None:
        self._check_names(category, key)
        if self._is_agent(category):
            if not code:
                refused = self.agent_keys.refusal(category, key)
                if refused:
                    raise ContextError(refused)
            with self._cur(code) as cur:
                cur.execute("""insert into pa.agent_state(namespace, key, value) values (%s, %s, %s)
                               on conflict (namespace, key) do update
                                 set value = excluded.value, updated_at = now(),
                                     updated_by = excluded.updated_by""",
                            (category, key, Jsonb(value)))
            return
        # A write is allowed to a sensitive category from any scope: it exposes
        # nothing, and history keeps whatever it replaced. The agent scope may
        # record "Andre started a new medication" without being able to read
        # the rest of health.
        with self._cur() as cur:
            cur.execute("insert into profile.category(slug) values (%s) on conflict do nothing",
                        (category,))
            cur.execute("""insert into profile.entry(category, key, value) values (%s, %s, %s)
                           on conflict (category, key) do update
                             set value = excluded.value, updated_at = now(),
                                 updated_by = excluded.updated_by""",
                        (category, key, Jsonb(value)))

    def delete(self, category: str, key: str | None = None) -> bool:
        """Delete one key, or (key None) the whole category and its entries.

        Deleting a category's last key keeps the category, unlike context.json,
        which pruned it: the row carries the tier, and a sensitive category
        that quietly became normal when re-created would be the wrong default.
        """
        self._check_names(category, key)
        if self._is_agent(category):
            refused = self.agent_keys.refusal(category, key, whole_category=key is None)
            if refused:
                raise ContextError(refused)
            with self._cur() as cur:
                if key is None:
                    cur.execute("delete from pa.agent_state where namespace = %s", (category,))
                else:
                    cur.execute("delete from pa.agent_state where namespace = %s and key = %s",
                                (category, key))
                return cur.rowcount > 0
        with self._cur() as cur:
            if key is None:
                cur.execute("delete from profile.category where slug = %s", (category,))
            else:
                cur.execute("delete from profile.entry where category = %s and key = %s",
                            (category, key))
            return cur.rowcount > 0

    def describe(self, category: str, description: str | None = None,
                 tier: str | None = None) -> dict:
        """Create a category, or change its description or tier. With neither
        given, just returns what is there (creating nothing)."""
        self._check_names(category)
        if self._is_agent(category):
            raise ContextError(f"'{category}' is agent state, not a context category.")
        if tier is not None and tier not in TIERS:
            raise ContextError(f"tier is one of: {', '.join(TIERS)}.")
        if (description is not None or tier is not None) and not self.scope.manage:
            raise ContextError("Category settings can only be changed from a chat session.")
        with self._cur() as cur:
            if description is not None or tier is not None:
                cur.execute("""insert into profile.category(slug, description, tier)
                               values (%s, %s, coalesce(%s, 'normal'))
                               on conflict (slug) do update
                                 set description = coalesce(%s, profile.category.description),
                                     tier = coalesce(%s, profile.category.tier),
                                     updated_at = now()""",
                            (category, description, tier, description, tier))
            cur.execute("""select c.slug as category, c.tier, c.description, count(e.key) as keys,
                                  c.created_at, c.updated_at
                             from profile.category c left join profile.entry e on e.category = c.slug
                            where c.slug = %s group by c.slug""", (category,))
            r = cur.fetchone()
        return {k: _iso(v) for k, v in r.items() if v is not None} if r else {}
