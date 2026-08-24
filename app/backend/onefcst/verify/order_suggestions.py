"""M5 — generate order suggestions from live data and sanity-check them
against real purchasing history (ingestion/legacy_po.py's PO reader, copied
in-house 2026-07-14 from the frozen consolidation/ POC's purchasing.py — see
ingestion/subfile_config.py's docstring for why).

Honesty note on the exit test: POC_BUILD_PLAN's exit criterion asks
suggestions to "replicate purchasing's actual recent orders on historical
snapshots." This system has only ONE live snapshot so far (no accumulated
history of its own runs yet), so a true point-in-time backtest isn't
possible today — that becomes possible once the system has been running and
recording its own monthly snapshots. What IS possible now, and is what this
module does, is an honest PLAUSIBILITY check: compute today's suggestion for
every item that has a recent real PO, and compare suggested quantity against
what was actually ordered. Large, unexplained gaps are worth investigating;
close agreement is real (if partial) evidence the logic is sound.
"""
from __future__ import annotations

from datetime import date, timedelta

from .. import config
from ..db import get_session
from ..models import Item, PlanValue, Snapshot
from ..engine.stock_projection import suggest_order
from ..registry.build import resolve
from ..forecast_views import (_apply_adjustments, _latest_adjustments,
                              _latest_stock_state, effective_lead_time,
                              trusted_current_balance)
from ..ingestion import legacy_po


def _future_demand(session, item_id: int, month_dates: list[date]) -> list[float]:
    """demand for each calendar month in month_dates, latest-snapshot-wins
    per (item, month, level) — same FIX_PLAN C4 rule as
    forecasting._level_series — with master preferred per month and
    one_row filling in months master is silent on, same per-month merge
    rule as forecasting._series_by_item. This used to read master only,
    which silently dropped items (e.g. ｿﾌﾄｳｪｱS licenses) whose plan only
    ever appears at the one_row level, producing no suggestion at all
    where the production path (forecast_views.order_suggestions, via the
    full make_forecast pipeline) found real demand; kept in sync with
    forecast_views/forecasting manually since this is a separate read
    path (see CLAUDE.md constraint #4)."""
    rows = (session.query(PlanValue.month, PlanValue.value, PlanValue.level,
                          PlanValue.snapshot_id, Snapshot.file_mtime)
           .join(Snapshot, Snapshot.id == PlanValue.snapshot_id)
           .filter(PlanValue.item_id == item_id, PlanValue.level.in_(("master", "one_row")),
                   PlanValue.month.in_(month_dates), PlanValue.value.isnot(None))
           # nulls_first() mirrors forecast_views._latest_plan_by_month —
           # these are deliberately separate code paths (CLAUDE.md
           # constraint #4) and must stay in sync. SQLite sorts NULLs first
           # ascending, Postgres last; pin the SQLite order explicitly.
           .order_by(PlanValue.month,
                    Snapshot.file_mtime.asc().nulls_first(),
                    PlanValue.snapshot_id.asc())
           .all())
    by_level: dict[str, dict[date, float]] = {"master": {}, "one_row": {}}
    for month, value, level, _snapshot_id, _file_mtime in rows:
        if isinstance(month, str):
            month = date.fromisoformat(month)
        by_level[level][month] = float(value)   # later (latest) overwrites
    merged = dict(by_level["one_row"])
    merged.update(by_level["master"])            # master wins per-month
    return [merged.get(m, 0.0) for m in month_dates]


def generate_for_item(session, item: Item, adjustments=None) -> dict | None:
    stock = _latest_stock_state(session, item.id)
    current_balance, _confidence = trusted_current_balance(stock)
    if current_balance is None:
        return None
    today = date.today().replace(day=1)
    months = 4
    month_dates = [date(today.year + (today.month - 1 + i) // 12,
                        (today.month - 1 + i) % 12 + 1, 1) for i in range(months)]
    demand = _future_demand(session, item.id, month_dates)
    if adjustments is None:
        adjustments = _latest_adjustments(session)
    demand, _ = _apply_adjustments(item.id, month_dates, demand, adjustments)
    if sum(demand) == 0:
        return None
    result = suggest_order(current_balance=current_balance, monthly_demand=demand,
                           moq=item.moq, lead_time_days=effective_lead_time(item),
                           min_stock_days=item.min_stock_days,
                           max_stock_days=item.max_stock_days,
                           moq_excessive_months=config.MOQ_EXCESSIVE_MONTHS)
    result.update({"item_id": item.id, "item": item.canonical_name,
                  "category": item.category, "current_balance": current_balance})
    return result


def run_plausibility_check(days_back: int = 90, limit: int = 15) -> dict:
    print("=== M5: order suggestions + plausibility check vs real PO history ===")
    session = get_session()
    pos = legacy_po.read_all_pos()
    cutoff = date.today() - timedelta(days=days_back)
    recent_pos: dict[str, list] = {}
    for p in pos:
        if p["order_date"] and p["order_date"] >= cutoff and p["qty"]:
            recent_pos.setdefault(p["sku"], []).append(p)
    print(f"[data] {len(pos)} total PO rows read from 17 files' 購買計画; "
         f"{len(recent_pos)} distinct SKUs ordered in the last {days_back} days")

    adjustments = _latest_adjustments(session)
    comparisons = []
    for sku, plist in recent_pos.items():
        item = resolve(session, sku, prefer_source="ONE綽名")
        if item is None:
            continue
        sugg = generate_for_item(session, item, adjustments)
        if sugg is None or sugg["action"] != "suggest_order":
            continue
        actual_qty = sum(p["qty"] for p in plist)
        our_qty = sugg["suggested_qty"]
        if our_qty == 0 and actual_qty == 0:
            continue
        ratio = our_qty / actual_qty if actual_qty else None
        comparisons.append({"sku": sku, "category": item.category,
                            "actual_recent_qty": round(actual_qty, 1),
                            "suggested_qty": our_qty, "ratio": round(ratio, 2) if ratio else None})

    print(f"\n[plausibility] {len(comparisons)} items had both a recent real PO "
         "and a live suggestion to compare")
    print(f"{'SKU':<20}{'category':<14}{'actual qty':>12}{'suggested qty':>15}{'ratio':>8}")
    for c in sorted(comparisons, key=lambda x: -(x["ratio"] or 0))[:limit]:
        print(f"{c['sku']:<20}{c['category'] or '':<14}{c['actual_recent_qty']:>12}"
              f"{c['suggested_qty']:>15}{(c['ratio'] if c['ratio'] else '—'):>8}")

    close = sum(1 for c in comparisons if c["ratio"] and 0.5 <= c["ratio"] <= 2.0)
    print(f"\n[plausibility] {close}/{len(comparisons)} suggestions within 2x of the "
         "actual recently-ordered quantity (order-of-magnitude agreement)")
    print("[honesty note] this is a live-data plausibility check, not a true "
         "point-in-time historical backtest — the system has no prior monthly "
         "snapshots of its own yet to replay. A real backtest becomes possible "
         "once this system has been running for several monthly cycles.")

    # sample suggestions across categories, for demo purposes
    sample_items = session.query(Item).filter(Item.moq.isnot(None)).limit(300).all()
    all_suggestions = [s for i in sample_items
                      if (s := generate_for_item(session, i, adjustments))]
    n_suggest = sum(1 for s in all_suggestions if s["action"] == "suggest_order")
    print(f"\n[coverage] {n_suggest}/{len(all_suggestions)} sampled items with stock+"
         "demand data currently need a suggested order")
    session.close()
    return {"total_pos": len(pos), "comparisons": comparisons, "close_matches": close}


if __name__ == "__main__":
    run_plausibility_check()
