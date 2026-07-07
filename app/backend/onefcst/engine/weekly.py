"""Monthly → weekly forecast disaggregation — pure functions, no I/O.

Honest scope: this is NOT an independently-fitted weekly statistical model.
No weekly-granularity historical actuals are persisted anywhere in the
pipeline (部品予測 is monthly-only; the procurement team's own SAP管理
Excel tracks daily/weekly figures but only as a live current snapshot, not
an archived history to backtest against) — so there is nothing to fit a
weekly method on yet, and claiming weekly-level accuracy we haven't earned
would repeat the exact overclaiming mistake this project has spent all
session correcting for the monthly engine.

What this DOES do, honestly: split each month's already-validated forecast
into calendar weeks, weighted by how many of that week's days actually fall
in the month (so a week spanning a month boundary gets its fair share of
each side, not double- or under-counted). This matches the real cadence
this system now runs on — SAP data refreshes every Monday, the procurement
team's own stock-setting review happens Friday — and it is the necessary
scaffolding for a future demand-sensing layer, which NEEDS a weekly
baseline to compare real daily/weekly pace against.

If/when weekly actuals start being archived (e.g. from the scheduler's own
recurring runs, see scheduler.py), a genuinely weekly-fitted method with
its own hold-out validation becomes possible — same evolution the monthly
engine already went through.
"""
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
