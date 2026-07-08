"""FVA report — finds adjustments old enough to score against a real actual,
and honestly reports when none exist yet.

Honesty note (same discipline as verify/order_suggestions.py): this system
started operating today. A real FVA history requires adjustments made in the
past whose target month has since completed with an actual on record. Until
onefcst has been running for several monthly cycles, this report will
legitimately show few or zero scoreable adjustments — that is the correct,
honest state, not a bug.
"""
from __future__ import annotations

from datetime import date

from ..db import get_session
from ..models import Adjustment, Actual, Item, Forecast, ForecastRun
from ..engine.fva import score_fva


def _original_baseline(session, adj: Adjustment) -> float | None:
    """The statistical forecast the adjustment actually overrode — NEVER the
    current master cell, which gets overwritten with the actual once the
    month closes (FIX_PLAN C1). Prefers old_value (captured verbatim at
    adjustment time); falls back to the Forecast row from whichever run was
    live when the adjustment was recorded."""
    if adj.old_value is not None:
        return adj.old_value
    run_id = adj.run_id
    if run_id is None:
        run = (session.query(ForecastRun)
              .filter(ForecastRun.created_at <= adj.created_at)
              .order_by(ForecastRun.created_at.desc()).first())
        run_id = run.id if run else None
    if run_id is None:
        return None
    row = (session.query(Forecast.baseline)
          .filter(Forecast.run_id == run_id, Forecast.item_id == adj.item_id,
                  Forecast.month == adj.month).first())
    return row[0] if row else None


def scoreable_adjustments(session) -> list[dict]:
    """Adjustments whose target month is in the past AND has an actual on
    record for the same item — the only cases we can honestly score."""
    today = date.today().replace(day=1)
    adjustments = (session.query(Adjustment)
                  .filter(Adjustment.month < today).all())
    out = []
    for adj in adjustments:
        actual_row = (session.query(Actual.qty)
                     .filter(Actual.item_id == adj.item_id, Actual.month == adj.month)
                     .first())
        baseline_value = _original_baseline(session, adj)
        if actual_row is None or baseline_value is None:
            continue
        item = session.get(Item, adj.item_id)
        score = score_fva([baseline_value], [adj.new_value], [actual_row[0]])
        out.append({"adjustment_id": adj.id, "item": item.canonical_name if item else "?",
                   "month": adj.month.isoformat(), "author": adj.author,
                   "reason": adj.reason, **score})
    return out


def run() -> dict:
    session = get_session()
    total_adjustments = session.query(Adjustment).count()
    scoreable = scoreable_adjustments(session)
    session.close()
    helped = sum(1 for s in scoreable if s["verdict"] == "adjustment_helped")
    hurt = sum(1 for s in scoreable if s["verdict"] == "adjustment_hurt")
    return {"total_adjustments_recorded": total_adjustments,
           "scoreable_count": len(scoreable), "helped": helped, "hurt": hurt,
           "scores": scoreable,
           "note": ("No adjustments old enough to score yet — this system started "
                    "operating today. FVA scoring will populate as adjustments made "
                    "now reach their target month and an actual is recorded."
                    if not scoreable else None)}


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2, default=str))
