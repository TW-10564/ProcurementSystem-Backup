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

import bisect
import json
from datetime import date, timedelta

from . import config
from .timeutil import as_utc_iso
from .models import (AccuracyScore, Actual, Adjustment, Forecast, ForecastRun,
                    Item, PlanValue, Snapshot, StockState, WeeklyActual)
from .engine.stock_projection import prorate_current_month, suggest_order
from .engine.weekly import disaggregate_forecast
from .advisory import order_actions as order_svc

WEEKLY_HORIZON_MONTHS = 3   # near-term months shown at weekly resolution —
                            # matches the M+1/M+2 horizon buyers actually act on


def _as_date(v) -> date:
    return date.fromisoformat(v) if isinstance(v, str) else v


def _latest_run(session) -> ForecastRun | None:
    return session.query(ForecastRun).order_by(ForecastRun.id.desc()).first()


def _run_info(run: ForecastRun) -> dict:
    info = {"run_id": run.id, "method_version": run.method_version,
            "created_at": as_utc_iso(run.created_at)}
    if run.note:
        info.update(json.loads(run.note))
    return info


def _prefer_sap_row(prev: StockState | None, r: StockState) -> bool:
    """True if `r` should replace `prev` as an item's "current" stock row.

    Found 2026-07-14: ingestion writes TWO separate StockState rows per item
    — one from ONE's own subfile 在庫残高, a forward-PROJECTED balance
    always dated to "today" (the ingestion run date), and one from SAP's
    physical count, dated to the SAP extract's own real refresh date (which
    can be several days older, since it only refreshes weekly). Naively
    picking "most recent snapshot_date" therefore preferred ONE's simulated
    projection — which can already be negative for a fast-moving item well
    before any REAL shortage — over the actual physical SAP count, on every
    day except the rare one SAP happens to refresh same-day as the build.
    Once a row with a real sap_qty has been seen for an item, a later
    ONE-only row (sap_qty is None) must never displace it; a later row that
    ALSO has sap_qty legitimately wins (it's a newer real count)."""
    if prev is not None and prev.sap_qty is not None and r.sap_qty is None:
        return False
    return True


def trusted_current_balance(stock: StockState | None) -> tuple[float | None, str]:
    """The current stock balance to actually use for order-suggestion math
    (and any other "how much do we have right now" question), plus a
    confidence flag: "sap_verified" / "one_only_unverified" /
    "one_only_negative_clamped" / "no_data".

    Rule (confirmed by the user 2026-07-15): SAP's physical count is the
    authoritative source of truth for real inventory; ONE is a downstream
    system that gets updated FROM SAP, not the other way around. SAP always
    wins when present — matches the existing tie-break in _prefer_sap_row.

    When an item has no SAP coverage at all (confirmed live 2026-07-15: 84
    of 583 items with any stock data, several of them Tier-1 CPU/MB SKUs),
    one_balance is the only signal available. But one_balance is ONE's own
    simulated running projection, not a physical count — CLAUDE.md's own
    constraint #3 documents it can go NEGATIVE well before any real
    shortage. A negative balance is physically impossible for real
    inventory and must not be trusted literally: engine.stock_projection's
    suggest_order() computes shortfall = target_level - projected[breach],
    and a negative starting balance flows straight through project_stock()
    into that shortfall — silently inflating the suggested order quantity
    by however negative the fake balance was. Clamped to 0 here instead."""
    if stock is None:
        return None, "no_data"
    if stock.sap_qty is not None:
        return stock.sap_qty, "sap_verified"
    if stock.one_balance is not None:
        if stock.one_balance < 0:
            return 0.0, "one_only_negative_clamped"
        return stock.one_balance, "one_only_unverified"
    return None, "no_data"


def _stock_by_item(session) -> dict[int, StockState]:
    """Latest stock state per item — see _prefer_sap_row for why this isn't
    simply "newest snapshot_date wins"."""
    # nulls_first() is explicit on purpose. SQLite sorts NULLs FIRST on an
    # ascending sort; PostgreSQL sorts them LAST. Since this loop keeps the
    # LAST acceptable row per item, a NULL-dated row would go from
    # "overridden by any real row" to "beats every real row" purely by
    # changing database — and if that row carries a sap_qty, _prefer_sap_row
    # accepts it and a bogus balance flows into trusted_current_balance and
    # on into order quantities. Pinning the SQLite semantics keeps behaviour
    # identical across both engines.
    rows = (session.query(StockState)
            .order_by(StockState.item_id,
                      StockState.snapshot_date.asc().nulls_first()).all())
    by_item: dict[int, StockState] = {}
    for r in rows:
        if _prefer_sap_row(by_item.get(r.item_id), r):
            by_item[r.item_id] = r
    return by_item


def _latest_stock_state(session, item_id: int) -> StockState | None:
    """Single-item equivalent of _stock_by_item, for call sites that only
    need one item's current stock (e.g. item_detail, verify/order_suggestions)."""
    rows = (session.query(StockState).filter(StockState.item_id == item_id)
           .order_by(StockState.snapshot_date.asc().nulls_first()).all())
    latest: StockState | None = None
    for r in rows:
        if _prefer_sap_row(latest, r):
            latest = r
    return latest


def sap_product_name(session, item: Item) -> str | None:
    """The full SAP product description (SYONAME) for an item, or None.

    Every view identifies parts by their 綽名 (あだ名) — the short nickname
    that is the join key across all ONE files. That nickname is often opaque
    on its own: "RX7600" doesn't say which board it is, "M02" and "緩衝材"
    say nothing at all. SAP's SYONAME carries the real description
    ("組ASR RX7600 CL 8GO(G6 1H3P OC)"), and ingestion already stores it on
    StockState.raw_name — it was simply never surfaced. 530 of ~2.9k items
    have a description richer than their nickname.

    Not read off _latest_stock_state's chosen row: ingestion writes several
    StockState rows per item (see _prefer_sap_row) and only the SAP-sourced
    one carries SYONAME — the ONE-sourced row just repeats the nickname. So
    scan every row for the most descriptive name instead of trusting one.
    """
    rows = (session.query(StockState.raw_name)
            .filter(StockState.item_id == item.id,
                    StockState.raw_name.isnot(None)).all())
    canonical = (item.canonical_name or "").strip()
    # Longest name that actually says more than the nickname already does.
    best = None
    for (name,) in rows:
        name = (name or "").strip()
        if not name or name == canonical:
            continue
        if len(name) <= len(canonical):
            continue          # a bare code (e.g. "14700F") — no more use than the nickname
        if best is None or len(name) > len(best):
            best = name
    return best


def _last_active_month_by_item(session, item_ids) -> dict[int, date]:
    """Most recent PAST month each item genuinely sold, for the Forecast
    page's "latest selling on top" default ordering. Prefers real `Actual`
    demand (qty > 0); Tier-2 accessory parts often have no `Actual` rows at
    all (they're derived, not independently sold/tracked), so those fall
    back to their most recent nonzero `PlanValue` month. Both sides are
    capped at the current month: `PlanValue` is a forward-looking plan, so
    an uncapped max() would surface a FUTURE planned month as if the part
    had just sold — the opposite of what "latest selling" means. Items with
    no signal at or before today have no recency and sort last (see
    list_forecasts)."""
    from sqlalchemy import func

    ids = list(item_ids)
    if not ids:
        return {}
    today = date.today()
    by_item: dict[int, date] = {
        item_id: _as_date(m) for item_id, m in
        session.query(Actual.item_id, func.max(Actual.month))
        .filter(Actual.item_id.in_(ids), Actual.qty > 0, Actual.month <= today)
        .group_by(Actual.item_id).all()
    }
    missing = [i for i in ids if i not in by_item]
    if missing:
        for item_id, m in (session.query(PlanValue.item_id, func.max(PlanValue.month))
                           .filter(PlanValue.item_id.in_(missing), PlanValue.value > 0,
                                  PlanValue.month <= today)
                           .group_by(PlanValue.item_id).all()):
            by_item[item_id] = _as_date(m)
    return by_item


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
                        "created_at": as_utc_iso(adj.created_at)}
            final[i] = new_val
    return final, detail


def effective_lead_time(item: Item) -> float | None:
    """LT always comes from 部品マスター's static lead_time_days — never from
    per-category 購買計画 PO history (ingestion/lead_time.py's observed
    median). Changed back 2026-08-14 at purchasing's request: for packaging
    items the observed figure (measured from real POs in each individual
    ONE file) ran 3-4x the master value (e.g. 外装箱大Ver2: 43 observed vs
    14 master) and was inflating order-suggestion quantities well past what
    purchasing's own plan called for. observed_lead_time_days/_samples are
    still populated and shown in the UI for reference, just no longer feed
    this calculation."""
    return item.lead_time_days


def _order_action_out(oa, today: date) -> dict:
    return {
        "id": oa.id, "status": oa.status, "qty_ordered": oa.qty_ordered,
        "expected_arrival_date": (oa.expected_arrival_date.isoformat()
                                 if oa.expected_arrival_date else None),
        "note": oa.note, "ordered_by": oa.ordered_by,
        "ordered_at": as_utc_iso(oa.ordered_at),
        "awaiting_confirmation": bool(oa.expected_arrival_date and oa.expected_arrival_date <= today),
    }


def _suggestion(item: Item, stock: StockState | None, demand: list[float],
                month_dates: list[date] | None = None) -> dict | None:
    """None only means "no stock to project from" (balance unknown). Zero
    demand (e.g. a discontinuing item) still goes through suggest_order() —
    found 2026-08-07 via UAT: short-circuiting here also blanked out
    "projected stock" for zero-demand items even though it's trivially just
    the current balance carried forward (no demand to draw it down). Their
    zero demand still correctly produces action="none", not a suggested
    order — see suggest_order()'s breach_month=None path."""
    balance, confidence = trusted_current_balance(stock)
    if balance is None:
        return None
    if month_dates and stock is not None and stock.snapshot_date is not None:
        demand = prorate_current_month(demand, month_dates[0], stock.snapshot_date)
    s = suggest_order(current_balance=balance, monthly_demand=demand,
                      moq=item.moq, lead_time_days=effective_lead_time(item),
                      min_stock_days=item.min_stock_days,
                      moq_excessive_months=config.MOQ_EXCESSIVE_MONTHS)
    s["current_balance"] = balance
    s["stock_confidence"] = confidence
    return s


def _is_unactionable_estimate(method: str | None, stock: StockState | None) -> bool:
    """True for the category-average fallback (engine/tier2.category_typical_rate
    — a rate borrowed from category PEERS, not the item's own consumption,
    for items with ZERO history of their own) on an item that ALSO has no
    stock record in either SAP or ONE.

    Found 2026-07-08: every one of these items structurally can never
    produce an order suggestion (_suggestion requires a stock balance to
    project from) — the forecast number exists but nothing procurement can
    act on ever attaches to it. Rolling it into the headline next3/next6
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
    excluded_next6 = 0.0
    for item_id, rows in by_item.items():
        item = items.get(item_id)
        if item is None:
            continue
        cat = item.category or "（未分類）"
        c = cats.setdefault(cat, {"category": cat, "items": 0,
                                  "next3_total": 0.0, "next6_total": 0.0,
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
            excluded_next6 += sum(qtys[:6])
        else:
            c["next3_total"] += sum(qtys[:3])
            c["next6_total"] += sum(qtys[:6])

        sugg = _suggestion(item, stock, qtys, months)
        if sugg and sugg["action"] == "suggest_order":
            orders_needed += 1

    for c in cats.values():
        c["next3_total"] = round(c["next3_total"])
        c["next6_total"] = round(c["next6_total"])
    return {"run": _run_info(run), "orders_needed": orders_needed,
            "categories": sorted(cats.values(), key=lambda c: -c["next6_total"]),
            "excluded_no_stock_tracking": {"items": excluded_items,
                                           "next3_total": round(excluded_next3),
                                           "next6_total": round(excluded_next6)}}


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
    active_order_actions = order_svc.list_active(session)
    today = date.today()

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
        sugg = _suggestion(item, stock, qtys, month_dates)
        if not sugg or sugg["action"] != "suggest_order":
            continue
        oa = active_order_actions.get(item_id)
        order_action = _order_action_out(oa, today) if oa is not None else None
        rows.append({
            "item_id": item_id, "name": item.canonical_name, "category": item.category,
            "method": frows[0].method,
            "stock": round(sugg["current_balance"], 1),
            "stock_confidence": sugg["stock_confidence"],
            "safety_stock": sugg["safety_stock"],
            "order_qty": sugg["suggested_qty"],
            "order_by": months[sugg["order_by_month_index"]],
            "breach_month": months[sugg["breach_month_index"]],
            "moq_excessive": sugg.get("moq_excessive", False),
            "moq_months_of_cover": sugg.get("moq_months_of_cover"),
            "order_action": order_action,
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
                   method: str | None = None, tier: int | None = None,
                   limit: int = 50, offset: int = 0) -> dict:
    """method: exact method string, or the shorthand "tier2" to match
    either derived method (tier2_attach_rate / tier2_category_average) —
    lets the UI isolate "how many parts came from the accessory-derivation
    path" instead of them being scattered through a volume-sorted list.

    tier: filters by Item.tier (1 = one of TIER1_CATEGORIES i.e. CPU/VGA/
    MB/Case, 2 = everything else) — a DIFFERENT split from `method`'s
    "tier2" shorthand above. tier=2 is every item purchasing (not
    企画部) owns, most of which still get a real per-SKU forecast from
    their own history; method="tier2" is only the minority with no history
    at all, falling back to attach-rate/category-average derivation. Do not
    conflate the two — see project memory on this exact distinction."""
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
    if tier is not None:
        item_q = item_q.filter(Item.tier == tier)
    items = item_q.all()
    last_active = _last_active_month_by_item(session, (i.id for i in items))

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
        sugg = _suggestion(item, stock, qtys, month_dates)
        acc = acc_scores.get(item.id)
        wape_m1 = acc.wape_m1 if acc and acc.wape_m1 is not None else None
        wape_full = acc.wape if acc and acc.wape is not None else None
        last_active_month = last_active.get(item.id)
        rows.append({
            "item_id": item.id, "name": item.canonical_name,
            "category": item.category, "tier": item.tier,
            "method": frows[0].method,
            "last_active_month": last_active_month.isoformat() if last_active_month else None,
            "wape": round(wape_m1 if wape_m1 is not None else wape_full, 1)
                    if (wape_m1 is not None or wape_full is not None) else None,
            "wape_full_horizon": round(wape_full, 1) if wape_full is not None else None,
            "stock": (stock.sap_qty if stock and stock.sap_qty is not None
                      else stock.one_balance if stock else None),
            "qty": qtys, "next6_total": round(sum(qtys[:6])),
            "has_adjustment": bool(adj_detail),
            "order": ({"qty": sugg["suggested_qty"],
                       "order_by": months[sugg["order_by_month_index"]]}
                      if sugg and sugg["action"] == "suggest_order" else None),
        })
    # Default order: most-recently-selling parts first (see
    # _last_active_month_by_item); items with no recency signal at all sort
    # last, then by forecast volume as a tiebreaker within each recency date.
    def _sort_key(r):
        m = r["last_active_month"]
        if m is None:
            return (1, 0, -r["next6_total"])
        return (0, -date.fromisoformat(m).toordinal(), -r["next6_total"])
    rows.sort(key=_sort_key)
    return {"run": _run_info(run), "total": len(rows), "months": months,
            "rows": rows[offset:offset + limit]}


def _add_months(d: date, n: int) -> date:
    y = d.year + (d.month - 1 + n) // 12
    m = (d.month - 1 + n) % 12 + 1
    return date(y, m, 1)


def _latest_plan_by_month(session, item_id: int, level: str) -> dict[date, float]:
    """{month: value}, latest-snapshot-wins per FIX_PLAN C4 (a re-ingested
    corrected month must not be averaged with the stale value it
    replaced) — shared by item_detail's history and
    plan_vs_actual's trailing-history weeks."""
    rows = (session.query(PlanValue.month, PlanValue.value,
                          PlanValue.snapshot_id, Snapshot.file_mtime)
           .join(Snapshot, Snapshot.id == PlanValue.snapshot_id)
           .filter(PlanValue.item_id == item_id, PlanValue.level == level,
                   PlanValue.value.isnot(None))
           .order_by(PlanValue.month,
                    Snapshot.file_mtime.asc().nulls_first(),
                    PlanValue.snapshot_id.asc())
           .all())
    out: dict[date, float] = {}
    for m, v, _sid, _mtime in rows:
        out[_as_date(m)] = v                  # later (latest) overwrites
    return out


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
    # Per-MONTH merge, master preferred (see forecasting._series_by_item for
    # why this can't be a whole-item "or": master coverage commonly STOPS
    # partway through an item's timeline rather than being all-or-nothing).
    master_by_month = _latest_plan_by_month(session, item_id, "master")
    one_row_by_month = _latest_plan_by_month(session, item_id, "one_row")
    latest_by_month = {**one_row_by_month, **master_by_month}
    plan_rows = sorted(latest_by_month.items())
    history = [{"month": m.isoformat(), "qty": round(v, 1)}
               for m, v in plan_rows if m < start][-24:]
    one_plan = [{"month": m.isoformat(), "qty": round(v, 1)}
                for m, v in plan_rows if m >= start][:len(forecast) or 12]

    stock = _latest_stock_state(session, item_id)
    sugg = _suggestion(item, stock, [f["qty"] or 0.0 for f in forecast],
                       [_as_date(f["month"]) for f in forecast])
    if sugg is not None:
        oa = order_svc.get_active(session, item_id)
        sugg["order_action"] = _order_action_out(oa, date.today()) if oa is not None else None
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
                     "product_name": sap_product_name(session, item),
                     "category": item.category, "tier": item.tier,
                     "lifecycle": item.lifecycle, "moq": item.moq,
                     "lead_time_days": item.lead_time_days,
                     "observed_lead_time_days": item.observed_lead_time_days,
                     "observed_lead_time_samples": item.observed_lead_time_samples,
                     "effective_lead_time_days": effective_lead_time(item),
                     "min_stock_days": item.min_stock_days},
            "method": method, "wape": wape, "wape_full_horizon": wape_full_horizon,
            "history": history, "forecast": forecast, "one_plan": one_plan,
            "weekly": weekly,
            "stock": ({"one_balance": stock.one_balance, "sap_qty": stock.sap_qty,
                       "confidence": trusted_current_balance(stock)[1],
                       "snapshot_date": _as_date(stock.snapshot_date).isoformat()
                       if stock.snapshot_date else None} if stock else None),
            "suggestion": sugg}


def plan_vs_actual(session, item_id: int, history_months: int = 1) -> dict | None:
    """System-planned quantity (this item's monthly Forecast, WITH any
    adjustment applied — a planner's tune should show up here immediately,
    the whole point of this view) disaggregated to weeks, against real
    weekly SAP-shipped quantity (WeeklyActual — see ingestion/sap_shipments.py
    for where that comes from). Includes `history_months` of TRAILING
    history before the forecast's own start month, so recently-closed
    weeks aren't blank just because the forecast horizon itself only
    looks forward — the "planned" side of that history comes from the
    item's own real ONE plan (_latest_plan_by_month, same master/one_row
    merge item_detail's history uses), not the Forecast table, since a
    ForecastRun only ever stores forward months from whenever it was run.

    Week alignment caveat (see engine/weekly.py and models.WeeklyActual):
    the disaggregated plan is Monday-anchored; a WeeklyActual row's
    week_start is whatever date the source SAP export itself used, which
    can differ by several days between product lines. An actual week is
    matched to the planned week whose [Monday, Monday+7) span CONTAINS
    it, not by exact date equality — this is an approximation, not exact
    weekly-fitted accuracy (see engine/weekly.py's own honesty note), but
    it's the closest alignment the two sources support today. An actual
    week older than every planned week (real, found live 2026-07-15: SAP's
    rolling shipment window can start before the current forecast horizon
    does) is still shown, keyed to its own Monday-anchored week with
    planned_qty=None, rather than silently dropped.

    Tuning: this view does NOT accept writes itself — a planner tunes via
    the existing advisory/adjustments.record_adjustment (scope="sku"),
    the same accountable, audited, FVA-scored mechanism used everywhere
    else in this app. `weeks[].has_adjustment` tells the caller which
    weeks' month already carries a tune, so the UI can show that state
    without a second write path to keep in sync. History weeks (before
    the forecast horizon) are never tunable — a closed week is a fact,
    not a plan — `weeks[].is_history` marks them for the UI."""
    item = session.get(Item, item_id)
    if item is None:
        return None
    run = _latest_run(session)
    if run is None:
        return None
    frows = (session.query(Forecast)
             .filter(Forecast.run_id == run.id, Forecast.item_id == item_id)
             .order_by(Forecast.month).all())
    if not frows:
        return None

    month_dates = [_as_date(f.month) for f in frows]
    adjustments = _latest_adjustments(session)
    qtys, adj_detail = _apply_adjustments(item_id, month_dates,
                                          [f.baseline or 0.0 for f in frows], adjustments)

    hist_months = [_add_months(month_dates[0], -i) for i in range(history_months, 0, -1)]
    master_by_month = _latest_plan_by_month(session, item_id, "master")
    one_row_by_month = _latest_plan_by_month(session, item_id, "one_row")
    hist_by_month = {**one_row_by_month, **master_by_month}   # master preferred, see item_detail
    hist_qtys = [hist_by_month.get(m, 0.0) for m in hist_months]

    combined_months = hist_months + month_dates
    combined_qtys = hist_qtys + qtys

    weekly_plan = disaggregate_forecast(list(zip(combined_months, combined_qtys)))
    plan_by_week: dict[date, float] = {date.fromisoformat(w["week_start"]): w["qty"]
                                       for w in weekly_plan}
    sorted_weeks = sorted(plan_by_week)
    week_month: dict[date, date] = {}
    for wk in sorted_weeks:
        # each disaggregated week is attributed to the month its Monday
        # falls in, for grouping weeks under a single "tune this month"
        # control — an approximation at month-boundary weeks (see
        # disaggregate_forecast).
        candidates = [m for m in combined_months if m <= wk]
        week_month[wk] = max(candidates) if candidates else combined_months[0]

    actual_rows = (session.query(WeeklyActual)
                  .filter(WeeklyActual.item_id == item_id)
                  .order_by(WeeklyActual.week_start).all())
    actual_by_week: dict[date, float] = {}
    for r in actual_rows:
        idx = bisect.bisect_right(sorted_weeks, r.week_start) - 1
        if idx >= 0 and r.week_start < sorted_weeks[idx] + timedelta(days=7):
            wk = sorted_weeks[idx]
        else:
            # older than every planned week (or no planned weeks at all) —
            # still shown, keyed to its own Monday-anchored week.
            wk = r.week_start - timedelta(days=r.week_start.weekday())
        actual_by_week[wk] = actual_by_week.get(wk, 0.0) + (r.qty or 0.0)

    forecast_horizon_start = month_dates[0]
    all_weeks = sorted(set(plan_by_week) | set(actual_by_week))
    weeks_out = []
    for wk in all_weeks:
        planned = plan_by_week.get(wk)
        actual = actual_by_week.get(wk)
        # variance_qty (actual - planned, real units) alongside variance_pct
        # — added 2026-07-23 because % alone is unstable at low volume (a
        # planned=2/actual=4 week reads as "+100%", the same as a
        # planned=200/actual=400 week, even though one is a trivial miss
        # and the other is a large one). Same reason engine/baseline.py
        # scores accuracy with volume-weighted WAPE rather than raw % —
        # this view didn't have that protection until now; variance_qty
        # gives the reader the absolute-unit context % can't.
        variance_qty = (round(actual - planned, 1)
                        if planned is not None and actual is not None else None)
        variance_pct = (round((actual - planned) / planned * 100, 1)
                        if planned and actual is not None else None)
        month = week_month.get(wk, wk.replace(day=1))
        is_history = month < forecast_horizon_start
        month_idx = month_dates.index(month) if (not is_history and month in month_dates) else None
        weeks_out.append({
            "week_start": wk.isoformat(), "month": month.isoformat(),
            "planned_qty": planned, "actual_qty": actual,
            "variance_qty": variance_qty, "variance_pct": variance_pct,
            "is_history": is_history,
            "has_adjustment": bool(adj_detail.get(month_idx)) if month_idx is not None else False,
        })

    return {"item_id": item.id, "name": item.canonical_name, "weeks": weeks_out}
