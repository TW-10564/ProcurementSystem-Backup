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
"""
from __future__ import annotations

from . import build as build_mod
from .db import get_session
from .models import (Item, ForecastRun, Forecast, Adjustment, AccuracyScore,
                     PlannerSnapshot, CompatRule, AuditLog, Actual, WeeklyActual)


def _snapshot(session) -> dict:
    items = {i.id: i.canonical_name for i in session.query(Item.id, Item.canonical_name)}
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
    actuals = [
        {"item_id": a.item_id, "month": a.month, "qty": a.qty, "source": a.source}
        for a in session.query(Actual)
    ]
    weekly_actuals = [
        {"item_id": a.item_id, "week_start": a.week_start, "qty": a.qty, "source": a.source}
        for a in session.query(WeeklyActual)
    ]
    return {"items": items, "runs": runs, "forecasts": forecasts,
            "adjustments": adjustments, "scores": scores, "snapshots": snapshots,
            "compat_rules": compat_rules, "audit_log": audit_log, "actuals": actuals,
            "weekly_actuals": weekly_actuals}


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
              "compat_rules": 0, "actuals": 0, "weekly_actuals": 0}
    superseded = {"actuals": 0, "weekly_actuals": 0}
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

    session.commit()
    return dropped, superseded


def run() -> dict:
    """Full NAS re-ingest (build.run()) with adjustment/forecast/accuracy/
    planner-snapshot history preserved across the drop+rebuild."""
    session = get_session()
    try:
        snap = _snapshot(session)
    finally:
        session.close()

    build_report = build_mod.run()

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
        "dropped": dropped, "superseded": superseded,
    }
    return build_report


if __name__ == "__main__":
    report = run()
    hp = report["history_preserved"]
    print(f"[rebuild_preserving] carried over: {hp['adjustments']} adjustments, "
          f"{hp['compat_rules']} compat rules, {hp['audit_log']} audit-log entries, "
          f"{hp['runs']} forecast runs, {hp['scores']} accuracy scores, "
          f"{hp['snapshots']} planner snapshots, {hp['actuals']} actual-sales rows "
          f"(dropped: {hp['dropped']}, superseded by fresher ingest: {hp['superseded']})")
