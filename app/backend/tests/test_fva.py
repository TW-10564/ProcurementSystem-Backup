"""Regression tests for FIX_PLAN C1 — FVA must score adjustments against the
ORIGINAL forecast baseline, never the current master cell (which has already
been overwritten with the actual once the month closes)."""
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from onefcst.models import (Base, Item, ForecastRun, Forecast, Adjustment,
                            Actual, WeeklyActual)
from onefcst.verify.fva import _original_baseline, scoreable_adjustments


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def test_original_baseline_prefers_old_value(session):
    """old_value, captured verbatim at adjustment time, is the most direct
    record of what the forecast said before the override — always prefer it
    when present."""
    item = Item(canonical_name="X870E", kind="part")
    session.add(item)
    session.flush()
    adj = Adjustment(item_id=item.id, month=date(2026, 5, 1),
                     old_value=42.0, new_value=78.0, scope="sku",
                     reason="test", author="tester", created_at=datetime(2026, 4, 1))
    session.add(adj)
    session.commit()
    assert _original_baseline(session, adj) == 42.0


def test_original_baseline_falls_back_to_forecast_row_via_run_lookup(session):
    """When old_value wasn't captured (run_id also unset, as the API leaves
    it today), fall back to the Forecast.baseline row from whichever run was
    live when the adjustment was recorded — never the master PlanValue cell,
    which gets overwritten with the actual once the month closes."""
    item = Item(canonical_name="X870E", kind="part")
    session.add(item)
    session.flush()
    run = ForecastRun(method_version="v1", created_at=datetime(2026, 4, 1))
    session.add(run)
    session.flush()
    fc = Forecast(run_id=run.id, item_id=item.id, month=date(2026, 5, 1),
                 baseline=55.0, method="seasonal")
    session.add(fc)
    adj = Adjustment(item_id=item.id, month=date(2026, 5, 1),
                     old_value=None, new_value=78.0, scope="sku",
                     reason="test", author="tester", created_at=datetime(2026, 4, 15))
    session.add(adj)
    session.commit()
    assert _original_baseline(session, adj) == 55.0


def test_fva_shows_adjustment_helped_when_it_actually_did(session):
    """The bug this closes: comparing against the (already-overwritten)
    master cell made baseline == actual, so every adjustment scored as
    "adjustment_hurt" no matter what. Using the true original baseline lets
    a genuinely-better adjustment show adjustment_helped."""
    item = Item(canonical_name="X870E", kind="part")
    session.add(item)
    session.flush()
    run = ForecastRun(method_version="v1", created_at=datetime(2026, 4, 1))
    session.add(run)
    session.flush()
    target_month = date(2026, 5, 1)
    session.add(Forecast(run_id=run.id, item_id=item.id, month=target_month,
                         baseline=20.0, method="seasonal"))
    session.add(Actual(item_id=item.id, month=target_month, qty=75.0, source="test"))
    # the 2026-07-17 incomplete-SAP-month gate skips any month with no SAP
    # weekly coverage from day 1 — seed one row so the month counts as closed
    session.add(WeeklyActual(item_id=item.id, week_start=target_month,
                             qty=20.0, source="SAP"))
    adj = Adjustment(item_id=item.id, month=target_month, old_value=None,
                     new_value=78.0, scope="sku", reason="X3D phase-out",
                     author="tester", created_at=datetime(2026, 4, 15))
    session.add(adj)
    session.commit()

    scores = scoreable_adjustments(session)
    assert len(scores) == 1
    assert scores[0]["verdict"] == "adjustment_helped"


def test_fva_skips_adjustment_with_no_recoverable_baseline(session):
    """If neither old_value nor a matching Forecast row exists, honestly
    skip rather than fabricate a baseline."""
    item = Item(canonical_name="Orphan", kind="part")
    session.add(item)
    session.flush()
    target_month = date(2026, 5, 1)
    session.add(Actual(item_id=item.id, month=target_month, qty=75.0, source="test"))
    adj = Adjustment(item_id=item.id, month=target_month, old_value=None,
                     new_value=78.0, scope="sku", reason="no history",
                     author="tester", created_at=datetime(2026, 4, 15))
    session.add(adj)
    session.commit()

    assert scoreable_adjustments(session) == []
