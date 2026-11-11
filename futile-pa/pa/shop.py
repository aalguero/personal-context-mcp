"""Shopping lists (task 212, phase 1): queries, commands, and their MCP tools.

The trip is a task. The list is data the trip reads. Items live in
pa.shopping_item and never in pa.task, so nothing here can reach the backlog,
the daily brief or the suggester.

Why this is its own module rather than more entries in tools.py: the agent
(`assistant/toolset.py`) imports `pa.tools.TOOLS` wholesale, so anything added
there joins the agent's tool surface on its next restart. Phase 1 must not
change the agent, so these tools are registered by server.py alone. Phase 2
gives the agent its shopping tools deliberately, by importing SHOP_TOOLS.

Two decisions run through it:

1. **Echo-back replaces ids.** Every question in this system carries a task
   id, which is what makes a reply resolvable. Numbering groceries is absurd,
   so `shop_bought` must say exactly what it matched — and an ambiguous name
   marks nothing and returns the candidates. Guessing is the failure mode.

2. **The rendered read-back contains no task-id-shaped text.** The outbound
   Signal filter treats "(123)" and "task 123" as task ids and may redact the
   whole line. Quantities render as "coffee — 2 lb", and user text that
   happens to contain either shape is neutralised in the rendering only.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable

import psycopg

from .db import Db, get_db
from .repo import Ambiguous, NotFound
from .tools import TOOL_PREFIX, _t

# Store walk order: the order he moves through the shop, per category. A
# placeholder to be tuned with Andre. Sub-categories not listed sort after the
# listed ones, alphabetically; items with no sub-category come last of all.
WALK_ORDER: dict[str, list[str]] = {
    "grocery": ["produce", "bakery", "deli", "butcher", "seafood", "dairy",
                "frozen", "pantry", "snacks", "drinks", "household"],
    "hardware": ["lumber", "fasteners", "tools", "plumbing", "electrical",
                 "paint", "garden"],
}

ITEM_WRITABLE = ("category", "subcategory", "name", "quantity", "store", "notes")

_ACTIVE = "i.bought_at is null and i.removed_at is null"

_ITEM_SELECT = """
    select i.id, i.name, i.category, i.subcategory, i.quantity, i.store, i.notes,
           i.list_id, l.name as list_name, i.added_at, i.bought_at,
           i.removed_at, i.removed_reason
      from pa.shopping_item i
      left join pa.shopping_list l on l.id = i.list_id
"""

# The outbound filter's two task-id shapes; see sensitivity_filter.py.
_PAREN_DIGITS = re.compile(r"\((\s*\d+\s*)\)")
_TASK_WORD_DIGITS = re.compile(r"\b(task)(\s+#?)(\d+)\b", re.IGNORECASE)


def _clean(s: str | None) -> str | None:
    if s is None:
        return None
    s = str(s).strip()
    return s or None


def _key(s: str | None) -> str | None:
    s = _clean(s)
    return s.lower() if s else None


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------

def _find_list(db: Db, ref: Any) -> dict | None:
    """A live list by id or exact (case-insensitive) name. No substring step:
    a near-miss must create a visibly new list, not land in a similar one."""
    if isinstance(ref, int) or str(ref).strip().isdigit():
        row = db.row("select * from pa.shopping_list where id = %s", (int(ref),))
        if not row:
            raise NotFound(f"No shopping list {ref}")
        return row
    return db.row(
        "select * from pa.shopping_list where lower(btrim(name)) = lower(btrim(%s)) and archived_at is null",
        (str(ref),),
    )


def _list_names(db: Db) -> list[str]:
    return [r["name"] for r in db.rows(
        "select name from pa.shopping_list where archived_at is null order by name")]


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def shop_add(name: str, category: str | None = None, subcategory: str | None = None,
             quantity: str | None = None, store: str | None = None,
             notes: str | None = None, list: Any = None, db: Db | None = None) -> dict:
    """Add an item, or update the active one it duplicates.

    The duplicate check is the database's unique index, used through ON
    CONFLICT, so it holds under any writer and any race. On a duplicate,
    quantity/store/subcategory are replaced when given and notes are appended,
    so a second "milk — before Thanksgiving" does not erase the first note.
    """
    db = db or get_db()
    name = _clean(name)
    if not name:
        raise ValueError("name is required")
    created_list = None
    with db.transaction():
        lst = None
        if _clean(list) is not None:
            lst = _find_list(db, list)
            if lst is None:
                # No default category taken from this first item: that would
                # file the camping list's marshmallows under "outdoors".
                lst = db.row(
                    "insert into pa.shopping_list(name) values (%s) returning *",
                    (_clean(list),),
                )
                created_list = lst["name"]
        category = _key(category) or (lst or {}).get("category")
        if not category:
            raise ValueError("category is required (e.g. grocery, hardware), "
                             "unless the list has a default category")
        if db.value("select pa.shop_normalize(%s)", (name,)) == "":
            raise ValueError(f"'{name}' is empty once punctuation is removed")
        row = db.row(
            """
            insert into pa.shopping_item as i
              (list_id, category, subcategory, name, quantity, store, notes)
            values (%s, %s, %s, %s, %s, %s, %s)
            on conflict (coalesce(list_id, 0), category, normalized_name)
              where bought_at is null and removed_at is null
            do update set
              quantity    = coalesce(excluded.quantity, i.quantity),
              store       = coalesce(excluded.store, i.store),
              subcategory = coalesce(excluded.subcategory, i.subcategory),
              notes       = case
                              when excluded.notes is null then i.notes
                              when i.notes is null then excluded.notes
                              when position(lower(excluded.notes) in lower(i.notes)) > 0 then i.notes
                              else i.notes || '; ' || excluded.notes
                            end
            returning i.id, (xmax = 0) as inserted
            """,
            ((lst or {}).get("id"), category, _key(subcategory), name,
             _clean(quantity), _clean(store), _clean(notes)),
        )
        item = _item(db, row["id"])
    where = f"list '{item['list_name']}'" if item.get("list_name") else f"the {category} list"
    if row["inserted"]:
        action = "added"
        msg = f"Added {item['name']} to {where}."
    else:
        action = "updated_existing"
        msg = f"{item['name']} was already on {where}; updated it instead of adding a second."
    if created_list:
        msg = f"Created new list '{created_list}'. " + msg
    return {"action": action, "created_list": created_list, "message": msg, "item": item}


def shop_update(item_id: int, list: Any = None, clear_list: bool = False,
                db: Db | None = None, **fields: Any) -> dict:
    db = db or get_db()
    if item_id is None:
        raise ValueError("item_id is required")
    sets: list[str] = []
    params: list[Any] = []
    for k, v in fields.items():
        if k not in ITEM_WRITABLE:
            raise ValueError(f"Field '{k}' is not writable. Allowed: {', '.join(ITEM_WRITABLE)}, list, clear_list")
        if k in ("category", "subcategory"):
            v = _key(v)
        elif k == "name":
            v = _clean(v)
        if k in ("category", "name") and not v:
            raise ValueError(f"{k} cannot be empty")
        sets.append(f"{k} = %s")
        params.append(v)
    if clear_list and list is not None:
        raise ValueError("pass list or clear_list, not both")
    if list is not None:
        lst = _find_list(db, list)
        if lst is None:
            raise NotFound(f"No shopping list named '{list}'. Lists are created by pa_shop_add. "
                           f"Existing: {', '.join(_list_names(db)) or 'none'}")
        sets.append("list_id = %s")
        params.append(lst["id"])
    if clear_list:
        sets.append("list_id = null")
    if not sets:
        raise ValueError("nothing to update")
    params.append(item_id)
    try:
        n = db.execute(f"update pa.shopping_item set {', '.join(sets)} where id = %s", params)
    except psycopg.errors.UniqueViolation:
        raise ValueError("that would duplicate another active item on the same list "
                         "and category; update or remove that one instead") from None
    except psycopg.errors.CheckViolation:
        raise ValueError("invalid value: category and name must be non-empty") from None
    if n == 0:
        raise NotFound(f"No shopping item {item_id}")
    return {"item": _item(db, item_id)}


def shop_bought(items: Iterable[Any], list: Any = None, category: str | None = None,
                db: Db | None = None) -> dict:
    """Mark items bought, echoing back exactly what was marked."""
    return _close(items, list, category, "bought", None, db or get_db())


def shop_remove(items: Iterable[Any], reason: str | None = None, list: Any = None,
                category: str | None = None, db: Db | None = None) -> dict:
    """Take items off without buying them. Sets removed_at; never deletes."""
    return _close(items, list, category, "removed", _clean(reason), db or get_db())


def _close(items: Iterable[Any], list: Any, category: str | None, how: str,
           reason: str | None, db: Db) -> dict:
    if isinstance(items, (str, int)):
        items = [items]
    items = [i for i in (items or []) if _clean(str(i)) is not None]
    if not items:
        raise ValueError("items is required: one or more names or ids")
    marked: list[dict] = []
    ambiguous: list[dict] = []
    not_found: list[dict] = []
    seen: set[int] = set()
    with db.transaction():
        pool = _active_pool(db, list, category)
        for term in items:
            hits, near = _match(db, term, pool)
            if len(hits) == 1:
                it = hits[0]
                if it["id"] not in seen:
                    seen.add(it["id"])
                    if how == "bought":
                        db.execute("update pa.shopping_item set bought_at = now() where id = %s", (it["id"],))
                    else:
                        db.execute("update pa.shopping_item set removed_at = now(), removed_reason = %s "
                                   "where id = %s", (reason, it["id"]))
                    marked.append(_brief_item(it))
            elif len(hits) > 1:
                ambiguous.append({"asked": str(term), "candidates": [_brief_item(h) for h in hits]})
            else:
                not_found.append({"asked": str(term), "did_you_mean": [_brief_item(h) for h in near]})

    verb = "bought" if how == "bought" else "removed"
    parts = []
    parts.append(f"Marked {verb}: {', '.join(_label(m) for m in marked)}." if marked
                 else f"Nothing marked {verb}.")
    for a in ambiguous:
        parts.append(f"Not marked — '{a['asked']}' matches several: "
                     f"{', '.join(_label(c) for c in a['candidates'])}. Say which, or use the id.")
    for nf in not_found:
        hint = (f" Did you mean {', '.join(_label(c) for c in nf['did_you_mean'])}?"
                if nf["did_you_mean"] else "")
        parts.append(f"Not marked — nothing active matches '{nf['asked']}'.{hint}")
    return {"marked": marked, "ambiguous": ambiguous, "not_found": not_found,
            "message": " ".join(parts)}


def _active_pool(db: Db, list: Any, category: str | None) -> list[dict]:
    where = [_ACTIVE]
    params: list[Any] = []
    if list is not None:
        lst = _find_list(db, list)
        if lst is None:
            raise NotFound(f"No shopping list named '{list}'.")
        where.append("i.list_id = %s")
        params.append(lst["id"])
    if _key(category):
        where.append("i.category = %s")
        params.append(_key(category))
    return db.rows(
        f"""select i.id, i.name, i.normalized_name, i.category, i.list_id, l.name as list_name
              from pa.shopping_item i left join pa.shopping_list l on l.id = i.list_id
             where {' and '.join(where)} order by i.id""",
        params,
    )


def _match(db: Db, term: Any, pool: list[dict]) -> tuple[list[dict], list[dict]]:
    """(hits, near-misses) for one term against the active pool.

    An integer is an id and matches only that id. A name matches an item whose
    normalised name equals it *or contains it as whole words* — and any match
    of either kind counts, so "coffee" against coffee and coffee filters is two
    hits and therefore ambiguous. Whole words, not substrings, so "tea" can
    never mark "steak". Near-misses (items whose name sits inside the term,
    e.g. "coffee beans" vs coffee) are offered, never marked.
    """
    s = str(term).strip()
    if isinstance(term, int) or s.isdigit():
        return [p for p in pool if p["id"] == int(s)], []
    t = db.value("select pa.shop_normalize(%s)", (s,)) or ""
    if not t:
        return [], []
    word = re.compile(r"(?<!\w)" + re.escape(t) + r"(?!\w)")
    hits = [p for p in pool if word.search(p["normalized_name"])]
    near = [] if hits else [
        p for p in pool
        if re.search(r"(?<!\w)" + re.escape(p["normalized_name"]) + r"(?!\w)", t)
    ]
    return hits, near


def _brief_item(it: dict) -> dict:
    out = {"id": it["id"], "name": it["name"], "category": it["category"]}
    if it.get("list_name"):
        out["list"] = it["list_name"]
    return out


def _label(it: dict) -> str:
    extra = it.get("list") or it.get("category")
    return f"{it['name']} — {extra}" if extra else it["name"]


def _item(db: Db, item_id: int) -> dict:
    row = db.row(_ITEM_SELECT + " where i.id = %s", (item_id,))
    if not row:
        raise NotFound(f"No shopping item {item_id}")
    return row


# ---------------------------------------------------------------------------
# Read-back
# ---------------------------------------------------------------------------

def shop_list(category: str | None = None, list: Any = None,
              include_closed: bool = False, db: Db | None = None) -> dict:
    """By category (across every list, annotated) or by list (across categories)."""
    db = db or get_db()
    if category is not None and list is not None:
        raise ValueError("pass category or list, not both")
    where, params, title = [_ACTIVE], [], "Shopping — everything"
    lst = None
    if list is not None:
        lst = _find_list(db, list)
        if lst is None:
            raise NotFound(f"No shopping list named '{list}'. "
                           f"Existing: {', '.join(_list_names(db)) or 'none'}")
        where.append("i.list_id = %s")
        params.append(lst["id"])
        title = f"{lst['name']} list"
    elif _key(category):
        where.append("i.category = %s")
        params.append(_key(category))
        title = f"{_key(category).capitalize()} list"
    items = db.rows(_ITEM_SELECT + f" where {' and '.join(where)} order by i.name", params)
    items = sorted(items, key=_walk_key)
    out: dict[str, Any] = {
        "title": title, "count": len(items), "items": items,
        "rendered": render(title, items, show_list=lst is None, show_category=lst is not None or not _key(category)),
    }
    if include_closed:
        closed_where = [w for w in where if w != _ACTIVE] + ["(i.bought_at is not null or i.removed_at is not null)"]
        out["closed"] = db.rows(
            _ITEM_SELECT + f" where {' and '.join(closed_where)} "
            "order by coalesce(i.bought_at, i.removed_at) desc limit 100", params)
    return out


def _walk_key(it: dict) -> tuple:
    cat = it.get("category") or ""
    sub = it.get("subcategory")
    order = WALK_ORDER.get(cat, [])
    if sub is None:
        pos = (2, "")
    elif sub in order:
        pos = (0, f"{order.index(sub):03d}")
    else:
        pos = (1, sub)
    return (cat, pos, (it.get("name") or "").lower())


def _neutral(s: str) -> str:
    """Strip the two task-id shapes the outbound filter looks for."""
    s = _PAREN_DIGITS.sub(lambda m: m.group(1).strip(), s)
    return _TASK_WORD_DIGITS.sub(lambda m: f"{m.group(1)} no. {m.group(3)}", s)


def render(title: str, items: list[dict], show_list: bool = True, show_category: bool = False) -> str:
    """Compact text, grouped by category then sub-category in walk order.

    One line per item: name — quantity · store · list · notes. No ids, and no
    parenthesised numbers: see the module docstring.
    """
    if not items:
        return f"{title}: nothing on it."
    lines = [title]
    group = None
    for it in items:
        g = ((it.get("category") if show_category else None), it.get("subcategory") or "other")
        if g != group:
            group = g
            head = " / ".join(x for x in g if x)
            lines.append(f"{head.capitalize()}:")
        bits = [b for b in (it.get("quantity"), it.get("store"),
                            (it.get("list_name") if show_list else None), it.get("notes")) if b]
        line = f"  - {it['name']}"
        if bits:
            line += " — " + " · ".join(bits)
        lines.append(line)
    return "\n".join(_neutral(line) for line in lines)


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------

_ITEMS = {"type": "array", "items": {"type": ["string", "integer"]},
          "description": "Item names as he said them, or ids from pa_shop_list."}
_LIST = {"type": ["string", "integer"],
         "description": "A named list such as 'camping trip', by exact name or id."}

SHOP_TOOLS: list[dict] = [
    _t("shop_add", """
       Put something on a shopping list. Shopping items are not tasks: never
       use pa_task_add for groceries or hardware, and never this for anything
       with a deadline — "pick up the turkey before 2pm on the 25th" is a task.
       Category is where it is bought (grocery, hardware, technology,
       outdoors); subcategory is the aisle or counter (produce, butcher,
       plumbing). `list` files it under a named list such as "camping trip",
       which is optional and independent of category; naming a list that does
       not exist creates it, and the reply says so — repeat that back, so a
       misspelt list is caught at once. Adding something already on the same
       list updates it rather than duplicating, and the reply says which
       happened.
       """, {
        "name": {"type": "string"},
        "category": {"type": "string", "description": "grocery, hardware, technology, outdoors… "
                                                      "Optional only when the named list has a default."},
        "subcategory": {"type": "string"},
        "quantity": {"type": "string", "description": "Free text: '2 lb', 'a dozen', 'enough for 8'."},
        "store": {"type": "string", "description": "A specific shop, e.g. Sutter's Meats."},
        "notes": {"type": "string", "description": "Anything else, including timing such as 'before Thanksgiving'."},
        "list": _LIST,
    }, ["name"]),

    _t("shop_list", """
       Read a shopping list back. By category ("my grocery list") it returns
       every active item in that category from every list, each annotated with
       its list — standing in the hardware store he wants the pipe wrench filed
       under "bathroom reno" too. By list ("the camping list") it returns that
       list across categories. Neither gives everything. Show him the
       `rendered` text, which is grouped in the order he walks the store.
       """, {
        "category": {"type": "string"},
        "list": _LIST,
        "include_closed": {"type": "boolean", "default": False,
                           "description": "Also return recently bought or removed items."},
    }),

    _t("shop_update", """
       Change one shopping item by id: rename, recategorise, move to another
       named list (or off lists with clear_list), set quantity, store or
       notes. Notes are replaced, not appended.
       """, {
        "item_id": {"type": "integer"},
        "name": {"type": "string"},
        "category": {"type": "string"},
        "subcategory": {"type": "string"},
        "quantity": {"type": "string"},
        "store": {"type": "string"},
        "notes": {"type": "string"},
        "list": _LIST,
        "clear_list": {"type": "boolean", "description": "Take it off its named list."},
    }, ["item_id"]),

    _t("shop_bought", """
       Mark items bought. Always state back the exact items in `marked` — that
       echo is the only safeguard, since groceries carry no ids in
       conversation. A name that matches more than one item marks nothing and
       comes back under `ambiguous` with the candidates: ask which, do not
       pick one. Narrow with list or category when he says where he was.
       """, {
        "items": _ITEMS,
        "list": _LIST,
        "category": {"type": "string"},
    }, ["items"]),

    _t("shop_remove", """
       Take items off without buying them — changed his mind, the shop was
       out. Different from bought, and the difference matters: it is how a
       staple that keeps being out of stock is told apart from one bought last
       week. Matches names exactly as pa_shop_bought does. Nothing is deleted.
       """, {
        "items": _ITEMS,
        "reason": {"type": "string", "description": "e.g. 'out of stock', 'changed my mind'."},
        "list": _LIST,
        "category": {"type": "string"},
    }, ["items"]),
]

HANDLERS: dict[str, Callable[[dict], Any]] = {
    "shop_add":    lambda a: shop_add(**a),
    "shop_list":   lambda a: shop_list(**a),
    "shop_update": lambda a: shop_update(a.pop("item_id", None), **a),
    "shop_bought": lambda a: shop_bought(**a),
    "shop_remove": lambda a: shop_remove(**a),
}

SHOP_TOOL_NAMES = frozenset(TOOL_PREFIX + k for k in HANDLERS)


def dispatch(name: str, args: dict | None = None) -> Any:
    """Same contract as pa.tools.dispatch: caller errors come back as
    {"error": ...}; genuine faults propagate."""
    key = name[len(TOOL_PREFIX):] if name.startswith(TOOL_PREFIX) else name
    handler = HANDLERS.get(key)
    if handler is None:
        raise KeyError(f"Unknown tool '{name}'. Known: {', '.join(sorted(HANDLERS))}")
    try:
        return handler(dict(args or {}))
    except (Ambiguous, NotFound, ValueError) as exc:
        return {"error": str(exc), "tool": TOOL_PREFIX + key}
    except TypeError as exc:
        # An unexpected argument name; report the shape, not a traceback.
        return {"error": f"bad arguments: {exc}", "tool": TOOL_PREFIX + key}
