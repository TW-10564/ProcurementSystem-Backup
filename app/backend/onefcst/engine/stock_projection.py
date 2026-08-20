"""Stock projection + order suggestion (発注提案) — pure functions, no I/O.

Projects each item's stock forward from its current balance against monthly
forecast demand (no restocking assumed in the projection itself — the whole
point is to find WHEN a restock is needed), then suggests an order sized to
MOQ and timed to lead time so the order lands before the safety-stock line
is breached.

This is a SUGGESTION only (REQ-017 / BR-013): it proposes timing and
quantity; a human buyer reviews, edits, or dismisses it. Nothing here places
an order or writes anywhere outside the caller's own return value.
"""
from __future__ import annotations

import calendar
from datetime import date


def prorate_current_month(monthly_demand: list[float], first_month_start: date,
                          snapshot_date: date | None) -> list[float]:
    """Scale month 0's demand down to only the days remaining after the stock
    snapshot date, so project_stock() doesn't subtract demand for days that
    already happened and are already reflected in current_balance.

    Found 2026-08-14 via UAT (W11HAD): current_balance is dated to whatever
    day the snapshot was taken (e.g. the 9th), not the 1st of the month, but
    monthly_demand[0] was always the FULL month's total — subtracting it
    double-counted the elapsed days' demand (manual calc 15,031 vs system
    12,289 for W11HAD; the 2,742 gap matched the Aug 1-9 demand almost
    exactly). If the snapshot predates the first forecast month entirely
    (stale stock data), no proration happens — that demand is genuinely
    still all ahead of it, so subtracting the full month is correct."""
    if not monthly_demand or snapshot_date is None or snapshot_date < first_month_start:
        return monthly_demand
    days_in_month = calendar.monthrange(first_month_start.year, first_month_start.month)[1]
    month_end = first_month_start.replace(day=days_in_month)
    remaining_days = max(0, (month_end - snapshot_date).days)
    fraction = remaining_days / days_in_month
    out = list(monthly_demand)
    out[0] = (out[0] or 0.0) * fraction
    return out


def project_stock(current_balance: float, monthly_demand: list[float]) -> list[float]:
    """Month-end balance after each month's demand, starting from current_balance.
    No incoming supply assumed — this is the "if nothing is ordered" baseline
    the suggestion engine measures against."""
    out = []
    bal = current_balance
    for d in monthly_demand:
        bal = bal - (d or 0.0)
        out.append(bal)
    return out


def find_breach_month(projected: list[float], safety_stock: float) -> int | None:
    """Index of the first month projected balance drops below safety_stock,
    or None if it never does within the horizon."""
    for i, bal in enumerate(projected):
        if bal < safety_stock:
            return i
    return None


MOQ_EXCESSIVE_MONTHS = 6   # default flag threshold — months of average demand
                           # a final MOQ-rounded order must exceed to be
                           # flagged; callers may override via
                           # moq_excessive_months (see config.MOQ_EXCESSIVE_MONTHS
                           # for the env-configurable value used in production)


def suggest_order(*, current_balance: float, monthly_demand: list[float],
                  moq: float | None, lead_time_days: float | None,
                  min_stock_days: float | None, avg_monthly_demand: float | None = None,
                  moq_excessive_months: float = MOQ_EXCESSIVE_MONTHS
                  ) -> dict:
    """Returns a suggestion dict — never an executed action.

    safety_stock is derived from min_stock_days × average monthly demand
    (falls back to the horizon's own average if avg_monthly_demand omitted).
    order_by_month_index is shifted earlier by lead_time_days so the order
    LANDS before the breach, not is merely placed on the breach date.

    target_level's extra cushion above safety_stock is sized to
    lead_time_days (2026-07-17, replacing a flat "+1 average month"): the
    legitimate reason to hold more than the bare safety line is that a NEW
    order itself takes lead_time_days to arrive, during which demand keeps
    drawing the balance down further — a flat one-month pad was unrelated
    to that and inflated every suggestion regardless of the item's real
    lead time (confirmed against real items 2026-07-17: an 18-day-lead-time
    SSD was getting sized for ~1.75 months of demand despite lead time +
    safety policy only justifying ~1.3 months). A zero/unknown lead time
    now correctly adds no extra cushion beyond safety_stock itself."""
    demand_avg = avg_monthly_demand or (sum(d or 0.0 for d in monthly_demand) / len(monthly_demand)
                                        if monthly_demand else 0.0)
    safety_stock = (min_stock_days or 0) / 30.0 * demand_avg
    projected = project_stock(current_balance, monthly_demand)
    breach = find_breach_month(projected, safety_stock)

    if breach is None:
        return {"action": "none", "reason": "stock does not breach safety threshold "
               "within the forecast horizon", "projected": [round(p, 1) for p in projected]}

    lead_months = (lead_time_days or 0) / 30.0
    order_by_index = max(0, breach - int(lead_months + 0.999))   # round up, order early

    # top up back to a healthy level: safety_stock plus enough to survive
    # the lead time of the very order this suggestion is placing
    target_level = safety_stock + lead_months * demand_avg
    shortfall = target_level - projected[breach]
    qty = max(shortfall, 0.0)
    moq_applied = False
    if moq and qty > 0:
        import math
        rounded = math.ceil(qty / moq) * moq
        moq_applied = rounded > qty
        qty = rounded

    # moq_months_of_cover / moq_excessive (added 2026-07-17, found via real-
    # data audit): MOQ values come straight from ONE's own 部品マスター
    # sheet with no unit conversion — for a low-volume item, rounding UP
    # to a large supplier MOQ can turn a real few-unit need into an order
    # covering YEARS of demand (found live: a limited-run packaging item
    # with ~1 unit/month demand and MOQ=850 → 850 months of cover in one
    # order). math.ceil(qty/moq)*moq is not wrong — that IS the smallest
    # order size the supplier accepts — but a buyer scanning a list of
    # suggestions has no way to tell "this is a normal 2-month reorder"
    # from "this is MOQ forcing 70 years of stock" without this flag.
    moq_months_of_cover = round(qty / demand_avg, 1) if demand_avg > 0 else None
    moq_excessive = bool(moq_applied and moq_months_of_cover is not None
                         and moq_months_of_cover > moq_excessive_months)

    return {"action": "suggest_order", "order_by_month_index": order_by_index,
           "breach_month_index": breach, "suggested_qty": round(qty, 1),
           "safety_stock": round(safety_stock, 1),
           "projected": [round(p, 1) for p in projected],
           "moq_applied": moq_applied,
           "moq_months_of_cover": moq_months_of_cover,
           "moq_excessive": moq_excessive}
