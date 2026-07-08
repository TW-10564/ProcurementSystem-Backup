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


def suggest_order(*, current_balance: float, monthly_demand: list[float],
                  moq: float | None, lead_time_days: float | None,
                  min_stock_days: float | None, avg_monthly_demand: float | None = None
                  ) -> dict:
    """Returns a suggestion dict — never an executed action.

    safety_stock is derived from min_stock_days × average monthly demand
    (falls back to the horizon's own average if avg_monthly_demand omitted).
    order_by_month_index is shifted earlier by lead_time_days so the order
    LANDS before the breach, not is merely placed on the breach date.
    """
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

    # top up back to a healthy level: cover min_stock_days beyond the breach point
    target_level = safety_stock + demand_avg   # 1 extra month of buffer above safety line
    shortfall = target_level - projected[breach]
    qty = max(shortfall, 0.0)
    if moq and qty > 0:
        import math
        qty = math.ceil(qty / moq) * moq

    return {"action": "suggest_order", "order_by_month_index": order_by_index,
           "breach_month_index": breach, "suggested_qty": round(qty, 1),
           "safety_stock": round(safety_stock, 1),
           "projected": [round(p, 1) for p in projected],
           "moq_applied": bool(moq and qty > 0)}
