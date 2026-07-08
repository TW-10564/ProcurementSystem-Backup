"""Read-side assembly of forecast data for the API — no writes here.

Everything is served from the latest forecast run in the app's own DB, plus
on-the-fly order suggestions (pure math over stored stock + forecast; the
suggestion engine never places an order — REQ-017 / BR-013).

Adjustments (recorded via advisory/adjustments.py — a human override tied to
a real reason, e.g. a procurement meeting decision) are overlaid HERE, at
serve time, on every read path: the SKU list, the category summary, the
order suggestions, and the detail chart. This is deliberate, not an
oversight: `Forecast.baseline` in the DB must stay the untouched output of
the statistical method, because FVA (verify/fva.py) exists specifically to
measure whether a human adjustment made things MORE or LESS accurate than
the baseline once the real actual lands — that comparison is impossible if
adjustments get baked into the stored baseline. A weekly regeneration
(forecasting.generate()) creates a new ForecastRun with fresh baselines,
but adjustments are looked up by (item_id, month) independent of run_id, so
a decision recorded in one week's meeting keeps applying automatically to
every future run until a newer adjustment for that same (item, month)
supersedes it — recalculating never silently erases it.
"""
from __future__ import annotations

import json
from datetime import date

from .models import (AccuracyScore, Adjustment, Forecast, ForecastRun, Item,
                    PlanValue, Snapshot, StockState)
from .engine.stock_projection import suggest_order
from .engine.weekly import disaggregate_forecast

WEEKLY_HORIZON_MONTHS = 3   # near-term months shown at weekly resolution —
                            # matches the M+1/M+2 horizon buyers actually act on


def _as_date(v) -> date:
    return date.fromisoformat(v) if isinstance(v, str) else v


def _latest_run(session) -> ForecastRun | None:
    return session.query(ForecastRun).order_by(ForecastRun.id.desc()).first()


def _run_info(run: ForecastRun) -> dict:
    info = {"run_id": run.id, "method_version": run.method_version,
            "created_at": run.created_at.isoformat() if run.created_at else None}
    if run.note:
        info.update(json.loads(run.note))
    return info


def _stock_by_item(session) -> dict[int, StockState]:
    """Latest stock state per item (one query, newest snapshot wins)."""
    rows = (session.query(StockState)
            .order_by(StockState.item_id, StockState.snapshot_date).all())
    return {r.item_id: r for r in rows}          # later (newer) overwrites


def _forecast_rows(session, run_id: int) -> dict[int, list]:
    rows = (session.query(Forecast).filter(Forecast.run_id == run_id)
            .order_by(Forecast.item_id, Forecast.month).all())
    out: dict[int, list] = {}
    for f in rows:
        out.setdefault(f.item_id, []).append(f)
    return out


def _adjustment_factor(adj: Adjustment) -> float | None:
    """Multiplicative factor a global/category adjustment represents, used to
    scale every OTHER item in scope (the item it was actually recorded
    against still gets the literal new_value — see _apply_adjustments).
    Prefers the explicit `factor` column; falls back to deriving one from
    old_value/new_value (what the Adjustments form actually collects today).
    Returns None when neither is available — not enough information to
    safely expand beyond the single recorded item."""
    if adj.factor is not None:
        return adj.factor
    if adj.old_value not in (None, 0):
        return adj.new_value / adj.old_value
    return None


def _latest_adjustments(session) -> dict[tuple[int, date], Adjustment]:
    """Most recent adjustment per (item_id, month), across ALL runs ever
    recorded — NOT filtered to the current run_id, so a decision from an
    earlier week's meeting keeps applying after regeneration. If a later
    meeting records a new adjustment for the same (item, month), the later
    one (by created_at) wins — this week's decision supersedes last week's
    for the same target, same as a person overwriting a sticky note.

    scope="sku" affects only its own item_id, as before. scope="global"/
    "category" (FIX_PLAN C2 — previously accepted and stored but NEVER
    read by any downstream path) are expanded here to every item they
    should affect: every item for "global", every item sharing the
    recorded item's category for "category". _apply_adjustments applies
    the expanded ones as a scaling factor rather than pasting one item's
    absolute value onto every other item."""
    rows = (session.query(Adjustment)
            .filter(Adjustment.item_id.isnot(None))
            .order_by(Adjustment.created_at.asc()).all())
    if not rows:
        return {}
    items = {i.id: i for i in session.query(Item).all()}
    latest: dict[tuple[int, date], Adjustment] = {}
    for a in rows:
        month = _as_date(a.month)
        if a.scope == "global" and _adjustment_factor(a) is not None:
            targets = list(items.keys())
        elif a.scope == "category" and _adjustment_factor(a) is not None:
            anchor = items.get(a.item_id)
            cat = anchor.category if anchor else None
            targets = [iid for iid, it in items.items() if it.category == cat]
        else:
            # scope="sku", or global/category without enough info (no
            # factor and no old_value) to safely expand — still apply to
            # the recorded item rather than silently dropping it.
            targets = [a.item_id]
        for item_id in targets:
            latest[(item_id, month)] = a   # later created_at overwrites earlier
    return latest


def _apply_adjustments(item_id: int, months: list[date], baseline_qtys: list[float],
                       adjustments: dict[tuple[int, date], Adjustment]
                       ) -> tuple[list[float], dict[int, dict]]:
    """Overlay any matching adjustments onto a baseline series. Returns the
    final (possibly-overridden) quantities AND a {month_index: detail} map
    so callers can show "adjusted from N — reason, author" rather than
    silently swapping the number.

    The item the adjustment was literally recorded against (scope="sku", or
    the anchor item of a "global"/"category" adjustment) gets the exact
    new_value typed in. Every OTHER item swept in by a "global"/"category"
    expansion gets its own baseline scaled by the same factor instead —
    pasting one item's absolute value onto a different item's forecast
    would be meaningless."""
    final = list(baseline_qtys)
    detail: dict[int, dict] = {}
    for i, m in enumerate(months):
        adj = adjustments.get((item_id, m))
        if adj is not None:
            if getattr(adj, "scope", "sku") == "sku" or adj.item_id == item_id:
                new_val = adj.new_value
            else:
                factor = _adjustment_factor(adj)
                new_val = baseline_qtys[i] * factor if factor is not None else adj.new_value
            detail[i] = {"baseline": baseline_qtys[i], "adjusted": new_val,
                        "reason": adj.reason, "author": adj.author,
                        "created_at": adj.created_at.isoformat() if adj.created_at else None}
            final[i] = new_val
    return final, detail


def _suggestion(item: Item, stock: StockState | None, demand: list[float]) -> dict | None:
    if stock is None or (stock.one_balance is None and stock.sap_qty is None):
        return None
    balance = stock.sap_qty if stock.sap_qty is not None else stock.one_balance
    if sum(demand) == 0:
        return None
    s = suggest_order(current_balance=balance, monthly_demand=demand,
                      moq=item.moq, lead_time_days=item.lead_time_days,
                      min_stock_days=item.min_stock_days)
    s["current_balance"] = balance
    return s


def _is_unactionable_estimate(method: str | None, stock: StockState | None) -> bool:
    """True for the category-average fallback (engine/tier2.category_typical_rate
    — a rate borrowed from category PEERS, not the item's own consumption,
    for items with ZERO history of their own) on an item that ALSO has no
    stock record in either SAP or ONE.

    Found 2026-07-08: every one of these items structurally can never
    produce an order suggestion (_suggestion requires a stock balance to
    project from) — the forecast number exists but nothing procurement can
    act on ever attaches to it. Rolling it into the headline next3/next12
    totals overstates what's actually plannable; it's excluded there but
    still fully visible per-item in list_forecasts/item_detail, and counted
    separately here so it isn't silently dropped."""
    if method != "tier2_category_average":
        return False
    return stock is None or (stock.one_balance is None and stock.sap_qty is None)


def summary(session) -> dict:
    run = _latest_run(session)
    if run is None:
        return {"run": None, "categories": []}
    by_item = _forecast_rows(session, run.id)
    items = {i.id: i for i in session.query(Item).all()}
    stocks = _stock_by_item(session)
    adjustments = _latest_adjustments(session)

    cats: dict[str, dict] = {}
    orders_needed = 0
    excluded_items = 0
    excluded_next3 = 0.0
    excluded_next12 = 0.0
    for item_id, rows in by_item.items():
        item = items.get(item_id)
        if item is None:
            continue
        cat = item.category or "（未分類）"
        c = cats.setdefault(cat, {"category": cat, "items": 0,
                                  "next3_total": 0.0, "next12_total": 0.0,
                                  "no_stock_tracking_items": 0})
        c["items"] += 1
        months = [_as_date(f.month) for f in rows]
        qtys, _ = _apply_adjustments(item_id, months, [f.baseline or 0.0 for f in rows],
                                     adjustments)
        stock = stocks.get(item_id)

        if _is_unactionable_estimate(rows[0].method, stock):
            c["no_stock_tracking_items"] += 1
            excluded_items += 1
            excluded_next3 += sum(qtys[:3])
            excluded_next12 += sum(qtys[:12])
        else:
            c["next3_total"] += sum(qtys[:3])
            c["next12_total"] += sum(qtys[:12])

        sugg = _suggestion(item, stock, qtys)
        if sugg and sugg["action"] == "suggest_order":
            orders_needed += 1

    for c in cats.values():
        c["next3_total"] = round(c["next3_total"])
        c["next12_total"] = round(c["next12_total"])
    return {"run": _run_info(run), "orders_needed": orders_needed,
            "categories": sorted(cats.values(), key=lambda c: -c["next12_total"]),
            "excluded_no_stock_tracking": {"items": excluded_items,
                                           "next3_total": round(excluded_next3),
                                           "next12_total": round(excluded_next12)}}


def order_suggestions(session) -> dict:
    """The full detail behind summary()'s "orders needed" count — every
    item that actually needs a purchase order, with the numbers a buyer
    needs to assess real need at a glance: current stock, suggested
    quantity, and how soon. Built so the KPI on the dashboard is never a
    number without a way to see what it's counting."""
    run = _latest_run(session)
    if run is None:
        return {"run": None, "total": 0, "rows": [], "by_category": [],
               "by_month": [], "negative_stock_count": 0, "total_qty": 0}
    by_item = _forecast_rows(session, run.id)
    items = {i.id: i for i in session.query(Item).all()}
    stocks = _stock_by_item(session)
    adjustments = _latest_adjustments(session)

    rows = []
    for item_id, frows in by_item.items():
        item = items.get(item_id)
        if item is None:
            continue
        month_dates = [_as_date(f.month) for f in frows]
        months = [m.isoformat() for m in month_dates]
        qtys, _ = _apply_adjustments(item_id, month_dates,
                                     [f.baseline or 0.0 for f in frows], adjustments)
        stock = stocks.get(item_id)
        sugg = _suggestion(item, stock, qtys)
        if not sugg or sugg["action"] != "suggest_order":
            continue
        balance = stock.sap_qty if stock and stock.sap_qty is not None else (
            stock.one_balance if stock else None)
        rows.append({
            "item_id": item_id, "name": item.canonical_name, "category": item.category,
            "method": frows[0].method,
            "stock": round(balance, 1) if balance is not None else None,
            "safety_stock": sugg["safety_stock"],
            "order_qty": sugg["suggested_qty"],
            "order_by": months[sugg["order_by_month_index"]],
            "breach_month": months[sugg["breach_month_index"]],
        })
    rows.sort(key=lambda r: (r["order_by"], -r["order_qty"]))

    by_category: dict[str, dict] = {}
    by_month: dict[str, int] = {}
    negative_stock_count = 0
    total_qty = 0.0
    for r in rows:
        cat = r["category"] or "（未分類）"
        c = by_category.setdefault(cat, {"category": cat, "items": 0, "qty": 0.0})
        c["items"] += 1
        c["qty"] += r["order_qty"]
        by_month[r["order_by"]] = by_month.get(r["order_by"], 0) + 1
        total_qty += r["order_qty"]
        if r["stock"] is not None and r["stock"] < 0:
            negative_stock_count += 1

    return {"run": _run_info(run), "total": len(rows), "rows": rows,
           "total_qty": round(total_qty),
           "by_category": sorted(by_category.values(), key=lambda c: -c["qty"]),
           "by_month": [{"month": m, "items": n} for m, n in sorted(by_month.items())],
           "negative_stock_count": negative_stock_count}


def list_forecasts(session, q: str = "", category: str | None = None,
                   method: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    """method: exact method string, or the shorthand "tier2" to match
    either derived method (tier2_attach_rate / tier2_category_average) —
    lets the UI isolate "how many parts came from the accessory-derivation
    path" instead of them being scattered through a volume-sorted list."""
    run = _latest_run(session)
    if run is None:
        return {"run": None, "total": 0, "months": [], "rows": []}
    by_item = _forecast_rows(session, run.id)
    stocks = _stock_by_item(session)
    adjustments = _latest_adjustments(session)
    # FIX_PLAN M1: wape_m1 (near-term, M+1-only) is the primary confidence
    # signal shown/colored in the UI — it's the number a buyer actually acts
    # on given lead times. The 6-month blended WAPE is kept as a secondary
    # "full horizon" caveat (wape_full_horizon) rather than driving the
    # confidence dot, since it's dominated by noisier far-out months.
    acc_scores = {a.item_id: a for a in session.query(AccuracyScore)
                 .filter(AccuracyScore.run_id == run.id).all()}

    item_q = session.query(Item).filter(Item.id.in_(by_item.keys()))
    if q:
        item_q = item_q.filter(Item.canonical_name.ilike(f"%{q}%"))
    if category:
        item_q = item_q.filter(Item.category == category)
    items = item_q.all()

    months: list[str] = []
    rows = []
    for item in items:
        frows = by_item[item.id]
        item_method = frows[0].method
        if method == "tier2":
            if not (item_method or "").startswith("tier2_"):
                continue
        elif method and item_method != method:
            continue
        if not months:
            months = [_as_date(f.month).isoformat() for f in frows]
        month_dates = [_as_date(f.month) for f in frows]
        qtys, adj_detail = _apply_adjustments(item.id, month_dates,
                                              [f.baseline or 0.0 for f in frows],
                                              adjustments)
        stock = stocks.get(item.id)
        sugg = _suggestion(item, stock, qtys)
        acc = acc_scores.get(item.id)
        wape_m1 = acc.wape_m1 if acc and acc.wape_m1 is not None else None
        wape_full = acc.wape if acc and acc.wape is not None else None
        rows.append({
            "item_id": item.id, "name": item.canonical_name,
            "category": item.category, "tier": item.tier,
            "method": frows[0].method,
            "wape": round(wape_m1 if wape_m1 is not None else wape_full, 1)
                    if (wape_m1 is not None or wape_full is not None) else None,
            "wape_full_horizon": round(wape_full, 1) if wape_full is not None else None,
            "stock": (stock.sap_qty if stock and stock.sap_qty is not None
                      else stock.one_balance if stock else None),
            "qty": qtys, "next12_total": round(sum(qtys[:12])),
            "has_adjustment": bool(adj_detail),
            "order": ({"qty": sugg["suggested_qty"],
                       "order_by": months[sugg["order_by_month_index"]]}
                      if sugg and sugg["action"] == "suggest_order" else None),
        })
    rows.sort(key=lambda r: -r["next12_total"])
    return {"run": _run_info(run), "total": len(rows), "months": months,
            "rows": rows[offset:offset + limit]}


def item_detail(session, item_id: int) -> dict | None:
    item = session.get(Item, item_id)
    if item is None:
        return None
    run = _latest_run(session)
    forecast = []
    method = None
    if run:
        frows = (session.query(Forecast)
                 .filter(Forecast.run_id == run.id, Forecast.item_id == item_id)
                 .order_by(Forecast.month).all())
        month_dates = [_as_date(f.month) for f in frows]
        adjustments = _latest_adjustments(session)
        qtys, adj_detail = _apply_adjustments(item_id, month_dates,
                                              [f.baseline or 0.0 for f in frows], adjustments)
        forecast = [{"month": m.isoformat(), "qty": qtys[i],
                    "baseline_qty": frows[i].baseline or 0.0,
                    "adjustment": adj_detail.get(i)}
                   for i, m in enumerate(month_dates)]
        method = frows[0].method if frows else None

    start = _as_date(forecast[0]["month"]) if forecast else date.today().replace(day=1)
    # FIX_PLAN C4: same latest-snapshot-wins fix as forecasting._series_by_item
    # — a re-ingested (corrected) month must not be averaged with the stale
    # value it replaced.
    def _latest_by_month(level: str) -> dict[date, float]:
        rows = (session.query(PlanValue.month, PlanValue.value,
                              PlanValue.snapshot_id, Snapshot.file_mtime)
               .join(Snapshot, Snapshot.id == PlanValue.snapshot_id)
               .filter(PlanValue.item_id == item_id, PlanValue.level == level,
                       PlanValue.value.isnot(None))
               .order_by(PlanValue.month, Snapshot.file_mtime.asc(),
                        PlanValue.snapshot_id.asc())
               .all())
        out: dict[date, float] = {}
        for m, v, _sid, _mtime in rows:
            out[_as_date(m)] = v                  # later (latest) overwrites
        return out

    # Per-MONTH merge, master preferred (see forecasting._series_by_item for
    # why this can't be a whole-item "or": master coverage commonly STOPS
    # partway through an item's timeline rather than being all-or-nothing).
    master_by_month = _latest_by_month("master")
    one_row_by_month = _latest_by_month("one_row")
    latest_by_month = {**one_row_by_month, **master_by_month}
    plan_rows = sorted(latest_by_month.items())
    history = [{"month": m.isoformat(), "qty": round(v, 1)}
               for m, v in plan_rows if m < start][-24:]
    one_plan = [{"month": m.isoformat(), "qty": round(v, 1)}
                for m, v in plan_rows if m >= start][:len(forecast) or 12]

    stock = (session.query(StockState).filter(StockState.item_id == item_id)
             .order_by(StockState.snapshot_date.desc()).first())
    sugg = _suggestion(item, stock, [f["qty"] or 0.0 for f in forecast])
    wape = None
    wape_full_horizon = None
    if run:
        acc = (session.query(AccuracyScore)
               .filter(AccuracyScore.run_id == run.id,
                       AccuracyScore.item_id == item_id).first())
        # FIX_PLAN M1: near-term (M+1) WAPE is the primary confidence signal;
        # 6-month blended WAPE follows as a secondary "full horizon" caveat.
        if acc:
            wape_full_horizon = round(acc.wape, 1) if acc.wape is not None else None
            wape = (round(acc.wape_m1, 1) if acc.wape_m1 is not None
                    else wape_full_horizon)

    near_term = [(_as_date(f["month"]), f["qty"] or 0.0)
                for f in forecast[:WEEKLY_HORIZON_MONTHS]]
    weekly = disaggregate_forecast(near_term) if near_term else []

    return {"item": {"id": item.id, "name": item.canonical_name,
                     "category": item.category, "tier": item.tier,
                     "lifecycle": item.lifecycle, "moq": item.moq,
                     "lead_time_days": item.lead_time_days,
                     "min_stock_days": item.min_stock_days},
            "method": method, "wape": wape, "wape_full_horizon": wape_full_horizon,
            "history": history, "forecast": forecast, "one_plan": one_plan,
            "weekly": weekly,
            "stock": ({"one_balance": stock.one_balance, "sap_qty": stock.sap_qty,
                       "snapshot_date": _as_date(stock.snapshot_date).isoformat()
                       if stock.snapshot_date else None} if stock else None),
            "suggestion": sugg}
