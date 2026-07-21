"""Tests for advisory/order_actions.py — manual order-status tracking
(Phase-1: no PO/ERP integration, a human records what they actually did)."""
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from onefcst.advisory import order_actions as order_svc
from onefcst.models import AuditLog, Base, Item, OrderAction


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def seed_item(session):
    item = Item(canonical_name="TESTPART", category="CPU", kind="part")
    session.add(item)
    session.commit()
    return item


def test_mark_ordered_creates_an_active_row_and_logs_it(session):
    item = seed_item(session)
    oa = order_svc.mark_ordered(session, item, qty_ordered=500, note="urgent",
                                expected_arrival_date=date(2026, 8, 1), author="alice")
    assert oa.status == "ordered"
    assert oa.qty_ordered == 500
    assert oa.ordered_by == "alice"
    assert order_svc.get_active(session, item.id).id == oa.id
    assert session.query(AuditLog).filter(AuditLog.who == "alice").count() == 1


def test_cannot_mark_ordered_twice_while_one_is_active(session):
    item = seed_item(session)
    order_svc.mark_ordered(session, item, qty_ordered=100, note="",
                           expected_arrival_date=None, author="alice")
    with pytest.raises(order_svc.OrderActionAlreadyActive):
        order_svc.mark_ordered(session, item, qty_ordered=200, note="",
                               expected_arrival_date=None, author="bob")


def test_confirm_received_closes_the_loop_and_a_new_order_can_be_marked(session):
    item = seed_item(session)
    oa = order_svc.mark_ordered(session, item, qty_ordered=100, note="",
                                expected_arrival_date=date(2026, 8, 1), author="alice")
    order_svc.confirm_received(session, oa.id, author="alice")
    assert session.get(OrderAction, oa.id).status == "received"
    assert order_svc.get_active(session, item.id) is None

    # a fresh shortfall after receiving should be able to open a new record,
    # not be blocked by the now-resolved old one
    oa2 = order_svc.mark_ordered(session, item, qty_ordered=50, note="",
                                 expected_arrival_date=None, author="bob")
    assert oa2.id != oa.id
    assert order_svc.get_active(session, item.id).id == oa2.id


def test_dismiss_closes_the_loop_without_marking_received(session):
    item = seed_item(session)
    oa = order_svc.mark_ordered(session, item, qty_ordered=None, note="false positive",
                                expected_arrival_date=None, author="alice")
    order_svc.dismiss(session, oa.id, author="alice", reason="duplicate suggestion")
    assert session.get(OrderAction, oa.id).status == "dismissed"
    assert order_svc.get_active(session, item.id) is None


def test_extend_expected_date_updates_an_active_row(session):
    item = seed_item(session)
    oa = order_svc.mark_ordered(session, item, qty_ordered=100, note="",
                                expected_arrival_date=date(2026, 8, 1), author="alice")
    order_svc.extend_expected_date(session, oa.id, date(2026, 8, 15), author="alice")
    assert session.get(OrderAction, oa.id).expected_arrival_date == date(2026, 8, 15)


def test_confirm_received_on_an_already_resolved_row_raises(session):
    item = seed_item(session)
    oa = order_svc.mark_ordered(session, item, qty_ordered=100, note="",
                                expected_arrival_date=None, author="alice")
    order_svc.confirm_received(session, oa.id, author="alice")
    with pytest.raises(order_svc.OrderActionNotActive):
        order_svc.confirm_received(session, oa.id, author="alice")


def test_confirm_received_on_missing_id_raises_not_found(session):
    with pytest.raises(order_svc.OrderActionNotFound):
        order_svc.confirm_received(session, 999, author="alice")


def test_list_active_only_includes_ordered_status(session):
    a = Item(canonical_name="A", kind="part")
    b = Item(canonical_name="B", kind="part")
    session.add_all([a, b])
    session.commit()
    oa_a = order_svc.mark_ordered(session, a, qty_ordered=1, note="",
                                  expected_arrival_date=None, author="alice")
    oa_b = order_svc.mark_ordered(session, b, qty_ordered=1, note="",
                                  expected_arrival_date=None, author="alice")
    order_svc.dismiss(session, oa_b.id, author="alice")

    active = order_svc.list_active(session)
    assert set(active.keys()) == {a.id}
    assert active[a.id].id == oa_a.id
