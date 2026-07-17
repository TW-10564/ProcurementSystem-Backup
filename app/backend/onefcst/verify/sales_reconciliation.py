"""ONE (closed month) vs SAP (shipped actual) sales reconciliation —
surfaces where the two source systems' idea of "what actually sold" for
the same item-month disagrees, does not resolve it.

Why this exists (user's own concern, 2026-07-17): the forecasting engine
trains on ONE's own plan_values — a closed month's master-level cell gets
overwritten with ONE's own recorded actual once that month settles (see
verify/reconciliation.py's docstring for the same mechanism). Separately,
FVA scoring (verify/fva.py) grades adjustment quality against SAP's
Actual for that same month. These are two independently-measured numbers
for the same real-world shipments, with no cross-check between them
today — if they diverge, the model trains on one "truth" while FVA grades
against another, and nobody is told. This module is that missing check.

Ground truth rule: UNLIKE stock_reconciliation.py (where SAP always wins,
per the user's 2026-07-15 confirmation that ONE's stock is downstream of
SAP), there is no established priority between ONE's own closed-month
figure and SAP's shipped-quantity figure — they're plausibly measuring
different things (ONE's own accounting close vs SAP's shipment
transactions can differ by cutoff timing, return handling, etc.). This
module does NOT pick a winner; it flags the disagreement for a human,
same "system surfaces, human decides" pattern as reconciliation.py.

Only CLOSED months are compared — a month still open in ONE hasn't been
overwritten with an actual yet (it's still ONE's own forward plan for
that month), so comparing it against SAP's real shipped total would be
comparing a plan to an actual, not two actuals to each other. "Closed"
here means strictly before the current calendar month.

Data-depth caveat (real, as of 2026-07-17): SAP Actual ingestion only
started 2026-07-15 — there may be zero or very few overlapping
(item, month) pairs to compare yet. This report tells "checked, no
disagreement found" apart from "not enough overlapping history yet",
the same honesty pattern verify/discontinuation.py's zero-stock check
uses for the same underlying reason (shallow SAP history)."""
from __future__ import annotations

from datetime import date, timedelta

from ..db import get_session
from ..models import Item, PlanValue, Actual, WeeklyActual

MIN_RELATIVE_PCT = 15.0   # below this, treat as normal rounding/cutoff noise
MIN_ABSOLUTE = 5.0        # and require a real unit difference too
MAX_COVERAGE_GAP_DAYS = 10   # a month's earliest SAP week must start within
                             # this many days of day 1, or the month is
                             # missing its own beginning and isn't a real
                             # closed-month total to compare against


def _incomplete_sap_months(session, candidate_months: set[date]) -> set[date]:
    """Months where SAP's own weekly data doesn't actually cover the
    START of the calendar month — found 2026-07-17 (user's own question
    about the sales-reconciliation divergences): SAP部材管理_詳細版's rolling
    4-week window had already moved past early June by the time it was
    captured, so 'Actual.month=2026-06' silently summed only the LAST
    ~2.5 weeks of June (verified against real data: 12cm ｹｰｽFAN's June
    total of 1937 = exactly the 6/15+6/22+6/29 weekly rows, with 6/1-6/14
    entirely absent). Comparing that partial sum against ONE's TRUE
    full-month closed actual produced ~70 false "divergences" — SAP
    looking uniformly lower not because real demand differed, but because
    half the month's shipments were never captured. A month only counts
    as comparable if its earliest SAP week starts within
    MAX_COVERAGE_GAP_DAYS of day 1."""
    incomplete = set()
    for m in candidate_months:
        next_m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
        earliest = (session.query(WeeklyActual.week_start)
                   .filter(WeeklyActual.source == "SAP",
                           WeeklyActual.week_start >= m, WeeklyActual.week_start < next_m)
                   .order_by(WeeklyActual.week_start.asc()).first())
        if earliest is None or (earliest[0] - m).days > MAX_COVERAGE_GAP_DAYS:
            incomplete.add(m)
    return incomplete


def _closed_months(session) -> tuple[list[date], list[date]]:
    """Every month strictly before the current one that has at least one
    real (non-null) master plan_value AND at least one SAP Actual row —
    the actual overlap window, not an assumed fixed lookback. Returns
    (complete_months, excluded_incomplete_months) so the caller can report
    what was skipped and why, not just silently narrow the list."""
    this_month = date.today().replace(day=1)
    master_months = {m for (m,) in session.query(PlanValue.month)
                     .filter(PlanValue.level == "master", PlanValue.value.isnot(None),
                             PlanValue.month < this_month).distinct()}
    sap_months = {m for (m,) in session.query(Actual.month)
                 .filter(Actual.source == "SAP", Actual.month < this_month).distinct()}
    candidates = master_months & sap_months
    incomplete = _incomplete_sap_months(session, candidates)
    return sorted(candidates - incomplete), sorted(incomplete)


def compute_sales_reconciliation(session, min_relative_pct: float = MIN_RELATIVE_PCT,
                                 min_absolute: float = MIN_ABSOLUTE) -> dict:
    overlap_months, excluded_incomplete_months = _closed_months(session)

    one_rows = (session.query(PlanValue.item_id, PlanValue.month, PlanValue.value)
               .filter(PlanValue.level == "master", PlanValue.item_id.isnot(None),
                       PlanValue.value.isnot(None), PlanValue.month.in_(overlap_months))
               .all()) if overlap_months else []
    # latest-snapshot-wins per (item, month), same rule forecasting._level_series
    # applies — a re-ingested correction must not get averaged with a stale row.
    one_by_key: dict[tuple[int, date], float] = {}
    for item_id, month, value in one_rows:
        one_by_key[(item_id, month)] = float(value)

    sap_rows = (session.query(Actual.item_id, Actual.month, Actual.qty)
               .filter(Actual.source == "SAP", Actual.item_id.isnot(None),
                       Actual.month.in_(overlap_months))
               .all()) if overlap_months else []
    sap_by_key = {(item_id, month): float(qty) for item_id, month, qty in sap_rows}

    items = {i.id: i for i in session.query(Item).all()}
    compared = 0
    divergences = []
    for key in set(one_by_key) & set(sap_by_key):
        item_id, month = key
        one_val, sap_val = one_by_key[key], sap_by_key[key]
        compared += 1
        denom = max(abs(one_val), abs(sap_val), 1.0)
        diff = one_val - sap_val
        pct = round(abs(diff) / denom * 100, 1)
        if abs(diff) < min_absolute or pct < min_relative_pct:
            continue
        item = items.get(item_id)
        divergences.append({
            "item_id": item_id, "name": item.canonical_name if item else "?",
            "category": item.category if item else None,
            "month": month.isoformat(),
            "one_closed_actual": round(one_val, 1), "sap_actual": round(sap_val, 1),
            "diff": round(diff, 1), "pct_diff": pct,
            "higher_source": "ONE" if diff > 0 else "SAP",
        })

    divergences.sort(key=lambda d: -abs(d["diff"]))

    return {
        "overlap_months": [m.isoformat() for m in overlap_months],
        "excluded_incomplete_months": [m.isoformat() for m in excluded_incomplete_months],
        "compared_item_months": compared,
        "divergence_threshold_pct": min_relative_pct,
        "divergences": divergences,
        "note": ("ONE's own closed-month plan_value and SAP's shipped-quantity "
                "Actual for the SAME item-month, compared with no assumed "
                "winner (unlike stock, where SAP is always trusted — see "
                "stock_reconciliation.py). A listed item isn't necessarily "
                "wrong in either system; the forecasting engine trains on "
                "ONE's number while FVA scoring (verify/fva.py) grades "
                "against SAP's — a real disagreement here means those two "
                "processes are working from different ground truths for "
                "that item-month, worth a human decision on which to trust "
                "or whether to record via advisory/adjustments.py."
                if overlap_months else
                "No overlapping closed item-months between ONE and SAP yet — "
                "SAP shipment ingestion only started 2026-07-15, so there "
                "hasn't been a full month-close cycle to compare against. "
                "This will start populating as weekly SAP rebuilds accumulate "
                "past the next month boundary; an empty result here means "
                "'not enough history yet', not 'no disagreement found'."),
        "excluded_incomplete_note": ("Months excluded because SAP's own weekly "
                "data doesn't cover the START of that calendar month (see "
                "MAX_COVERAGE_GAP_DAYS) — found 2026-07-17: comparing a partial "
                "SAP month against ONE's true full-month total produced ~70 "
                "false 'divergences' that were actually a missing-data artifact, "
                "not a real disagreement. These months will become comparable "
                "once a SAP ingestion actually captures their first week."
                if excluded_incomplete_months else None),
    }


MIN_PARTIAL_COVERAGE_DAYS = 10   # below this many real days covered, the
                                 # ONE pro-rated estimate is too noisy to
                                 # compare against meaningfully


def compute_partial_window_reconciliation(session, min_relative_pct: float = MIN_RELATIVE_PCT,
                                           min_absolute: float = MIN_ABSOLUTE) -> dict:
    """For a month excluded from the full-month comparison (SAP data
    doesn't cover the month's start), compare SAP's real total for
    whatever days it DOES cover against ONE's own monthly figure
    PRO-RATED down to that exact same window — instead of waiting for a
    full closed month before saying anything at all (user's own direction,
    2026-07-17: 'for recent sales check the SAP data for the [available]
    weeks' rather than discarding it).

    Pro-ration is day-based, not weekly-disaggregated (simpler than
    engine/weekly.py's Monday-anchored weighting, and sufficient here —
    this is a rough early signal, not a precision comparison the way the
    full-month reconciliation above is): ONE's monthly value × (days
    actually covered by SAP / days in the month). ONE's month can be
    either its OWN closed actual (if the month has already closed) or,
    for the CURRENT open month, ONE's own forward plan for it — pro-rating
    a plan is still a fair comparison against partial real shipments
    (are we tracking the plan so far), just not a "two actuals agree"
    claim the way the full-month check makes."""
    this_month = date.today().replace(day=1)
    master_months = {m for (m,) in session.query(PlanValue.month)
                     .filter(PlanValue.level == "master", PlanValue.value.isnot(None)).distinct()}
    sap_weeks_by_month: dict[date, list[date]] = {}
    for (week_start,) in session.query(WeeklyActual.week_start).filter(WeeklyActual.source == "SAP").distinct():
        m = week_start.replace(day=1)
        sap_weeks_by_month.setdefault(m, []).append(week_start)

    results = []
    for month, weeks in sap_weeks_by_month.items():
        if month not in master_months:
            continue
        next_m = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
        days_in_month = (next_m - month).days
        covered_days = set()
        for w in weeks:
            for d in range(7):
                day = w + timedelta(days=d)
                if month <= day < next_m:
                    covered_days.add(day)
        n_covered = len(covered_days)
        if n_covered < MIN_PARTIAL_COVERAGE_DAYS or n_covered >= days_in_month - 3:
            continue   # too little data to trust, or already effectively a full month

        one_rows = (session.query(PlanValue.item_id, PlanValue.value)
                   .filter(PlanValue.level == "master", PlanValue.item_id.isnot(None),
                           PlanValue.value.isnot(None), PlanValue.month == month).all())
        one_by_item = {}
        for item_id, value in one_rows:
            one_by_item[item_id] = float(value)   # last write wins; good enough for this rough check

        sap_rows = (session.query(WeeklyActual.item_id, WeeklyActual.qty)
                   .filter(WeeklyActual.source == "SAP", WeeklyActual.item_id.isnot(None),
                           WeeklyActual.week_start.in_(weeks)).all())
        sap_by_item: dict[int, float] = {}
        for item_id, qty in sap_rows:
            sap_by_item[item_id] = sap_by_item.get(item_id, 0.0) + (qty or 0.0)

        items = {i.id: i for i in session.query(Item).all()}
        for item_id in set(one_by_item) & set(sap_by_item):
            one_prorated = one_by_item[item_id] * (n_covered / days_in_month)
            sap_val = sap_by_item[item_id]
            denom = max(one_prorated, sap_val, 1.0)
            diff = one_prorated - sap_val
            pct = round(abs(diff) / denom * 100, 1)
            if abs(diff) < min_absolute or pct < min_relative_pct:
                continue
            item = items.get(item_id)
            results.append({
                "item_id": item_id, "name": item.canonical_name if item else "?",
                "category": item.category if item else None,
                "month": month.isoformat(), "days_covered": n_covered,
                "days_in_month": days_in_month,
                "one_monthly_value": round(one_by_item[item_id], 1),
                "one_prorated_for_window": round(one_prorated, 1),
                "sap_actual_for_window": round(sap_val, 1),
                "diff": round(diff, 1), "pct_diff": pct,
                "higher_source": "ONE" if diff > 0 else "SAP",
            })

    results.sort(key=lambda r: -abs(r["diff"]))
    return {
        "results": results,
        "note": ("Rough early-signal comparison for months too incomplete for the "
                "full-month reconciliation above: SAP's real total for its "
                "actually-covered days vs ONE's monthly figure pro-rated to that "
                "SAME window (day-based, not weekly-disaggregated — a coarse "
                "estimate, not a precision claim). A real gap here is worth a "
                "look sooner than waiting for full month-close, but pro-ration "
                "assumes roughly even demand across the month, which is not "
                "always true (promotions, launches, month-end pushes) — treat "
                "as a lead indicator, not a verdict."),
    }


def run(session=None) -> dict:
    own_session = session is None
    if own_session:
        session = get_session()
    try:
        result = compute_sales_reconciliation(session)
        result["partial_window"] = compute_partial_window_reconciliation(session)
        return result
    finally:
        if own_session:
            session.close()


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2, ensure_ascii=False, default=str))
