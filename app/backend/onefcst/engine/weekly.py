"""Monthly → weekly forecast disaggregation — pure functions, no I/O.

Honest scope: this is NOT an independently-fitted weekly statistical
model. The forecast itself is still produced and validated at monthly
grain (engine/baseline.py) — this module only splits an already-validated
month's forecast across its overlapping calendar weeks, weighted by each
week's day-overlap with the month (so a week spanning a month boundary
gets its fair share of each side, not double- or under-counted).

Updated 2026-07-15: real weekly ACTUALS are now archived (WeeklyActual,
fed by ingestion/sap_shipments.py's raw per-week SAP shipment data,
before it gets aggregated into Actual's monthly grain) — see
forecast_views.plan_vs_actual, which compares this module's disaggregated
forecast against those real weekly actuals. That comparison is still not
a weekly-FITTED method (the forecast itself is unchanged, monthly-derived
math split across weeks) — a genuinely weekly-fitted method with its own
hold-out validation is future work, now that real weekly history exists
to validate one against."""
from __future__ import annotations

from datetime import date, timedelta


def _month_bounds(month: date) -> tuple[date, date]:
    """[start, end) of the calendar month containing `month` (day is
    normalized to the 1st)."""
    start = month.replace(day=1)
    end = date(start.year + (start.month == 12), (start.month % 12) + 1, 1)
    return start, end


def week_starts_covering(month: date) -> list[date]:
    """Monday-start dates of every ISO week that overlaps this month, in
    order — including weeks that spill into the adjacent month."""
    start, end = _month_bounds(month)
    first_monday = start - timedelta(days=start.weekday())   # Monday on/before day 1
    weeks = []
    w = first_monday
    while w < end:
        weeks.append(w)
        w += timedelta(days=7)
    return weeks


def disaggregate_month_to_weeks(month: date, qty: float) -> list[dict]:
    """Split one month's forecast quantity across its overlapping weeks,
    weighted by each week's day-overlap with the month. Weights always sum
    to 1.0 across the returned weeks, so qty is fully conserved (no units
    invented or dropped at month boundaries)."""
    start, end = _month_bounds(month)
    rows = []
    for week_start in week_starts_covering(month):
        week_end = week_start + timedelta(days=7)
        overlap_start = max(week_start, start)
        overlap_end = min(week_end, end)
        days_in_month = max(0, (overlap_end - overlap_start).days)
        if days_in_month == 0:
            continue
        rows.append({"week_start": week_start, "days_in_month": days_in_month})
    total_days = sum(r["days_in_month"] for r in rows)
    for r in rows:
        weight = r["days_in_month"] / total_days if total_days else 0.0
        r["qty"] = round(qty * weight, 1)
        r["weight"] = round(weight, 4)
    return rows


def disaggregate_forecast(monthly_forecast: list[tuple[date, float]]) -> list[dict]:
    """Apply disaggregate_month_to_weeks across a whole forecast series,
    merging weeks that get contributions from two adjacent months (a week
    spanning a month boundary appears once, summing both months' shares)."""
    by_week: dict[date, float] = {}
    for month, qty in monthly_forecast:
        for row in disaggregate_month_to_weeks(month, qty):
            by_week[row["week_start"]] = by_week.get(row["week_start"], 0.0) + row["qty"]
    return [{"week_start": w.isoformat(), "qty": round(q, 1)}
           for w, q in sorted(by_week.items())]
