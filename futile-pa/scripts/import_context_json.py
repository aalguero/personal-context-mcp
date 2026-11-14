#!/usr/bin/env python3
"""Move context.json into Postgres (migration 009), once; or export it back.

    import_context_json.py --check  [--from FILE]   # parse and plan; writes nothing
    import_context_json.py --apply  [--from FILE]   # import, verify, commit, all or nothing
    import_context_json.py --export FILE            # the database, as a context.json (rollback)

--apply runs in ONE transaction: every category and entry is written through
pa/profile.py (so history records actor 'import' for each), agent namespaces go
to pa.agent_state as code, the sensitive tiers are set, and then everything is
read back through the same service and compared value-for-value with the file.
Any difference rolls the whole thing back. It refuses to run if the store
already holds anything, so it cannot be run twice by accident.

--export writes every category, sensitive and agent state included, in the old
file's shape, mode 600, refusing to overwrite. It exists so a rollback to the
pre-009 code loses nothing written after the cutover.

Prints category and key NAMES and counts only, never a value (hard rules 1
and 8: values are medical and financial facts). Reads .env in-process.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from datetime import datetime, timezone

HERE = pathlib.Path(__file__).resolve().parent
PA_ROOT = HERE.parent
REPO = PA_ROOT.parent
sys.path.insert(0, str(PA_ROOT))

DEFAULT_FILE = pathlib.Path.home() / ".personal-context" / "context.json"

# Andre's starting tiers (2026-11-13). Change later with context_category;
# every change is recorded in profile.history.
SENSITIVE = ("health", "investments", "employment_situation")


def _agent_keys():
    import importlib.util
    spec = importlib.util.spec_from_file_location("_agent_keys", PA_ROOT / "assistant" / "agent_keys.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _db():
    from dotenv import load_dotenv
    load_dotenv(REPO / ".env")
    from pa.db import Db
    url = os.environ.get("PA_DATABASE_URL")
    if not url:
        sys.exit("PA_DATABASE_URL is not set (looked in the environment and the repo's .env)")
    return Db(url=url, actor="import", use_pool=False)


def _load(path: pathlib.Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    ctx = data.get("contexts")
    if not isinstance(ctx, dict):
        sys.exit(f"{path}: no 'contexts' object")
    for cat, entries in ctx.items():
        if not isinstance(entries, dict):
            sys.exit(f"{path}: category {cat!r} is not an object")
    return ctx


def _plan(ctx: dict, keys) -> None:
    from pa.profile import SLUG
    bad = [c for c in ctx if not SLUG.match(c)]
    if bad:
        sys.exit(f"category names the new store would refuse: {bad}")
    long_keys = [f"{c}.{k[:40]}..." for c, v in ctx.items() for k in v if not 1 <= len(k) <= 200]
    if long_keys:
        sys.exit(f"keys the new store would refuse (1-200 characters): {long_keys}")
    for cat in sorted(ctx):
        where = ("pa.agent_state" if cat in keys.AGENT_NAMESPACES
                 else "profile [sensitive]" if cat in SENSITIVE else "profile")
        print(f"  {cat:<28} {len(ctx[cat]):>3} keys  -> {where}")
    missing = [c for c in SENSITIVE if c not in ctx]
    if missing:
        print(f"  note: sensitive tier planned for categories not in the file: {missing} (created empty)")


def _store_counts(db) -> tuple[int, int, int]:
    return (db.value("select count(*) from profile.category"),
            db.value("select count(*) from profile.entry"),
            db.value("select count(*) from pa.agent_state"))


def apply(path: pathlib.Path) -> int:
    from pa.profile import IMPORT, ContextService
    keys = _agent_keys()
    ctx = _load(path)
    _plan(ctx, keys)
    db = _db()
    counts = _store_counts(db)
    if any(counts):
        sys.exit(f"REFUSING: the store is not empty (categories, entries, agent_state = {counts}). "
                 "This import runs once.")
    svc = ContextService(IMPORT, keys, db=db)
    mismatched: list[str] = []
    try:
        with db.transaction("import"):
            for cat in sorted(ctx):
                for key, value in ctx[cat].items():
                    svc.set(cat, key, value, code=cat in keys.AGENT_NAMESPACES)
            for cat in SENSITIVE:
                svc.describe(cat, tier="sensitive")
            # Read back through the same service, inside the transaction.
            for cat in sorted(ctx):
                got = svc.get(cat)
                if got != ctx[cat]:
                    diff = sorted(set(got) ^ set(ctx[cat])) + sorted(
                        k for k in set(got) & set(ctx[cat]) if got[k] != ctx[cat][k])
                    mismatched.append(f"{cat}: {diff}")
            tiers = {r["slug"]: r["tier"] for r in
                     db.rows("select slug, tier from profile.category")}
            wrong_tier = [c for c in SENSITIVE if tiers.get(c) != "sensitive"]
            if wrong_tier:
                mismatched.append(f"tier not sensitive: {wrong_tier}")
            if mismatched:
                raise RuntimeError("read-back differs from the file")
    except RuntimeError:
        print("ROLLED BACK: the read-back did not match the file. Differing keys (names only):")
        for m in mismatched:
            print(f"  {m}")
        return 1
    cats, entries, agent = _store_counts(db)
    file_entries = sum(len(v) for c, v in ctx.items() if c not in keys.AGENT_NAMESPACES)
    file_agent = sum(len(v) for c, v in ctx.items() if c in keys.AGENT_NAMESPACES)
    hist = db.value("select count(*) from profile.history where actor = 'import'")
    print(f"committed: {cats} categories, {entries} entries (file: {file_entries}), "
          f"{agent} agent_state rows (file: {file_agent}), {hist} history rows by 'import'")
    print("read-back: every category matched the file value-for-value")
    return 0 if (entries, agent) == (file_entries, file_agent) else 1


def export(path: pathlib.Path) -> int:
    from pa.profile import IMPORT, ContextService
    keys = _agent_keys()
    db = _db()
    svc = ContextService(IMPORT, keys, db=db)
    cats = [r["category"] for r in svc.list_categories()]
    ctx = {c: svc.get(c) for c in cats}
    data = {"meta": {"version": 1,
                     "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                     "exported_from": "postgres, migration 009"},
            "contexts": ctx}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    print(f"exported {len(ctx)} categories, {sum(len(v) for v in ctx.values())} keys to {path} (mode 600)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--apply", action="store_true")
    g.add_argument("--export", metavar="FILE")
    ap.add_argument("--from", dest="src", default=str(DEFAULT_FILE))
    a = ap.parse_args()
    try:
        if a.export:
            return export(pathlib.Path(a.export))
        if a.apply:
            return apply(pathlib.Path(a.src))
        _plan(_load(pathlib.Path(a.src)), _agent_keys())
        print("check only: nothing written")
        return 0
    except FileExistsError:
        print("REFUSING: the export file already exists")
        return 1
    except Exception as e:   # class name only: no values, no DSN
        from pa.profile import ContextError
        detail = f": {e}" if isinstance(e, ContextError) else ""   # worded to carry no value
        print(f"failed ({type(e).__name__}){detail}; nothing was committed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
