"""Regression test for FIX_PLAN M1's second half — the near-term (M+1)
WAPE must be the primary per-item confidence signal served to the UI, with
the 6-month blended WAPE demoted to a secondary 'full horizon' caveat."""
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from onefcst.models import (Base, Item, ForecastRun, Forecast, AccuracyScore)
from onefcst.forecast_views import list_forecasts, item_detail


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _seed(session):
    item = Item(canonical_name="X870E", category="ﾏｻﾞｰﾎﾞｰﾄﾞ", kind="part")
    session.add(item)
    session.flush()
    run = ForecastRun(method_version="v1")
    session.add(run)
    session.flush()
    for i in range(3):
        session.add(Forecast(run_id=run.id, item_id=item.id,
                             month=date(2026, 8 + i, 1), baseline=100.0, method="combo"))
    # full-horizon WAPE is bad (121%), near-term M+1 is much better (12%) —
    # exactly the documented real-world gap this fix surfaces
    session.add(AccuracyScore(run_id=run.id, item_id=item.id, category=item.category,
                              month=date(2026, 8, 1), level="baseline",
                              wape=121.5, wape_m1=12.0))
    session.commit()
    return item, run


def test_list_forecasts_serves_m1_wape_as_primary_and_full_horizon_as_secondary(session):
    _seed(session)
    result = list_forecasts(session)
    row = result["rows"][0]
    assert row["wape"] == 12.0                    # near-term, primary
    assert row["wape_full_horizon"] == 121.5       # full horizon, secondary


def test_item_detail_serves_m1_wape_as_primary_and_full_horizon_as_secondary(session):
    item, _ = _seed(session)
    detail = item_detail(session, item.id)
    assert detail["wape"] == 12.0
    assert detail["wape_full_horizon"] == 121.5


def test_falls_back_to_full_horizon_wape_when_m1_is_unavailable(session):
    """Short-history items may have a full-horizon WAPE but no scoreable
    M+1 point (e.g. m1_actual was 0) — must not silently show no confidence
    signal at all."""
    item = Item(canonical_name="Sparse Part", kind="part")
    session.add(item)
    session.flush()
    run = ForecastRun(method_version="v1")
    session.add(run)
    session.flush()
    session.add(Forecast(run_id=run.id, item_id=item.id, month=date(2026, 8, 1),
                         baseline=10.0, method="flat3"))
    session.add(AccuracyScore(run_id=run.id, item_id=item.id, category=None,
                              month=date(2026, 8, 1), level="baseline",
                              wape=45.0, wape_m1=None))
    session.commit()

    detail = item_detail(session, item.id)
    assert detail["wape"] == 45.0
    assert detail["wape_full_horizon"] == 45.0
