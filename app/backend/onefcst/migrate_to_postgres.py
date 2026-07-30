"""One-shot SQLite → PostgreSQL cutover.

Run:
    cd app/backend
    DATABASE_URL=postgresql+psycopg://user:pw@host:5432/onefcst \\
        python3 -m onefcst.migrate_to_postgres            # dry run (default)
    DATABASE_URL=... python3 -m onefcst.migrate_to_postgres --commit

WHY THIS IS NOT A ROW-BY-ROW COPY
---------------------------------
CLAUDE.md's constraint #2 already says app/data/onefcst.db "is always
regenerated — never treat it as source of truth", and build.run() drops and
rebuilds every table on the weekly schedule anyway. Everything derived from
the NAS sources (items, aliases, plan values, stock, actuals) is therefore
better re-ingested on the Postgres side than copied: a copy would also have
to fix up every id sequence afterwards with setval(), which is a whole
failure mode the re-ingest simply does not have.

What genuinely cannot be regenerated is the human data — confirmed compat
rules, adjustments, order actions, the audit log, EOL/no-reorder flags,
accuracy history. rebuild_preserving._snapshot()/_restore() already move
exactly that set across a drop+rebuild every week, keyed by canonical_name
rather than by id, and are already covered by tests. So this script is a
thin wrapper that points the two halves at two different databases:

    _snapshot(SQLite session) → build.run() on Postgres → _restore(PG session)

SAFETY
------
The SQLite database is opened READ-ONLY and never written. If anything here
fails, the old database is untouched and you simply keep running on it —
that is the rollback. The Postgres side is fully dropped and rebuilt, so it
must be a database you are willing to lose.
"""
from __future__ import annotations

import argparse
import os
import sys

# db.py binds its engine at IMPORT time from config.DATABASE_URL, so the
# environment has to be right before anything under onefcst is imported.
# This module is executed with DATABASE_URL already pointing at Postgres;
# the SQLite side gets its own explicit engine below.
_PG_URL = os.environ.get("DATABASE_URL", "")

from sqlalchemy import create_engine                        # noqa: E402
from sqlalchemy.orm import sessionmaker                     # noqa: E402

from . import build as build_mod                            # noqa: E402
from .config import DB_PATH                                 # noqa: E402
from .db import get_session                                 # noqa: E402
from .rebuild_preserving import _restore, _snapshot         # noqa: E402

# The buckets worth reporting on: everything a human created by hand, plus
# the accuracy history that would take months of real runs to rebuild.
HUMAN_DATA = ("adjustments", "compat_rules", "audit_log", "order_actions",
              "eol_flags", "discontinuation_suggestions", "scores", "runs")


def _sqlite_session(path):
    """Read-only session on the existing SQLite file."""
    engine = create_engine(f"sqlite:///file:{path}?mode=ro&uri=true",
                           future=True)
    return sessionmaker(bind=engine, future=True)()


def _counts(snap: dict) -> dict:
    return {k: len(snap[k]) for k in HUMAN_DATA if k in snap}


def run(commit: bool, sqlite_path=None) -> dict:
    if not _PG_URL or _PG_URL.startswith("sqlite"):
        raise SystemExit(
            "DATABASE_URL must point at PostgreSQL for this migration.\n"
            "  e.g. DATABASE_URL=postgresql+psycopg://user:pw@host:5432/onefcst")

    src = sqlite_path or DB_PATH
    if not os.path.exists(src):
        raise SystemExit(f"no SQLite database at {src}")

    print(f"source (read-only) : {src}")
    print(f"target             : {_PG_URL.split('@')[-1]}")
    print()

    # ---- 1. snapshot the irreplaceable data out of SQLite ----------------
    ssn = _sqlite_session(src)
    try:
        snap = _snapshot(ssn)
    finally:
        ssn.close()

    before = _counts(snap)
    print("human data found in SQLite:")
    for k, v in before.items():
        print(f"    {k:32} {v:>7}")
    print()

    if not commit:
        print("DRY RUN — nothing written. Re-run with --commit to migrate.")
        print("(--commit will DROP and rebuild every table in the target.)")
        return {"status": "dry-run", "counts": before}

    # ---- 2. full re-ingest from the NAS sources, into Postgres -----------
    print("rebuilding from source into Postgres (this takes a few minutes)…")
    build_report = build_mod.run()

    # ---- 3. put the human data back -------------------------------------
    pg = get_session()
    try:
        dropped, superseded = _restore(pg, snap)
    finally:
        pg.close()

    # ---- 4. verify, by re-reading Postgres rather than trusting step 3 ---
    pg = get_session()
    try:
        after = _counts(_snapshot(pg))
    finally:
        pg.close()

    print()
    print(f"{'bucket':32} {'sqlite':>8} {'postgres':>9}   result")
    ok = True
    for k in before:
        got = after.get(k, 0)
        # _restore legitimately drops rows whose item no longer exists in
        # the freshly-ingested registry (a part deleted from the ONE files),
        # so fewer is explainable — more never is.
        if got == before[k]:
            mark = "ok"
        elif got < before[k]:
            mark = f"LOST {before[k] - got} — check `dropped` below"
            ok = False
        else:
            mark = f"UNEXPECTED +{got - before[k]}"
            ok = False
        print(f"{k:32} {before[k]:>8} {got:>9}   {mark}")

    print()
    print(f"dropped (item no longer in registry): {dropped}")
    print(f"superseded (fresh ingest is newer)  : {superseded}")
    print()
    print("MIGRATION COMPLETE" if ok else "MIGRATION FINISHED WITH DISCREPANCIES")
    print("The SQLite file was not modified — it remains a valid rollback.")

    return {"status": "ok" if ok else "discrepancies", "before": before,
            "after": after, "dropped": dropped, "superseded": superseded,
            "build": build_report.get("gate_a", {})}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--commit", action="store_true",
                    help="actually migrate (default is a dry run)")
    ap.add_argument("--sqlite-path", default=None,
                    help="override the source .db path (default: config.DB_PATH)")
    args = ap.parse_args()
    res = run(commit=args.commit, sqlite_path=args.sqlite_path)
    return 0 if res["status"] in ("ok", "dry-run") else 1


if __name__ == "__main__":
    sys.exit(main())
