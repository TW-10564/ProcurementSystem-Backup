"""Regression tests for forecast_views.order_suggestions — the detail
behind the dashboard's "orders needed" KPI. Requested so procurement can
click the count and see exactly which items, with the numbers needed to
assess real need (stock, suggested qty, timing), instead of a bare number."""
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from onefcst.models import Base, Item, ForecastRun, Forecast, StockState
from onefcst.forecast_views import order_suggestions, summary


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _seed_run(session):
    run = ForecastRun(method_version="v1")
    session.add(run)
    session.flush()
    return run


def test_only_items_that_actually_need_an_order_are_listed(session):
    needs_order = Item(canonical_name="Low Stock Part", category="CPU", kind="part")
    fine = Item(canonical_name="Plenty Stock Part", category="CPU", kind="part")
    session.add_all([needs_order, fine])
    session.flush()
    session.add(StockState(item_id=needs_order.id, sap_qty=10.0))
    session.add(StockState(item_id=fine.id, sap_qty=100_000.0))
    run = _seed_run(session)
    for i in range(3):
        session.add(Forecast(run_id=run.id, item_id=needs_order.id,
                             month=date(2026, 7 + i, 1), baseline=200.0, method="flat3"))
        session.add(Forecast(run_id=run.id, item_id=fine.id,
                             month=date(2026, 7 + i, 1), baseline=200.0, method="flat3"))
    session.commit()

    result = order_suggestions(session)
    names = {r["name"] for r in result["rows"]}
    assert "Low Stock Part" in names
    assert "Plenty Stock Part" not in names
    assert result["total"] == 1


def test_aggregates_by_category_and_month_and_flags_negative_stock(session):
    item_a = Item(canonical_name="A", category="CPU", kind="part")
    item_b = Item(canonical_name="B", category="MEM", kind="part")
    session.add_all([item_a, item_b])
    session.flush()
    session.add(StockState(item_id=item_a.id, sap_qty=-50.0))    # already short
    session.add(StockState(item_id=item_b.id, sap_qty=5.0))
    run = _seed_run(session)
    for i in range(3):
        session.add(Forecast(run_id=run.id, item_id=item_a.id,
                             month=date(2026, 7 + i, 1), baseline=500.0, method="flat3"))
        session.add(Forecast(run_id=run.id, item_id=item_b.id,
                             month=date(2026, 7 + i, 1), baseline=300.0, method="flat3"))
    session.commit()

    result = order_suggestions(session)
    assert result["total"] == 2
    assert result["negative_stock_count"] == 1
    cats = {c["category"] for c in result["by_category"]}
    assert cats == {"CPU", "MEM"}
    assert sum(c["items"] for c in result["by_category"]) == 2
    assert sum(m["items"] for m in result["by_month"]) == 2


def test_order_suggestions_total_matches_summary_orders_needed(session):
    """The KPI count and the detail list must agree — a mismatch would mean
    a buyer clicking the number gets a different count than what's shown."""
    item = Item(canonical_name="Needs Order", category="CPU", kind="part")
    session.add(item)
    session.flush()
    session.add(StockState(item_id=item.id, sap_qty=1.0))
    run = _seed_run(session)
    for i in range(3):
        session.add(Forecast(run_id=run.id, item_id=item.id,
                             month=date(2026, 7 + i, 1), baseline=400.0, method="flat3"))
    session.commit()

    result = order_suggestions(session)
    kpi = summary(session)
    assert result["total"] == kpi["orders_needed"]


def test_empty_when_no_run_exists(session):
    result = order_suggestions(session)
    assert result == {"run": None, "total": 0, "rows": [], "by_category": [],
                      "by_month": [], "negative_stock_count": 0, "total_qty": 0}
