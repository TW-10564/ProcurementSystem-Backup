"""Master vs sub-file (one_row) source reconciliation — surfaces
disagreement, does not resolve it.

Found 2026-07-08 during a Data Summary cross-check: for 13 CPU/国内一般品
items, ONE計画's own 部品予測 (master — a chain-formula-derived rollup,
already known to carry at least one documented corruption; see
engine/tier2.OUTLIER_MULTIPLE) disagreed with the raw per-category
sub-file's own daily production-plan sum (one_row) for the SAME item and
month, captured at essentially the same moment (snapshot mtimes ~1 second
apart — not a staleness gap). one_row matched an independent cross-check
in all 13 cases; master didn't.

This does NOT mean one_row is always right and master always wrong —
master is the file actually used in planning meetings, so a disagreement
can just as easily be a deliberate, considered correction a planner made
there as it can be a formula bug. The system has no way to tell those
apart from the numbers alone. So rather than silently preferring either
source, this module flags every disagreement above a real-signal
threshold so a human can look at it and, if it's a genuine data issue,
either fix it at the root (in ONE計画.xlsm) or record it via the existing
Adjustment layer (advisory/adjustments.py) with a reason — the same
"system surfaces, human decides" pattern already used for CompatRule
drafts (BR-010).

CLASSIFICATION (added 2026-07-08, after digging into WHY two items —
D2T and 外付けBD/DVD — disagreed): master's cells for CLOSED months get
overwritten with the actual, so master and one_row trivially agree for
any month that's already settled; disagreement only exists for OPEN
(not-yet-actual) months, where master runs its own chain-formula forecast
against the category's own directly-entered daily plan. Digging into the
month-by-month ratio for several flagged items surfaced two distinct
shapes:
  - D2T, 外付けBD, 外付けDVD, CA-099A/100A: ratio == 1.0 for every closed
    month, then SNAPS to a near-constant value (0.8068485303254019 for
    D2T, repeating to 10+ decimal places) the instant the month opens,
    and holds there — the signature of a single frozen/broken coefficient
    in master's formula chain, not two forecasts organically diverging.
  - ry77700, ry79700X, ry55500GT: the ratio is noisy and gets WORSE each
    month (0.83→0.75→0.62→0.71 for ry77700) — consistent with two
    actively-maintained, genuinely different forecasts (a BOM-chain
    estimate vs the category's own plan) drifting apart, not a single
    bug. This needs a planning decision, not a spreadsheet fix.
_classify() tells these apart by the coefficient of variation of the
ratio across an item's own flagged months — low CV (barely moves) reads
as a frozen coefficient; high CV (moves meaningfully) reads as two
forecasts disagreeing.
"""
from __future__ import annotations

import statistics
from datetime import date

from ..engine.baseline import add_months
from ..models import Item, PlanValue

MIN_RELATIVE_PCT = 15.0   # below this, treat as normal rounding/day-count noise
MIN_ABSOLUTE = 5.0        # and require a real unit difference too — a 200% swing
                          # on a 1-unit item isn't worth a human's time
RATIO_CV_THRESHOLD = 0.05 # coefficient of variation (stdev/mean) of the
                          # master/one_row ratio across an item's flagged
                          # months, below which the ratio counts as "held
                          # essentially constant" — see module docstring.


def _classify(ratios: list[float]) -> tuple[str, float | None]:
    """(classification, ratio_cv). Needs >=2 flagged months for this item
    to say anything about whether the ratio moves — with only one data
    point there's nothing to compare it to."""
    if len(ratios) < 2:
        return "insufficient_data", None
    mean = statistics.mean(ratios)
    if mean == 0:
        return "insufficient_data", None
    cv = statistics.stdev(ratios) / abs(mean)
    classification = "likely_formula_bug" if cv < RATIO_CV_THRESHOLD else "methodology_disagreement"
    return classification, round(cv, 4)


def compute_discrepancies(session, start: date | None = None, months: int = 4,
                          min_relative_pct: float = MIN_RELATIVE_PCT,
                          min_absolute: float = MIN_ABSOLUTE) -> list[dict]:
    """Every (item, month) in the near-term window where BOTH master and
    one_row have a real, nonzero value and disagree by more than the
    threshold. `months` defaults to 4 — the window one_row actually has
    committed coverage for (see forecasting._series_by_item's own note on
    the ~4-month coverage cliff); comparing beyond that is comparing real
    data against silence, not a genuine disagreement."""
    start = start or date.today().replace(day=1)
    target_months = [add_months(start, i) for i in range(months)]
    rows = (session.query(PlanValue.item_id, PlanValue.level, PlanValue.month,
                          PlanValue.value)
            .filter(PlanValue.level.in_(["master", "one_row"]),
                    PlanValue.item_id.isnot(None),
                    PlanValue.month.in_(target_months),
                    PlanValue.value.isnot(None))
            .all())
    by_key: dict[tuple, dict] = {}
    for item_id, level, month, value in rows:
        by_key.setdefault((item_id, month), {})[level] = value

    items = {i.id: i for i in session.query(Item).all()}
    out = []
    for (item_id, month), levels in by_key.items():
        master, one_row = levels.get("master"), levels.get("one_row")
        if master is None or one_row in (None, 0):
            continue
        diff = master - one_row
        pct = 100 * diff / one_row
        if abs(diff) < min_absolute or abs(pct) < min_relative_pct:
            continue
        item = items.get(item_id)
        out.append({"item_id": item_id, "item_name": item.canonical_name if item else "?",
                   "category": item.category if item else None,
                   "month": month.isoformat(),
                   "master": round(master, 1), "one_row": round(one_row, 1),
                   "diff": round(diff, 1), "pct_diff": round(pct, 1),
                   "_ratio": master / one_row})

    # classify per item using ONLY its own flagged months' ratios — see
    # module docstring for why a frozen coefficient (low variance) reads
    # differently from two forecasts drifting apart (high variance).
    by_item: dict[int, list[dict]] = {}
    for row in out:
        by_item.setdefault(row["item_id"], []).append(row)
    for rows_for_item in by_item.values():
        classification, cv = _classify([r["_ratio"] for r in rows_for_item])
        for r in rows_for_item:
            r["classification"] = classification
            r["ratio_cv"] = cv
            del r["_ratio"]

    out.sort(key=lambda r: -abs(r["diff"]))
    return out


def run(session=None, start: date | None = None) -> dict:
    from ..db import get_session
    own_session = session is None
    session = session or get_session()
    try:
        rows = compute_discrepancies(session, start=start)
        affected_items = len({r["item_id"] for r in rows})

        by_classification: dict[str, dict] = {}
        for r in rows:
            c = by_classification.setdefault(r["classification"],
                                             {"count": 0, "items": set()})
            c["count"] += 1
            c["items"].add(r["item_id"])
        by_classification = {k: {"count": v["count"], "affected_items": len(v["items"])}
                            for k, v in by_classification.items()}

        return {"discrepancies": rows, "count": len(rows),
               "affected_items": affected_items,
               "by_classification": by_classification,
               "note": ("master (ONE計画's own 部品予測) and the sub-file's own "
                        "daily production plan disagree for these item-months by "
                        f">{MIN_RELATIVE_PCT:.0f}%. Each row is classified: "
                        "'likely_formula_bug' — the master/one_row ratio holds "
                        "essentially constant across the item's flagged months, "
                        "the signature of one frozen coefficient in master's "
                        "formula chain — worth a direct look at that cell in "
                        "ONE計画.xlsm. 'methodology_disagreement' — the ratio "
                        "moves month to month, consistent with two genuinely "
                        "different, actively-maintained forecasts (master's "
                        "chain-formula vs the category's own daily plan) — needs "
                        "a planning decision, not a spreadsheet fix. "
                        "'insufficient_data' — fewer than 2 flagged months for "
                        "that item, not enough to tell the two apart yet. "
                        "Either way: a listed item isn't necessarily wrong in "
                        "master; fix at the root or record via Adjustment once "
                        "a human has looked.")}
    finally:
        if own_session:
            session.close()


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2, ensure_ascii=False, default=str))
