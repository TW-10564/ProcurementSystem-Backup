"""Regression tests for the tier2_category_average / no-stock-tracking
finding from the 2026-07-08 Data Summary cross-check: 153 items forecasted
purely from a category-peer average, with zero stock tracking in SAP or
ONE, never produce an order suggestion — rolling their volume into the
dashboard's headline next3/next12 totals overstates what's actually
plannable. summary() must exclude them from the totals while still
surfacing the excluded volume, not silently dropping it."""
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from onefcst.models import Base, Item, ForecastRun, Forecast, StockState
from onefcst.forecast_views import summary, _is_unactionable_estimate


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


def test_is_unactionable_estimate_requires_both_conditions():
    assert _is_unactionable_estimate("tier2_category_average", None) is True
    assert _is_unactionable_estimate("tier2_category_average",
                                     StockState(sap_qty=None, one_balance=None)) is True
    # has stock -> actionable even via this method
    assert _is_unactionable_estimate("tier2_category_average",
                                     StockState(sap_qty=100.0)) is False
    # no stock, but not the category-average method -> not this bucket
    assert _is_unactionable_estimate("flat3", None) is False


def test_summary_excludes_no_stock_category_average_from_headline_totals(session):
    item = Item(canonical_name="47011", category="ｷｰﾎﾞｰﾄﾞN", kind="part")
    session.add(item)
    session.flush()
    run = _seed_run(session)
    for i in range(3):
        session.add(Forecast(run_id=run.id, item_id=item.id,
                             month=date(2026, 7 + i, 1), baseline=4526.0,
                             method="tier2_category_average"))
    session.commit()

    result = summary(session)
    cat = next(c for c in result["categories"] if c["category"] == "ｷｰﾎﾞｰﾄﾞN")
    assert cat["next3_total"] == 0                    # excluded, not counted
    assert cat["no_stock_tracking_items"] == 1
    assert result["excluded_no_stock_tracking"]["items"] == 1
    assert result["excluded_no_stock_tracking"]["next3_total"] == 4526 * 3


def test_summary_includes_category_average_item_that_does_have_stock(session):
    item = Item(canonical_name="Real Item", category="ｷｰﾎﾞｰﾄﾞN", kind="part")
    session.add(item)
    session.flush()
    session.add(StockState(item_id=item.id, sap_qty=500.0))
    run = _seed_run(session)
    for i in range(3):
        session.add(Forecast(run_id=run.id, item_id=item.id,
                             month=date(2026, 7 + i, 1), baseline=100.0,
                             method="tier2_category_average"))
    session.commit()

    result = summary(session)
    cat = next(c for c in result["categories"] if c["category"] == "ｷｰﾎﾞｰﾄﾞN")
    assert cat["next3_total"] == 300                  # counted — has real stock
    assert cat["no_stock_tracking_items"] == 0
    assert result["excluded_no_stock_tracking"]["items"] == 0
