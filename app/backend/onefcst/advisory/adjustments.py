"""Planner adjustment layer (REQ-005 / BR-003 Adjustment Accountability).

Every override on top of the baseline forecast — a global factor, a per-SKU
value, or a scenario event (launch ramp, EOL pull-forward) — is recorded here
with author, timestamp and a mandatory reason. This is the formalisation of
what the ④→ONE divergence analysis showed already happens today, unlogged
(a systematic ~0.836-type factor plus per-SKU overrides): the same shape of
change, but now attributable and auditable instead of silently invisible.
"""
from __future__ import annotations

import json
from datetime import date

from ..models import Adjustment, AuditLog, Item
from .notifications import event as _event


def record_adjustment(session, *, item_id: int | None, month, old_value: float | None,
                      new_value: float, scope: str, author: str, reason: str,
                      run_id: int | None = None, factor: float | None = None) -> Adjustment:
    if not reason or not reason.strip():
        raise ValueError("adjustment reason is required (BR-003: accountability)")
    if scope not in ("global", "category", "sku"):
        raise ValueError("scope must be one of: global, category, sku")
    # forecast months are always day-1; a mid-month date (e.g. 2026-05-15,
    # easy to submit via the API) would be stored verbatim and then never
    # match any forecast month in _latest_adjustments / FVA — silently dead
    if month is not None:
        if isinstance(month, str):
            month = date.fromisoformat(month)
        month = month.replace(day=1)
    adj = Adjustment(run_id=run_id, item_id=item_id, month=month,
                     old_value=old_value, new_value=new_value, factor=factor,
                     scope=scope, reason=reason.strip(), author=author)
    session.add(adj)
    session.flush()
    item = session.get(Item, item_id) if item_id is not None else None
    item_name = item.canonical_name if item else {"global": "全品目", "category": "このカテゴリ"}.get(scope, scope)
    old_display = old_value if old_value is not None else "—"
    month_display = month.isoformat() if month else "—"
    session.add(AuditLog(
        who=author, what=f"adjustment#{adj.id} recorded ({scope}, item={item.canonical_name if item else scope})",
        before=json.dumps({"old_value": old_value}, default=str),
        after=json.dumps({"new_value": new_value, "factor": factor, "reason": reason,
                         **_event("adjustment.recorded", item_name=item_name, scope=scope,
                                 month_display=month_display, old_display=old_display,
                                 new_display=new_value)}, default=str)))
    session.commit()
    return adj


def list_adjustments(session, item_id: int | None = None, month=None,
                     limit: int = 200) -> list[Adjustment]:
    q = session.query(Adjustment)
    if item_id is not None:
        q = q.filter(Adjustment.item_id == item_id)
    if month is not None:
        q = q.filter(Adjustment.month == month)
    return q.order_by(Adjustment.created_at.desc()).limit(limit).all()


def adjustment_rate(session, run_id: int | None = None) -> dict:
    """% of forecast cells touched by a recorded adjustment — the metric
    Success Criterion #4 (Charter v1.1) tracks over time."""
    from ..models import Forecast
    q = session.query(Forecast)
    if run_id is not None:
        q = q.filter(Forecast.run_id == run_id)
    total = q.count()
    aq = session.query(Adjustment)
    if run_id is not None:
        aq = aq.filter(Adjustment.run_id == run_id)
    adjusted = aq.count()
    return {"total_forecast_cells": total, "adjusted_cells": adjusted,
           "rate_pct": round(100 * adjusted / total, 1) if total else None}
