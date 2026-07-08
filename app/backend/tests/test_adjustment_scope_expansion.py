"""Regression tests for FIX_PLAN C2 — global/category-scope adjustments were
silently accepted and stored but never applied to any forecast (only
scope="sku" was ever read downstream). _latest_adjustments must expand them
to every item they should affect."""
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from onefcst.models import Base, Item, Adjustment
from onefcst.forecast_views import _latest_adjustments, _apply_adjustments


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _seed_items(session):
    a = Item(canonical_name="CPU-A", category="CPU", kind="part")
    b = Item(canonical_name="CPU-B", category="CPU", kind="part")
    c = Item(canonical_name="VGA-A", category="グラフィックカード", kind="part")
    session.add_all([a, b, c])
    session.flush()
    return a, b, c


def test_category_adjustment_affects_every_item_in_that_category(session):
    a, b, c = _seed_items(session)
    month = date(2026, 8, 1)
    adj = Adjustment(item_id=a.id, month=month, old_value=100.0, new_value=120.0,
                     scope="category", reason="category ramp-up", author="tester",
                     created_at=datetime(2026, 7, 1))
    session.add(adj)
    session.commit()

    adjustments = _latest_adjustments(session)
    assert (a.id, month) in adjustments
    assert (b.id, month) in adjustments          # same category — must be affected
    assert (c.id, month) not in adjustments      # different category — untouched

    # anchor item gets the literal typed value; the swept-in item gets its
    # OWN baseline scaled by the same factor (120/100 = 1.2), not a's value
    final_a, _ = _apply_adjustments(a.id, [month], [100.0], adjustments)
    final_b, _ = _apply_adjustments(b.id, [month], [50.0], adjustments)
    assert final_a == [120.0]
    assert final_b == [60.0]


def test_global_adjustment_affects_every_item(session):
    a, b, c = _seed_items(session)
    month = date(2026, 8, 1)
    adj = Adjustment(item_id=a.id, month=month, old_value=100.0, new_value=110.0,
                     scope="global", reason="systematic +10%", author="tester",
                     created_at=datetime(2026, 7, 1))
    session.add(adj)
    session.commit()

    adjustments = _latest_adjustments(session)
    assert all((item.id, month) in adjustments for item in (a, b, c))

    final_c, _ = _apply_adjustments(c.id, [month], [200.0], adjustments)
    assert final_c[0] == pytest.approx(220.0)     # scaled by the 1.1 factor


def test_category_adjustment_without_enough_info_stays_scoped_to_its_item(session):
    """No factor and no old_value means there's nothing to safely scale other
    items by — falls back to affecting only the recorded item rather than
    pasting one absolute value onto every item in the category."""
    a, b, _ = _seed_items(session)
    month = date(2026, 8, 1)
    adj = Adjustment(item_id=a.id, month=month, old_value=None, new_value=120.0,
                     scope="category", reason="no baseline given", author="tester",
                     created_at=datetime(2026, 7, 1))
    session.add(adj)
    session.commit()

    adjustments = _latest_adjustments(session)
    assert (a.id, month) in adjustments
    assert (b.id, month) not in adjustments


def test_sku_scope_is_unaffected_by_the_expansion_logic(session):
    a, b, _ = _seed_items(session)
    month = date(2026, 8, 1)
    adj = Adjustment(item_id=a.id, month=month, old_value=100.0, new_value=999.0,
                     scope="sku", reason="one-off correction", author="tester",
                     created_at=datetime(2026, 7, 1))
    session.add(adj)
    session.commit()

    adjustments = _latest_adjustments(session)
    assert (a.id, month) in adjustments
    assert (b.id, month) not in adjustments
