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
from .notifications import event as _event


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
    qty_display = qty_ordered if qty_ordered is not None else "—"
    date_display = expected_arrival_date.isoformat() if expected_arrival_date else "—"
    _log(session, author, f"order_action#{oa.id} marked ordered (item#{item.id} "
        f"{item.canonical_name}, qty={qty_ordered}, expected={expected_arrival_date})",
        None, {"status": "ordered", "qty_ordered": qty_ordered,
               "expected_arrival_date": expected_arrival_date,
               **_event("order_action.marked_ordered", item_name=item.canonical_name,
                       qty_display=qty_display, date_display=date_display)})
    session.commit()
    return oa


def raise_due_alerts(session, today: date | None = None) -> int:
    """Write one AuditLog entry the first time each active order's expected
    arrival date is reached, so it surfaces on the Notifications page and
    the Dashboard activity feed (both just read AuditLog — see
    notifications.py's docstring for why this isn't a separate table).

    Before this, an item marked "ordered" with a date only ever showed a
    passive badge on the Forecast page's order-suggestions row
    (`awaiting_confirmation`, forecast_views._order_action_out) — nothing
    told the buyer the date had arrived unless they happened to look.

    Idempotent per (order_action, expected_arrival_date): the exact date is
    embedded in the logged message and checked before inserting, so calling
    this repeatedly (every scheduler poll, every notifications-page load)
    never double-alerts. Pushing the date out via extend_expected_date()
    changes the marker, so a re-extended order correctly alerts again if
    the new date also arrives instead of the buyer relying on the old row's
    already-fired alert being anything more than a snapshot.
    """
    today = today or date.today()
    due = (session.query(OrderAction, Item.canonical_name)
          .join(Item, Item.id == OrderAction.item_id)
          .filter(OrderAction.status == "ordered",
                  OrderAction.expected_arrival_date.isnot(None),
                  OrderAction.expected_arrival_date <= today)
          .all())
    if not due:
        return 0

    def _marker(oa) -> str:
        return f"order_action#{oa.id} arrival due ({oa.expected_arrival_date.isoformat()})"

    # `what` is "{marker}: {rest}", not the bare marker, so dedup has to be a
    # prefix match — an exact-equality lookup against the marker set would
    # never hit and every poll would log a fresh duplicate. The ilike is
    # just a coarse pre-filter (`_` is a LIKE wildcard, so it can also
    # over-match) — the exact check happens below in Python.
    already_logged = [w for (w,) in session.query(AuditLog.what)
                      .filter(AuditLog.who == "system",
                              AuditLog.what.ilike("order%action#%"))
                      .all()]

    raised = 0
    for oa, name in due:
        marker = _marker(oa)
        if any(w.startswith(marker) for w in already_logged):
            continue
        session.add(AuditLog(
            who="system",
            what=f"{marker}: {name} expected to arrive — confirm receipt or extend the date (警告)",
            after=json.dumps({"order_action_id": oa.id, "item_id": oa.item_id,
                             "expected_arrival_date": oa.expected_arrival_date,
                             **_event("order_action.arrival_due", item_name=name,
                                     date_display=oa.expected_arrival_date.isoformat())},
                             default=str),
        ))
        raised += 1
    if raised:
        session.commit()
    return raised


def extend_expected_date(session, order_action_id: int, new_date: date, author: str) -> OrderAction:
    oa = session.get(OrderAction, order_action_id)
    if oa is None:
        raise OrderActionNotFound(f"order action {order_action_id} not found")
    if oa.status != "ordered":
        raise OrderActionNotActive(f"order action {order_action_id} is '{oa.status}', not 'ordered'")
    before = {"expected_arrival_date": oa.expected_arrival_date}
    oa.expected_arrival_date = new_date
    item = session.get(Item, oa.item_id)
    item_name = item.canonical_name if item else f"item#{oa.item_id}"
    _log(session, author,
        f"order_action#{order_action_id} expected date pushed to {new_date} "
        f"(item#{oa.item_id} {item_name})",
        before, {"expected_arrival_date": new_date,
                 **_event("order_action.date_extended", item_name=item_name,
                         date_display=new_date.isoformat())})
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
    item = session.get(Item, oa.item_id)
    item_name = item.canonical_name if item else f"item#{oa.item_id}"
    _log(session, author,
        f"order_action#{order_action_id} confirmed received (item#{oa.item_id} {item_name})",
        before, {"status": "received", **_event("order_action.received", item_name=item_name)})
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
    item = session.get(Item, oa.item_id)
    item_name = item.canonical_name if item else f"item#{oa.item_id}"
    reason_display = f"（理由: {reason}）" if reason else ""
    _log(session, author,
        f"order_action#{order_action_id} dismissed (item#{oa.item_id} {item_name}): {reason}",
        before, {"status": "dismissed", "reason": reason,
         **_event("order_action.dismissed", item_name=item_name, reason_display=reason_display)})
    session.commit()
    return oa
