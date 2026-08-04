"""Weekly scheduled rebuild that re-ingests from the NAS (build.run(), which
does a full DB drop+rebuild) while preserving the tables build.run() would
otherwise wipe: human Adjustments, confirmed/rejected CompatRules, the
ForecastRun/Forecast run history, the AccuracyScore archive, the
PlannerSnapshot archive that verify/fva.py's run_id-by-timestamp
reconstruction and planner_accuracy.py both depend on, the AuditLog
(Notifications.tsx's feed is a read view over it — see
advisory/notifications.py), and — added 2026-07-15, same day
ingestion/sap_shipments.py started actually writing to it — the real
per-item Actual sales history, plus its weekly-grain sibling
WeeklyActual (added 2026-07-15, same day, for forecast_views.plan_vs_actual).

Actual and WeeklyActual need a different rule from everything else here:
build.run() ALSO writes fresh rows into them itself (the current rolling
4-week SAP shipment window, re-ingested every run — see
ingestion/sap_shipments.py), so by the time _restore() runs, some
(item_id, month-or-week_start, source='SAP') rows already exist for the
weeks that were live in BOTH the old and new ingestion. A preserved row
colliding with one of those must be SKIPPED, not inserted — the freshly
re-ingested value is the newer, more authoritative read of that same SAP
data (matches the "latest snapshot wins" rule used everywhere else in
this codebase, e.g. forecast_views' PlanValue handling) — counted
separately as `superseded` rather than `dropped` (the row isn't lost
because its item disappeared, it's outranked by fresher data for the
exact same key).

Item and ForecastRun primary keys are NOT stable across a rebuild (items are
re-inserted from scratch by build_registry(), in whatever order the source
files iterate), so history rows are translated via each item's
canonical_name (the 綽名 — the registry's natural stable key), not copied by
raw id. Rows for items no longer present after the rebuild are dropped and
counted, not silently kept with a dangling item_id. AuditLog rows have no
item FK (before/after are free-text JSON, not references) so they're
carried over verbatim, with no drop case.

ForecastRun rows are restored with their original created_at preserved
verbatim (not just re-ordered) because verify/fva.py resolves an Adjustment
with run_id=None (the common case — api/main.py's create_adjustment never
sets it) to "whichever run was live at adjustment time" by comparing
ForecastRun.created_at against Adjustment.created_at. Shifting those
timestamps would silently repoint old adjustments at the wrong historical
run.

StockState needs the same supersede-by-fresh-ingest rule as Actual/
WeeklyActual, added 2026-07-23 alongside ingestion/sap_stock_history.py
(manually-placed weekly ZM13 backfill files — see that module). Before
this, build.run() only ever wrote ONE current-day StockState row per
item per rebuild (whatever the live accdb/xlsx source had "today"), so
every rebuild silently discarded all prior weeks' physical-stock history
even though verify/discontinuation.py's zero-stock check already assumed
history would accumulate here "without wiping it" — that assumption did
not actually hold until this fix. The two StockState "kinds" per item
(one_balance from ONE's own simulated projection vs sap_qty from a real
SAP count — see forecast_views._prefer_sap_row) are preserved/deduped
separately by (item_id, snapshot_date, kind), since a fresh rebuild only
ever re-writes both kinds for the CURRENT day, never past dates — so any
preserved row from a past date is always additive, and only a same-day
collision (this rebuild + a prior rebuild run same-day) needs the
supersede-not-duplicate treatment.

Item.eol_flag/marked_no_reorder and discontinuation Suggestion rows (added
2026-07-17, same day advisory/discontinuation_review.py's confirm/reject
queue was built) need BOTH a value restore and a de-dup step, not just a
plain carry-over like everything else here. Two things collide:
  1. Item rows are fully recreated by build_registry() (see above — no
     stable id), so a human's confirmed eol_flag=True LIVES ON THE OLD ROW
     and is lost the instant that row is dropped, unless explicitly
     restored onto the new row by canonical_name — exactly the failure
     this whole module exists to prevent for Adjustment/CompatRule, just
     not yet applied to this newer feature until now.
  2. registry/build.py's step 7 (gray-cell scan) runs INSIDE build_mod.run(),
     which happens BEFORE _restore() gets a chance to put the old
     marked_no_reorder value back — so a fresh Item row always starts that
     scan at marked_no_reorder=False, and a SKU still gray-marked in the
     newly-ingested file will look like a brand-new detection and get a
     fresh draft Suggestion, even if it was already reviewed
     (confirmed/rejected) in an earlier cycle. _restore() must detect and
     remove that redundant fresh draft before reinserting the real
     (already-reviewed) history — the same "supersede, don't duplicate"
     idea Actual/WeeklyActual already use above, applied to a different
     kind of collision.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

from . import build as build_mod
from . import nas_sync
from .db import get_session
from .models import (Item, ForecastRun, Forecast, Adjustment, AccuracyScore,
                     PlannerSnapshot, CompatRule, AuditLog, Actual, WeeklyActual,
                     Suggestion, OrderAction, StockState, UserProfile,
                     UserAccess, UserScope, ItemScopeOverride)


def _lock_down(path: Path) -> None:
    """Make a backup readable only by the user that wrote it (audit F5).

    A database dump is the single most sensitive file this system produces: it
    contains every adjustment, the whole audit trail and the permissions
    roster — i.e. staff names and addresses — in one portable file. Created
    with the default umask it lands world-readable (0644), so any account on
    the DGX could copy the entire database without touching the application or
    leaving a trace in its audit log.

    Best-effort by design: on a filesystem that does not implement POSIX modes
    a failure here must not abort a rebuild that has already succeeded, so it
    warns rather than raising.
    """
    try:
        path.chmod(0o600)
    except OSError as exc:
        print(f"[backup] could not restrict permissions on {path.name}: {exc}")


def _lock_down_dir(path: Path) -> None:
    """Same reasoning for the directory: 0700 stops the FILENAMES (which carry
    timestamps, and so a record of when the system ran) being listed."""
    try:
        path.chmod(0o700)
    except OSError as exc:
        print(f"[backup] could not restrict permissions on {path}/: {exc}")


def _prune_backups(backup_dir: Path, pattern: str) -> int:
    """Shared retention for both backup formats — keep the newest
    ONEFCST_DB_BACKUP_KEEP generations, best-effort."""
    keep = int(os.environ.get("ONEFCST_DB_BACKUP_KEEP", "7"))
    existing = sorted(backup_dir.glob(pattern))
    pruned = 0
    for old in existing[:-keep] if keep > 0 else []:
        try:
            old.unlink()
            pruned += 1
        except OSError:
            pass
    return pruned


def _backup_postgres(database_url: str, backup_dir: Path) -> dict:
    """pg_dump the whole database to a custom-format archive.

    Same three contracts as the SQLite path, deliberately:
      * a FAILED dump raises, aborting the rebuild before anything is
        dropped — the entire point is to never destroy what we cannot
        recreate;
      * a truncated/partial file is deleted rather than left looking like a
        valid backup;
      * the same ONEFCST_DB_BACKUP_KEEP retention applies.

    -Fc (custom format) rather than plain SQL: it is compressed and restores
    selectively with pg_restore, which matters because the thing we actually
    need back in an emergency is a handful of human-data tables, not the
    60k+ rows of regenerable plan values.

    The password goes through PGPASSWORD in the subprocess environment, not
    argv — argv is world-readable via /proc on a shared host.
    """
    from urllib.parse import unquote, urlparse

    u = urlparse(database_url)
    if not u.path or u.path == "/":
        raise RuntimeError(
            f"pre-rebuild backup failed — no database name in DATABASE_URL "
            f"({database_url.split('@')[-1]!r}); refusing to drop tables "
            f"without a backup")

    backup_dir.mkdir(parents=True, exist_ok=True)
    _lock_down_dir(backup_dir)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = backup_dir / f"onefcst-{stamp}.dump"

    cmd = ["pg_dump", "--format=custom", "--no-owner", "--no-acl",
           "--dbname", u.path.lstrip("/"), "--file", str(dest)]
    if u.hostname:
        cmd += ["--host", u.hostname]
    if u.port:
        cmd += ["--port", str(u.port)]
    if u.username:
        cmd += ["--username", unquote(u.username)]

    env = dict(os.environ)
    if u.password:
        env["PGPASSWORD"] = unquote(u.password)

    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True,
                              timeout=1800)
    except FileNotFoundError as e:
        raise RuntimeError(
            "pre-rebuild backup failed — pg_dump not found on PATH. Install "
            "the postgresql-client package; the rebuild is aborted rather "
            "than dropping every table with no backup") from e
    except subprocess.TimeoutExpired as e:
        dest.unlink(missing_ok=True)
        raise RuntimeError(
            "pre-rebuild backup failed — pg_dump timed out after 30 min; "
            "rebuild aborted so the existing data is not dropped") from e

    if proc.returncode != 0 or not dest.exists():
        dest.unlink(missing_ok=True)          # never leave a truncated backup
        raise RuntimeError(
            f"pre-rebuild backup failed (pg_dump exit {proc.returncode}: "
            f"{(proc.stderr or '').strip()[:300]}) — rebuild aborted so the "
            f"existing data is not dropped without a copy")

    # Immediately after the dump exists and before anything else can read it.
    _lock_down(dest)
    pruned = _prune_backups(backup_dir, "onefcst-*.dump")
    return {"status": "ok", "path": str(dest), "bytes": dest.stat().st_size,
            "kept": int(os.environ.get("ONEFCST_DB_BACKUP_KEEP", "7")),
            "pruned": pruned, "format": "pg_dump-custom"}


def backup_db() -> dict:
    """Copy the whole DB to app/data/backups/ BEFORE build.run() drops every
    table. Returns a result dict for the rebuild report; raises only if a
    backup was possible but failed (see below).

    Why this exists: run() below snapshots the irreplaceable human data
    (confirmed compat rules, adjustments, order actions, the audit log,
    accuracy history) into a PYTHON DICT IN MEMORY, then drops all tables,
    rebuilds, and writes it back. If the process dies anywhere between the
    drop and the restore — kill, OOM, machine sleep, container eviction —
    that data is gone permanently: it exists in no other place. The
    concurrency guard in api/main.py stops two rebuilds from overlapping,
    but nothing protected against an interrupted one.

    Uses sqlite3's native backup API rather than shutil.copy: it takes a
    proper read lock and produces a consistent file even if another
    connection is mid-write, which a byte copy does not guarantee.

    A FAILED backup aborts the rebuild (raises). That is deliberate — the
    entire point is to not destroy data we cannot recreate, so "couldn't
    protect it" must not silently proceed to the drop. A full disk becomes
    a loud, fixable error instead of silent loss.

    PostgreSQL deployments take the same guarantee via pg_dump — see
    _backup_postgres. This used to return status="skipped" and let the
    rebuild proceed, which silently removed the entire protection described
    above the moment DATABASE_URL pointed at Postgres: run() would carry on
    to drop_all with no copy of the irreplaceable data anywhere. "skipped"
    is now reserved for the genuinely-nothing-to-lose case.
    """
    from .config import DATABASE_URL, DB_PATH

    if DATABASE_URL and not DATABASE_URL.startswith("sqlite"):
        return _backup_postgres(DATABASE_URL, Path(DB_PATH).parent / "backups")

    src = Path(DB_PATH)
    if not src.exists():
        # first-ever build: nothing to lose yet
        return {"status": "skipped", "reason": "no existing database file"}

    backup_dir = src.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    _lock_down_dir(backup_dir)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = backup_dir / f"{src.stem}-{stamp}.db"

    con = dst = None
    try:
        con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        dst = sqlite3.connect(str(dest))
        con.backup(dst)
    except Exception as e:
        for c in (dst, con):
            if c is not None:
                c.close()
        dest.unlink(missing_ok=True)          # never leave a truncated backup
        raise RuntimeError(
            f"pre-rebuild backup failed ({e!r}) — rebuild aborted so the "
            f"existing data is not dropped without a copy") from e
    finally:
        for c in (dst, con):
            if c is not None:
                c.close()

    # Same as the pg_dump path: a copy of the whole database must not be left
    # world-readable (audit F5).
    _lock_down(dest)

    # Retention: the DB is ~31MB, so keep a bounded number of generations
    # rather than growing without limit on a weekly schedule.
    keep = int(os.environ.get("ONEFCST_DB_BACKUP_KEEP", "7"))
    pruned = _prune_backups(backup_dir, f"{src.stem}-*.db")

    return {"status": "ok", "path": str(dest), "bytes": dest.stat().st_size,
            "kept": keep, "pruned": pruned}


def _snapshot(session) -> dict:
    items = {i.id: i.canonical_name for i in session.query(Item.id, Item.canonical_name)}
    eol_flags = {i.canonical_name: (i.eol_flag, i.marked_no_reorder)
                for i in session.query(Item.canonical_name, Item.eol_flag, Item.marked_no_reorder)
                if i.eol_flag or i.marked_no_reorder}
    discontinuation_suggestions = []
    for s in session.query(Suggestion).filter(Suggestion.kind == "discontinuation"):
        try:
            payload = json.loads(s.payload) if s.payload else {}
        except (TypeError, ValueError):
            payload = {}
        discontinuation_suggestions.append({
            "item_id": payload.get("item_id"), "payload": s.payload,
            "confidence": s.confidence, "status": s.status,
            "reviewed_by": s.reviewed_by, "reviewed_at": s.reviewed_at,
            "created_at": s.created_at,
        })
    runs = [
        {"id": r.id, "method_version": r.method_version,
         "created_at": r.created_at, "note": r.note}
        for r in session.query(ForecastRun)
    ]
    forecasts = [
        {"run_id": f.run_id, "item_id": f.item_id, "month": f.month,
         "baseline": f.baseline, "method": f.method}
        for f in session.query(Forecast)
    ]
    adjustments = [
        {"run_id": a.run_id, "item_id": a.item_id, "month": a.month,
         "old_value": a.old_value, "new_value": a.new_value, "factor": a.factor,
         "scope": a.scope, "reason": a.reason, "author": a.author,
         "created_at": a.created_at}
        for a in session.query(Adjustment)
    ]
    scores = [
        {"run_id": s.run_id, "item_id": s.item_id, "category": s.category,
         "month": s.month, "level": s.level, "mape": s.mape, "wape": s.wape,
         "wape_m1": s.wape_m1, "bias": s.bias}
        for s in session.query(AccuracyScore)
    ]
    snapshots = [
        {"item_id": p.item_id, "month": p.month, "value": p.value,
         "snapshot_date": p.snapshot_date}
        for p in session.query(PlannerSnapshot)
    ]
    compat_rules = [
        {"part_a_id": r.part_a_id, "part_b_id": r.part_b_id, "relation": r.relation,
         "confidence": r.confidence, "origin": r.origin, "status": r.status,
         "confirmed_by": r.confirmed_by, "confirmed_at": r.confirmed_at, "note": r.note}
        for r in session.query(CompatRule)
    ]
    audit_log = [
        {"who": a.who, "what": a.what, "before": a.before, "after": a.after, "at": a.at}
        for a in session.query(AuditLog)
    ]
    # Not derivable from the NAS: these are the Azure display names that let
    # every preserved `who`/`author`/`confirmed_by` email above be shown as a
    # person. Lose them and the whole audit trail reverts to mail aliases
    # until each employee happens to sign in again.
    # The permissions roster is human-entered and unrecoverable from the NAS:
    # losing it in a rebuild would drop every buyer back to read-only and, for
    # the item overrides, forget which owner each unowned part was assigned to.
    user_access = [
        {"email": u.email, "role": u.role, "note": u.note,
         "granted_by": u.granted_by, "created_at": u.created_at,
         "updated_at": u.updated_at}
        for u in session.query(UserAccess)
    ]
    user_scopes = [
        {"email": u.email, "scope_key": u.scope_key, "granted_by": u.granted_by,
         "created_at": u.created_at}
        for u in session.query(UserScope)
    ]
    # Keyed by canonical_name precisely so it survives the item-id churn a
    # rebuild causes.
    item_scope_overrides = [
        {"canonical_name": o.canonical_name, "scope_key": o.scope_key,
         "assigned_by": o.assigned_by, "created_at": o.created_at}
        for o in session.query(ItemScopeOverride)
    ]
    user_profiles = [
        {"email": u.email, "oid": u.oid, "display_name": u.display_name,
         "given_name": u.given_name, "surname": u.surname,
         "job_title": u.job_title, "department": u.department,
         "office_location": u.office_location, "source": u.source,
         "updated_at": u.updated_at, "last_seen_at": u.last_seen_at}
        for u in session.query(UserProfile)
    ]
    actuals = [
        {"item_id": a.item_id, "month": a.month, "qty": a.qty, "source": a.source}
        for a in session.query(Actual)
    ]
    weekly_actuals = [
        {"item_id": a.item_id, "week_start": a.week_start, "qty": a.qty, "source": a.source}
        for a in session.query(WeeklyActual)
    ]
    order_actions = [
        {"item_id": o.item_id, "status": o.status, "qty_ordered": o.qty_ordered,
         "expected_arrival_date": o.expected_arrival_date, "note": o.note,
         "ordered_by": o.ordered_by, "ordered_at": o.ordered_at,
         "received_by": o.received_by, "received_at": o.received_at}
        for o in session.query(OrderAction)
    ]
    stock_states = [
        {"item_id": s.item_id, "raw_name": s.raw_name, "snapshot_date": s.snapshot_date,
         "one_balance": s.one_balance, "one_stock_days": s.one_stock_days,
         "safety_days": s.safety_days, "sap_qty": s.sap_qty}
        for s in session.query(StockState)
    ]
    return {"items": items, "runs": runs, "forecasts": forecasts,
            "adjustments": adjustments, "scores": scores, "snapshots": snapshots,
            "compat_rules": compat_rules, "audit_log": audit_log, "actuals": actuals,
            "weekly_actuals": weekly_actuals, "eol_flags": eol_flags,
            "discontinuation_suggestions": discontinuation_suggestions,
            "order_actions": order_actions, "stock_states": stock_states,
            "user_profiles": user_profiles, "user_access": user_access,
            "user_scopes": user_scopes,
            "item_scope_overrides": item_scope_overrides}


def _restore(session, snap: dict) -> dict:
    new_items = {name: iid for iid, name in
                session.query(Item.id, Item.canonical_name)}

    def item_id(old_id):
        name = snap["items"].get(old_id)
        return new_items.get(name) if name is not None else None

    # ForecastRun first — Forecast/Adjustment/AccuracyScore need the new ids,
    # and created_at must be copied verbatim (see module docstring).
    run_id_map = {}
    dropped = {"forecasts": 0, "adjustments": 0, "scores": 0, "snapshots": 0,
              "compat_rules": 0, "actuals": 0, "weekly_actuals": 0,
              "discontinuation_suggestions": 0, "order_actions": 0, "stock_states": 0}
    superseded = {"actuals": 0, "weekly_actuals": 0, "discontinuation_suggestions": 0,
                 "stock_states": 0}

    # Item.eol_flag/marked_no_reorder — restore onto the NEW row by
    # canonical_name BEFORE anything else touches Suggestion, so a
    # previously human-confirmed discontinuation is never silently lost
    # even if the Suggestion row itself somehow doesn't restore cleanly.
    for name, (eol, no_reorder) in snap["eol_flags"].items():
        new_id = new_items.get(name)
        if new_id is None:
            continue
        item = session.get(Item, new_id)
        if eol:
            item.eol_flag = True
        if no_reorder:
            item.marked_no_reorder = True
    for r in snap["runs"]:
        new_run = ForecastRun(method_version=r["method_version"],
                              created_at=r["created_at"], note=r["note"])
        session.add(new_run)
        session.flush()
        run_id_map[r["id"]] = new_run.id

    for f in snap["forecasts"]:
        iid = item_id(f["item_id"])
        rid = run_id_map.get(f["run_id"])
        if iid is None or rid is None:
            dropped["forecasts"] += 1
            continue
        session.add(Forecast(run_id=rid, item_id=iid, month=f["month"],
                             baseline=f["baseline"], method=f["method"]))

    for a in snap["adjustments"]:
        iid = item_id(a["item_id"])
        if iid is None:
            dropped["adjustments"] += 1
            continue
        rid = run_id_map.get(a["run_id"]) if a["run_id"] is not None else None
        session.add(Adjustment(run_id=rid, item_id=iid, month=a["month"],
                               old_value=a["old_value"], new_value=a["new_value"],
                               factor=a["factor"], scope=a["scope"],
                               reason=a["reason"], author=a["author"],
                               created_at=a["created_at"]))

    for o in snap["order_actions"]:
        iid = item_id(o["item_id"])
        if iid is None:
            dropped["order_actions"] += 1
            continue
        session.add(OrderAction(item_id=iid, status=o["status"], qty_ordered=o["qty_ordered"],
                                expected_arrival_date=o["expected_arrival_date"], note=o["note"],
                                ordered_by=o["ordered_by"], ordered_at=o["ordered_at"],
                                received_by=o["received_by"], received_at=o["received_at"]))

    for s in snap["scores"]:
        iid = item_id(s["item_id"])
        rid = run_id_map.get(s["run_id"]) if s["run_id"] is not None else None
        if iid is None:
            dropped["scores"] += 1
            continue
        session.add(AccuracyScore(run_id=rid, item_id=iid, category=s["category"],
                                  month=s["month"], level=s["level"], mape=s["mape"],
                                  wape=s["wape"], wape_m1=s["wape_m1"], bias=s["bias"]))

    seen_snapshot_keys = set()
    for p in snap["snapshots"]:
        iid = item_id(p["item_id"])
        key = (iid, p["month"], p["snapshot_date"])
        if iid is None or key in seen_snapshot_keys:
            dropped["snapshots"] += 1
            continue
        seen_snapshot_keys.add(key)
        session.add(PlannerSnapshot(item_id=iid, month=p["month"], value=p["value"],
                                    snapshot_date=p["snapshot_date"]))

    seen_rule_keys = set()
    for r in snap["compat_rules"]:
        a_id = item_id(r["part_a_id"])
        b_id = item_id(r["part_b_id"])
        key = (a_id, b_id, r["relation"])
        if a_id is None or b_id is None or key in seen_rule_keys:
            dropped["compat_rules"] += 1
            continue
        seen_rule_keys.add(key)
        session.add(CompatRule(part_a_id=a_id, part_b_id=b_id, relation=r["relation"],
                               confidence=r["confidence"], origin=r["origin"],
                               status=r["status"], confirmed_by=r["confirmed_by"],
                               confirmed_at=r["confirmed_at"], note=r["note"]))

    # AuditLog has no item FK to translate — carried over verbatim, no drop case.
    for a in snap["audit_log"]:
        session.add(AuditLog(who=a["who"], what=a["what"], before=a["before"],
                             after=a["after"], at=a["at"]))

    # UserProfile likewise has no item FK. `.get()` rather than `[...]` so a
    # snapshot taken by an older build (before this table existed) still
    # restores instead of raising KeyError halfway through.
    #
    # Unlike every other bucket here, this table is written by ORDINARY LIVE
    # TRAFFIC (advisory/profiles.py's touch()/apply_graph(), on every /me and
    # Graph sync), not just by rebuilds — a signed-in user can hit /me during
    # the window between build.run()'s drop_all() and this restore step,
    # inserting a fresh row into the now-empty table for their own email. A
    # blind INSERT here then collides with that row on the email unique index
    # (IntegrityError, confirmed in production 2026-07-31 — aborted the whole
    # rebuild over a table that has nothing to do with NAS data). Check for
    # that row first and update it in place instead of assuming the table is
    # still empty.
    existing_profiles = {p.email: p for p in session.query(UserProfile).all()}
    for u in snap.get("user_profiles", []):
        row = existing_profiles.get(u["email"])
        if row is None:
            session.add(UserProfile(**u))
        else:
            for field, value in u.items():
                setattr(row, field, value)

    # Same `.get()` reasoning: a snapshot taken before these tables existed
    # must still restore rather than raise halfway through. Same live-traffic
    # race as user_profiles above is theoretically possible here too (PUT
    # /access and PUT /access/item-scope are admin-only and infrequent, so far
    # narrower a window in practice, but the same unique constraints apply) —
    # check-then-create/update rather than blind insert, for consistency.
    existing_access = {a.email: a for a in session.query(UserAccess).all()}
    for u in snap.get("user_access", []):
        row = existing_access.get(u["email"])
        if row is None:
            session.add(UserAccess(**u))
        else:
            for field, value in u.items():
                setattr(row, field, value)
    existing_scopes = {(s.email, s.scope_key) for s in session.query(UserScope).all()}
    for u in snap.get("user_scopes", []):
        if (u["email"], u["scope_key"]) not in existing_scopes:
            session.add(UserScope(**u))
    existing_overrides = {o.canonical_name: o for o in session.query(ItemScopeOverride).all()}
    for o in snap.get("item_scope_overrides", []):
        row = existing_overrides.get(o["canonical_name"])
        if row is None:
            session.add(ItemScopeOverride(**o))
        else:
            for field, value in o.items():
                setattr(row, field, value)

    # Actual: build.run() already re-inserted fresh SAP-shipment rows for
    # this run's rolling window before we get here — see module docstring
    # for why a colliding preserved row must be skipped (superseded), not
    # inserted (would violate Actual's (item_id, month, source) unique
    # constraint, or silently duplicate for sources that don't enforce it).
    existing_actual_keys = {(a.item_id, a.month, a.source) for a in session.query(Actual)}
    seen_actual_keys = set(existing_actual_keys)
    for a in snap["actuals"]:
        iid = item_id(a["item_id"])
        if iid is None:
            dropped["actuals"] += 1
            continue
        key = (iid, a["month"], a["source"])
        if key in seen_actual_keys:
            superseded["actuals"] += 1
            continue
        seen_actual_keys.add(key)
        session.add(Actual(item_id=iid, month=a["month"], qty=a["qty"], source=a["source"]))

    # WeeklyActual: same supersede-by-fresh-ingest rule as Actual above.
    existing_weekly_keys = {(a.item_id, a.week_start, a.source)
                            for a in session.query(WeeklyActual)}
    seen_weekly_keys = set(existing_weekly_keys)
    for a in snap["weekly_actuals"]:
        iid = item_id(a["item_id"])
        if iid is None:
            dropped["weekly_actuals"] += 1
            continue
        key = (iid, a["week_start"], a["source"])
        if key in seen_weekly_keys:
            superseded["weekly_actuals"] += 1
            continue
        seen_weekly_keys.add(key)
        session.add(WeeklyActual(item_id=iid, week_start=a["week_start"],
                                 qty=a["qty"], source=a["source"]))

    # Discontinuation suggestions: registry/build.py step 7 already ran
    # (inside build_mod.run(), before we got here) against fresh Item rows
    # that all started at marked_no_reorder=False — so a SKU still
    # gray-marked in the new file may have just been given a brand-new
    # draft Suggestion, even though the OLD history below might show it
    # was already confirmed/rejected. That fresh duplicate must be removed
    # before the real history is restored (see module docstring).
    fresh_drafts_by_item: dict[int, list[Suggestion]] = {}
    for fd in session.query(Suggestion).filter(Suggestion.kind == "discontinuation",
                                                Suggestion.status == "draft"):
        try:
            fd_item_id = json.loads(fd.payload).get("item_id") if fd.payload else None
        except (TypeError, ValueError):
            fd_item_id = None
        if fd_item_id is not None:
            fresh_drafts_by_item.setdefault(fd_item_id, []).append(fd)

    for old_s in snap["discontinuation_suggestions"]:
        iid = item_id(old_s["item_id"]) if old_s["item_id"] is not None else None
        if iid is None:
            dropped["discontinuation_suggestions"] += 1
            continue
        for fd in fresh_drafts_by_item.pop(iid, []):
            session.delete(fd)
            superseded["discontinuation_suggestions"] += 1
        # payload's own embedded item_id is the OLD id — rewrite it to the
        # new one so future lookups (list_suggestions/confirm/reject) match
        # the item this restored row actually refers to now.
        try:
            payload = json.loads(old_s["payload"]) if old_s["payload"] else {}
        except (TypeError, ValueError):
            payload = {}
        payload["item_id"] = iid
        session.add(Suggestion(kind="discontinuation", payload=json.dumps(payload, ensure_ascii=False),
                               confidence=old_s["confidence"], status=old_s["status"],
                               reviewed_by=old_s["reviewed_by"], reviewed_at=old_s["reviewed_at"],
                               created_at=old_s["created_at"]))

    # StockState: same supersede-by-fresh-ingest rule as Actual/WeeklyActual
    # above (see module docstring). "kind" distinguishes ONE's simulated
    # projection from a real SAP count, since both can exist for the same
    # (item, date) — see forecast_views._prefer_sap_row.
    def _kind(sap_qty, one_balance):
        if sap_qty is not None:
            return "sap"
        if one_balance is not None:
            return "one"
        return "unknown"

    existing_stock_keys = {(s.item_id, s.snapshot_date, _kind(s.sap_qty, s.one_balance))
                           for s in session.query(StockState)}
    seen_stock_keys = set(existing_stock_keys)
    for s in snap["stock_states"]:
        iid = item_id(s["item_id"])
        if iid is None:
            dropped["stock_states"] += 1
            continue
        key = (iid, s["snapshot_date"], _kind(s["sap_qty"], s["one_balance"]))
        if key in seen_stock_keys:
            superseded["stock_states"] += 1
            continue
        seen_stock_keys.add(key)
        session.add(StockState(item_id=iid, raw_name=s["raw_name"],
                               snapshot_date=s["snapshot_date"], one_balance=s["one_balance"],
                               one_stock_days=s["one_stock_days"], safety_days=s["safety_days"],
                               sap_qty=s["sap_qty"]))

    session.commit()
    return dropped, superseded


def run() -> dict:
    """Full NAS re-ingest (build.run()) with adjustment/forecast/accuracy/
    planner-snapshot history preserved across the drop+rebuild.

    Refreshes the local ONE-source cache from Z: via Windows interop
    first (see nas_sync.py — /mnt/z itself is never live, WSL doesn't
    auto-mount net-use'd drives) so both the manual rebuild button and
    the weekly scheduler (both call this one function) always pull
    current NAS data, not whatever was cached whenever someone last
    happened to run a refresh by hand. Failure here is non-fatal — the
    build proceeds against whatever's already cached locally, and
    build.py's own _check_required_sources() is the real safety net if
    that's not enough."""
    # FIRST, before anything can drop a table: the in-memory snapshot below
    # is the only other copy of the human data, so a crash between the drop
    # and the restore would otherwise lose it permanently. Raises (aborting
    # the rebuild) if a backup was possible but failed — see backup_db().
    db_backup = backup_db()

    nas_refresh = nas_sync.refresh_from_nas()

    session = get_session()
    try:
        snap = _snapshot(session)
    finally:
        session.close()

    # A NAS rebuild deliberately does NOT touch maintenance mode (policy set
    # 2026-07-30). Maintenance mode is now reserved for DEPLOYMENTS — pushing
    # new code or features, where the site really is unusable and an operator
    # decides when it starts and ends. A rebuild is routine (weekly, plus any
    # manual click) and a full-screen lock every time overstated it: the data
    # is briefly unavailable, the site is not down.
    #
    # What replaces it: rebuild_status is in-memory, so /build/rebuild/status
    # keeps answering while the tables are gone, and the frontend shows a
    # non-blocking 「再構築中」 strip driven by it (MaintenanceGate). Users
    # keep whatever already loaded instead of being thrown out.
    #
    # If an operator HAS put the site in maintenance, we leave it exactly as
    # found — this code no longer reads or writes that flag at all, so there
    # is no path by which finishing a rebuild opens a deliberate outage back
    # up.
    #
    # Cost of the change, on PostgreSQL only: maintenance mode used to stop
    # new readers arriving, which kept them from queueing behind the DROP's
    # ACCESS EXCLUSIVE lock. Without it a long in-flight report session can
    # still block the drop — that now surfaces as build.py's explicit
    # "could not drop tables within 15s … the database is intact" abort,
    # which is a safe failure (nothing dropped) and names maintenance mode as
    # the remedy. Prefer that over locking everyone out on every rebuild.
    build_report = build_mod.run()
    build_report["nas_refresh"] = nas_refresh
    build_report["db_backup"] = db_backup

    session = get_session()
    try:
        dropped, superseded = _restore(session, snap)
    finally:
        session.close()

    build_report["history_preserved"] = {
        "runs": len(snap["runs"]), "forecasts": len(snap["forecasts"]),
        "adjustments": len(snap["adjustments"]), "scores": len(snap["scores"]),
        "snapshots": len(snap["snapshots"]), "compat_rules": len(snap["compat_rules"]),
        "audit_log": len(snap["audit_log"]), "actuals": len(snap["actuals"]),
        "weekly_actuals": len(snap["weekly_actuals"]),
        "eol_flags": len(snap["eol_flags"]),
        "discontinuation_suggestions": len(snap["discontinuation_suggestions"]),
        "order_actions": len(snap["order_actions"]),
        "stock_states": len(snap["stock_states"]),
        "user_profiles": len(snap["user_profiles"]),
        "user_access": len(snap["user_access"]),
        "user_scopes": len(snap["user_scopes"]),
        "item_scope_overrides": len(snap["item_scope_overrides"]),
        "dropped": dropped, "superseded": superseded,
    }
    return build_report


if __name__ == "__main__":
    report = run()
    hp = report["history_preserved"]
    print(f"[rebuild_preserving] carried over: {hp['adjustments']} adjustments, "
          f"{hp['compat_rules']} compat rules, {hp['audit_log']} audit-log entries, "
          f"{hp['runs']} forecast runs, {hp['scores']} accuracy scores, "
          f"{hp['snapshots']} planner snapshots, {hp['actuals']} actual-sales rows, "
          f"{hp['order_actions']} order actions, {hp['stock_states']} stock-state rows "
          f"(dropped: {hp['dropped']}, superseded by fresher ingest: {hp['superseded']})")
