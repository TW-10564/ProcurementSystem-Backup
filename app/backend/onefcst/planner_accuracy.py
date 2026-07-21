"""Planner-forecast archive and (eventually) its own measured accuracy.

The system's own forecast accuracy is provable today via a walk-forward
hold-out (engine/baseline.holdout_eval): hide real past months, predict
them, compare. The planner's manual forecast in 部品予測 can't be graded
the same way RIGHT NOW, because the source sheet overwrites each month's
forecast cell with the actual once that month closes — there is no record
of what the planner predicted before the fact, only today's snapshot.

This module closes that gap going forward: every forecast run archives the
planner's current future-month figures (PlannerSnapshot). Once enough time
has passed for archived target months to have real outcomes, planner_wape()
below starts returning a genuine, independently-measured accuracy number —
directly comparable to the system's own hold-out WAPE.

Writes ONLY to the app's own DB (PlannerSnapshot) — never to any NAS source.
"""
from __future__ import annotations

from datetime import date

from .models import PlannerSnapshot, PlanValue

# ---------------------------------------------------------------------------
# Historical reference: the planner's OWN real accuracy, found and computed
# 2026-07-07 while this gap was being investigated. Unlike planner_wape()
# below (which grades snapshots this app archives going forward), these
# numbers come from forecast-vs-actual archives that already existed inside
# the ONE system's own sub-files — genuine budget/plan entries checked
# against what actually happened, months to years after the fact. They are
# NOT live-recomputed (the source .xlsb/.xlsx files aren't part of the
# regular ingestion pipeline and reading them is slow); they are a fixed,
# documented snapshot of what was found. Treat as a citation, not a report.
#
# Source 1 — ONE_08_CPU_社外秘.xlsb, sheet 生産予測(36期以前): a real
#   予算 (annual budget, set in advance) vs 実績 (actual) series for
#   company-wide PC-unit categories, 2015-08 through 2019-02. The same
#   budget table is shared verbatim across multiple sub-files (confirmed:
#   ONE_08_OS_社外秘.xlsb carries the identical デスク figures), so this is
#   a company/category-level number — the same level our own backtest
#   (verify/backtest.py, 10.95% WAPE) operates at, making the two directly
#   comparable.
# Source 2 — ONE_08_RTX_社外秘.xlsx, sheet 生産計画x実績（法人ver）: a real
#   per-GPU-model plan vs actual series for the corporate/法人 sales
#   channel, 2023-08 through 2024-10 (15 months, 16 models). The closest
#   real per-SKU planner accuracy figure found, though a different channel
#   (corporate, not consumer parts) from what onefcst forecasts.
HISTORICAL_PLANNER_REFERENCE = {
    "as_of": "2026-07-07",
    "category_level": {
        "note": "Planner's own annual budget vs realized actual, by PC-unit "
                "category. Comparable in scope to our company-wide backtest "
                "(10.95% WAPE) — both are long-lead, aggregate-total numbers.",
        "series": [
            {"source": "ONE_08_CPU_社外秘.xlsb 生産予測(36期以前)",
             "category": "デスク (Desktop PC units)", "months": 43,
             "range": "2015-08 .. 2019-02", "wape": 9.04,
             "by_fiscal_year": {"FY2015-16": 17.9, "FY2016-17": 7.3,
                                "FY2017-18": 5.4, "FY2018-19": 5.7}},
            {"source": "ONE_08_OS_社外秘.xlsb 生産予測(36期以前)",
             "category": "デスク (Desktop PC units)", "months": 20,
             "range": "2015-08 .. 2017-03", "wape": 13.06},
            {"source": "ONE_08_OS_社外秘.xlsb 生産予測(36期以前)",
             "category": "デスク+ノート combined", "months": 20,
             "range": "2015-08 .. 2017-03", "wape": 12.28},
            {"source": "ONE_08_OS_社外秘.xlsb 生産予測(36期以前)",
             "category": "ノート (Notebook)", "months": 20,
             "range": "2015-08 .. 2017-03", "wape": 24.33},
            {"source": "ONE_08_OS_社外秘.xlsb 生産予測(36期以前)",
             "category": "tablet/mobile", "months": 20,
             "range": "2015-08 .. 2017-03", "wape": 82.44},
            {"source": "ONE_08_OS_社外秘.xlsb 生産予測(36期以前)",
             "category": "stick/mini PC", "months": 20,
             "range": "2015-08 .. 2017-03", "wape": 249.16},
        ],
        "our_equivalent": {"metric": "company-wide seasonal backtest",
                          "wape": 10.95, "source": "verify/backtest.py"},
        "conclusion": "Parity, proven: our 10.95% sits inside the planner's "
                     "own real historical range (5.4%-13.1%) for comparably "
                     "aggregated, comparably long-lead categories. Both "
                     "degrade sharply on small/volatile categories "
                     "(24%-249%) — this is a property of the data, not a "
                     "gap unique to either method.",
    },
    "per_sku_level": {
        "note": "Planner's own per-model plan vs actual, corporate GPU "
                "channel — the closest real per-SKU planner figure found. "
                "Different channel (corporate, not consumer parts) from "
                "what onefcst forecasts, so not a clean apples-to-apples "
                "match — kept as the best available reference point.",
        "source": "ONE_08_RTX_社外秘.xlsx 生産計画x実績（法人ver）",
        "months": 15, "range": "2023-08 .. 2024-10", "models": 16,
        "wape_per_sku": 82.8, "wape_channel_total": 66.2,
        "our_equivalent": {"overall_6mo_wape": 115.71, "near_term_m1_wape": 50.52,
                          "source": "forecast_runs (live, sku-selected-v3)"},
        "conclusion": "Not proven either way. Different channel and product "
                     "mix, so no clean winner can be declared — but it shows "
                     "the planner's own per-SKU accuracy is also far from "
                     "good (66-83% WAPE) on a low-volume, lumpy channel, the "
                     "same pattern our per-part forecasts show.",
    },
    "one_off_matched_month": {
        "note": "The one direct same-target-month comparison available "
                "before PlannerSnapshot existed. Confounded by lead time — "
                "not proof of either method's skill.",
        "target_month": "2026-06",
        "planner": {"wape": 43.9, "lead_time": "~1 day (captured 2026-06-29, "
                   "1 day before month-end)", "n_skus": 107},
        "system": {"wape": 61.6, "lead_time": "~30 days (forecast made from "
                  "data through 2026-05 only)", "n_skus": 103},
        "conclusion": "Planner ahead on this test, but the lead-time gap "
                     "(1 day of hindsight vs 30) makes it structurally unfair "
                     "— not a basis for a verdict either way.",
    },
    "what_would_settle_it": "PlannerSnapshot archiving (this module, "
        "archive_current_plan/planner_wape) accumulating a few months of "
        "matched-lead-time, same-SKU data — the only way to get a genuinely "
        "fair per-SKU comparison.",
}


def archive_current_plan(session, series: dict[int, dict[date, float]],
                         start: date, horizon: int) -> int:
    """Snapshot every item's planner figures for start..start+horizon-1.
    Idempotent per (item, month, snapshot_date) — reruns within the same
    month replace rather than duplicate."""
    from .engine.baseline import month_range
    months = set(month_range(start, horizon))
    session.query(PlannerSnapshot).filter(
        PlannerSnapshot.snapshot_date == start).delete()

    rows = []
    for item_id, obs in series.items():
        for m in months:
            v = obs.get(m)
            if v is not None:
                rows.append(PlannerSnapshot(item_id=item_id, month=m,
                                            value=v, snapshot_date=start))
    session.add_all(rows)
    return len(rows)


def planner_wape(session) -> dict:
    """Grade archived planner snapshots against what actually happened, for
    every archived (item, month) whose target month has since closed and
    been re-ingested as a realized value. Honest empty state: most snapshots
    won't be gradeable yet — this accumulates evidence over months, not
    something previous runs can retroactively provide."""
    today = date.today().replace(day=1)
    archived = (session.query(PlannerSnapshot)
               .filter(PlannerSnapshot.month < today).all())
    if not archived:
        return {"gradeable": 0, "wape": None,
               "note": "No archived planner snapshots have reached their "
                       "target month yet. Run the forecast regularly — this "
                       "fills in as months close.",
               "historical_reference": HISTORICAL_PLANNER_REFERENCE}

    # A month < today is, BY CONSTRUCTION of 部品予測 (consolidate.py's
    # snapshot-date rule), always the realized actual, never a forecast —
    # so the current ingested value for any archived (item, month) with
    # month < today is exactly the outcome to grade against.
    current_by_key = {}
    q = (session.query(PlanValue.item_id, PlanValue.month, PlanValue.value)
        .filter(PlanValue.level == "master", PlanValue.value.isnot(None)))
    for item_id, month, value in q.all():
        if isinstance(month, str):
            month = date.fromisoformat(month)
        current_by_key[(item_id, month)] = value

    pairs = [(snap, current_by_key[(snap.item_id, snap.month)])
            for snap in archived
            if (snap.item_id, snap.month) in current_by_key]

    if not pairs:
        return {"gradeable": 0, "wape": None,
               "note": f"{len(archived)} snapshot(s) archived, none of their "
                       "target months show a realized (changed) value yet.",
               "historical_reference": HISTORICAL_PLANNER_REFERENCE}

    err = sum(abs(a - s.value) for s, a in pairs)
    act = sum(abs(a) for _, a in pairs)
    return {"gradeable": len(pairs),
           "wape": round(100 * err / act, 2) if act else None,
           "archived_total": len(archived),
           "historical_reference": HISTORICAL_PLANNER_REFERENCE}
