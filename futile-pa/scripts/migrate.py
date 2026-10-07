#!/usr/bin/env python3
"""Apply migrations in order, once each, inside transactions.

Deliberately about sixty lines rather than a framework. It tracks what has run in
pa.schema_migration, wraps each file in a single transaction so a failure leaves
nothing half-applied, and refuses to silently re-run a file whose contents have
changed since it was applied.

    python3 scripts/migrate.py                    # apply pending
    python3 scripts/migrate.py --status           # show what has run
    python3 scripts/migrate.py --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

MIGRATIONS = pathlib.Path(__file__).resolve().parent.parent / "migrations"

LEDGER = """
create schema if not exists pa;
create table if not exists pa.schema_migration (
  filename   text primary key,
  sha256     text not null,
  applied_at timestamptz not null default now()
);
"""


def url() -> str:
    u = os.environ.get("PA_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not u:
        sys.exit("Set PA_DATABASE_URL, e.g. postgresql://andre@localhost:5432/futile")
    return u


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    files = sorted(p for p in MIGRATIONS.glob("*.sql"))
    if not files:
        sys.exit(f"No .sql files in {MIGRATIONS}")

    with psycopg.connect(url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(LEDGER)
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("select filename, sha256, applied_at from pa.schema_migration")
            applied = {r["filename"]: r for r in cur.fetchall()}

        if args.status:
            for f in files:
                rec = applied.get(f.name)
                if not rec:
                    print(f"  pending  {f.name}")
                elif rec["sha256"] != _sha(f):
                    print(f"  CHANGED  {f.name}  (applied {rec['applied_at']:%Y-%m-%d %H:%M})")
                else:
                    print(f"  applied  {f.name}  {rec['applied_at']:%Y-%m-%d %H:%M}")
            return 0

        for f in files:
            digest = _sha(f)
            rec = applied.get(f.name)
            if rec:
                if rec["sha256"] != digest:
                    print(f"! {f.name} has changed since it was applied.")
                    print("  Add a new migration file instead of editing an applied one.")
                    return 1
                continue
            if args.dry_run:
                print(f"would apply {f.name}")
                continue
            print(f"applying {f.name} ...", end=" ", flush=True)
            try:
                with conn.cursor() as cur:
                    cur.execute(f.read_text())
                    cur.execute(
                        "insert into pa.schema_migration(filename, sha256) values (%s,%s)",
                        (f.name, digest),
                    )
                conn.commit()
                print("ok")
            except Exception as exc:
                conn.rollback()
                print("FAILED")
                print(f"  {exc}")
                return 1
    print("up to date")
    return 0


def _sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
