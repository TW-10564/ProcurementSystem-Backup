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

from sqlalchemy import func

from .models import AccuracyScore, Adjustment, Forecast, ForecastRun, Item, PlanValue, StockState
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


def _latest_adjustments(session) -> dict[tuple[int, date], Adjustment]:
    """Most recent sku-scoped adjustment per (item_id, month), across ALL
    runs ever recorded — NOT filtered to the current run_id, so a decision
    from an earlier week's meeting keeps applying after regeneration. If a
    later meeting records a new adjustment for the same (item, month), the
    later one (by created_at) wins — this week's decision supersedes last
    week's for the same target, same as a person overwriting a sticky note."""
    rows = (session.query(Adjustment)
            .filter(Adjustment.item_id.isnot(None), Adjustment.scope == "sku")
            .order_by(Adjustment.created_at.asc()).all())
    latest: dict[tuple[int, date], Adjustment] = {}
    for a in rows:
        latest[(a.item_id, _as_date(a.month))] = a   # later created_at overwrites earlier
    return latest


def _apply_adjustments(item_id: int, months: list[date], baseline_qtys: list[float],
                       adjustments: dict[tuple[int, date], Adjustment]
                       ) -> tuple[list[float], dict[int, dict]]:
    """Overlay any matching adjustments onto a baseline series. Returns the
    final (possibly-overridden) quantities AND a {month_index: detail} map
    so callers can show "adjusted from N — reason, author" rather than
    silently swapping the number."""
    final = list(baseline_qtys)
    detail: dict[int, dict] = {}
    for i, m in enumerate(months):
        adj = adjustments.get((item_id, m))
        if adj is not None:
            detail[i] = {"baseline": baseline_qtys[i], "adjusted": adj.new_value,
                        "reason": adj.reason, "author": adj.author,
                        "created_at": adj.created_at.isoformat() if adj.created_at else None}
            final[i] = adj.new_value
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
    for item_id, rows in by_item.items():
        item = items.get(item_id)
        if item is None:
            continue
        cat = item.category or "（未分類）"
        c = cats.setdefault(cat, {"category": cat, "items": 0,
                                  "next3_total": 0.0, "next12_total": 0.0})
        c["items"] += 1
        months = [_as_date(f.month) for f in rows]
        qtys, _ = _apply_adjustments(item_id, months, [f.baseline or 0.0 for f in rows],
                                     adjustments)
        c["next3_total"] += sum(qtys[:3])
        c["next12_total"] += sum(qtys[:12])
        sugg = _suggestion(item, stocks.get(item_id), qtys)
        if sugg and sugg["action"] == "suggest_order":
            orders_needed += 1

    for c in cats.values():
        c["next3_total"] = round(c["next3_total"])
        c["next12_total"] = round(c["next12_total"])
    return {"run": _run_info(run), "orders_needed": orders_needed,
            "categories": sorted(cats.values(), key=lambda c: -c["next12_total"])}


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
    wapes = {a.item_id: a.wape for a in session.query(AccuracyScore)
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
        rows.append({
            "item_id": item.id, "name": item.canonical_name,
            "category": item.category, "tier": item.tier,
            "method": frows[0].method,
            "wape": round(wapes[item.id], 1) if item.id in wapes else None,
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
    plan_rows = (session.query(PlanValue.month, func.avg(PlanValue.value))
                 .filter(PlanValue.item_id == item_id, PlanValue.level == "master",
                         PlanValue.value.isnot(None))
                 .group_by(PlanValue.month).order_by(PlanValue.month).all())
    history = [{"month": _as_date(m).isoformat(), "qty": round(v, 1)}
               for m, v in plan_rows if _as_date(m) < start][-24:]
    one_plan = [{"month": _as_date(m).isoformat(), "qty": round(v, 1)}
                for m, v in plan_rows if _as_date(m) >= start][:len(forecast) or 12]

    stock = (session.query(StockState).filter(StockState.item_id == item_id)
             .order_by(StockState.snapshot_date.desc()).first())
    sugg = _suggestion(item, stock, [f["qty"] or 0.0 for f in forecast])
    wape = None
    if run:
        acc = (session.query(AccuracyScore)
               .filter(AccuracyScore.run_id == run.id,
                       AccuracyScore.item_id == item_id).first())
        wape = round(acc.wape, 1) if acc and acc.wape is not None else None

    near_term = [(_as_date(f["month"]), f["qty"] or 0.0)
                for f in forecast[:WEEKLY_HORIZON_MONTHS]]
    weekly = disaggregate_forecast(near_term) if near_term else []

    return {"item": {"id": item.id, "name": item.canonical_name,
                     "category": item.category, "tier": item.tier,
                     "lifecycle": item.lifecycle, "moq": item.moq,
                     "lead_time_days": item.lead_time_days,
                     "min_stock_days": item.min_stock_days},
            "method": method, "wape": wape,
            "history": history, "forecast": forecast, "one_plan": one_plan,
            "weekly": weekly,
            "stock": ({"one_balance": stock.one_balance, "sap_qty": stock.sap_qty,
                       "snapshot_date": _as_date(stock.snapshot_date).isoformat()
                       if stock.snapshot_date else None} if stock else None),
            "suggestion": sugg}
