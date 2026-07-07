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
from ..models import Adjustment, PlanValue, Actual, Item
from ..engine.fva import score_fva


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
        baseline_row = (session.query(PlanValue.value)
                        .filter(PlanValue.item_id == adj.item_id,
                                PlanValue.month == adj.month, PlanValue.level == "master")
                        .first())
        if actual_row is None or baseline_row is None:
            continue
        item = session.get(Item, adj.item_id)
        score = score_fva([baseline_row[0]], [adj.new_value], [actual_row[0]])
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
