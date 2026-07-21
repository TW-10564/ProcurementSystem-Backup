"""Manual order-status tracking (Phase-1: no PO/ERP write-back — a human
records what they actually did, mirroring the "system surfaces, human
decides" pattern already used for CompatRule confirm/reject and
discontinuation_review.py's draft/confirm/reject queue).

Lifecycle per item: open (no row) -> "ordered" (buyer records qty + expected
arrival date) -> either "received" (confirmed by a human) or "dismissed"
(acknowledged, no order actually placed) -> back to open once resolved, so a
fresh suggestion after that point creates a new row rather than reopening an
old one. Only one active ("ordered") row may exist per item at a time —
callers must check get_active() first, same discipline
discontinuation_review.create_draft() already documents for Suggestion."""
from __future__ import annotations

import json
from datetime import date, datetime

from ..models import OrderAction, Item, AuditLog


class OrderActionNotFound(Exception):
    pass


class OrderActionAlreadyActive(Exception):
    pass


class OrderActionNotActive(Exception):
    pass


def _log(session, who: str, what: str, before, after) -> None:
    session.add(AuditLog(who=who, what=what,
                         before=json.dumps(before, default=str) if before is not None else None,
                         after=json.dumps(after, default=str) if after is not None else None))


def get_active(session, item_id: int) -> OrderAction | None:
    return (session.query(OrderAction)
           .filter(OrderAction.item_id == item_id, OrderAction.status == "ordered")
           .order_by(OrderAction.ordered_at.desc()).first())


def list_active(session) -> dict[int, OrderAction]:
    """{item_id: OrderAction} for every item with a currently active
    ('ordered') row — for forecast_views.order_suggestions() to join onto
    its computed rows without a per-item query."""
    rows = session.query(OrderAction).filter(OrderAction.status == "ordered").all()
    return {r.item_id: r for r in rows}


def mark_ordered(session, item: Item, *, qty_ordered: float | None,
                 expected_arrival_date: date | None, note: str, author: str) -> OrderAction:
    if get_active(session, item.id) is not None:
        raise OrderActionAlreadyActive(
            f"item {item.id} already has an active order action — "
            f"confirm receipt or dismiss it first")
    oa = OrderAction(item_id=item.id, status="ordered", qty_ordered=qty_ordered,
                     expected_arrival_date=expected_arrival_date, note=note,
                     ordered_by=author)
    session.add(oa)
    session.flush()
    _log(session, author, f"order_action#{oa.id} marked ordered (item#{item.id} "
        f"{item.canonical_name}, qty={qty_ordered}, expected={expected_arrival_date})",
        None, {"status": "ordered", "qty_ordered": qty_ordered,
               "expected_arrival_date": expected_arrival_date})
    session.commit()
    return oa


def extend_expected_date(session, order_action_id: int, new_date: date, author: str) -> OrderAction:
    oa = session.get(OrderAction, order_action_id)
    if oa is None:
        raise OrderActionNotFound(f"order action {order_action_id} not found")
    if oa.status != "ordered":
        raise OrderActionNotActive(f"order action {order_action_id} is '{oa.status}', not 'ordered'")
    before = {"expected_arrival_date": oa.expected_arrival_date}
    oa.expected_arrival_date = new_date
    _log(session, author, f"order_action#{order_action_id} expected date pushed to {new_date}",
        before, {"expected_arrival_date": new_date})
    session.commit()
    return oa


def confirm_received(session, order_action_id: int, author: str) -> OrderAction:
    oa = session.get(OrderAction, order_action_id)
    if oa is None:
        raise OrderActionNotFound(f"order action {order_action_id} not found")
    if oa.status != "ordered":
        raise OrderActionNotActive(f"order action {order_action_id} is '{oa.status}', not 'ordered'")
    before = {"status": oa.status}
    oa.status = "received"
    oa.received_by = author
    oa.received_at = datetime.utcnow()
    _log(session, author, f"order_action#{order_action_id} confirmed received", before,
        {"status": "received"})
    session.commit()
    return oa


def dismiss(session, order_action_id: int, author: str, reason: str = "") -> OrderAction:
    oa = session.get(OrderAction, order_action_id)
    if oa is None:
        raise OrderActionNotFound(f"order action {order_action_id} not found")
    if oa.status != "ordered":
        raise OrderActionNotActive(f"order action {order_action_id} is '{oa.status}', not 'ordered'")
    before = {"status": oa.status}
    oa.status = "dismissed"
    oa.received_by = author
    oa.received_at = datetime.utcnow()
    _log(session, author, f"order_action#{order_action_id} dismissed: {reason}", before,
        {"status": "dismissed", "reason": reason})
    session.commit()
    return oa
