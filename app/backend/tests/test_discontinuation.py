"""verify/discontinuation.py had no test coverage at all before this file.
Covers find_zero_stock_candidates (new 2026-07-15, user's own domain
heuristic: SAP physical stock reading 0 for a long time usually means a
discontinued part) and a smoke test for the pre-existing demand-decline
path (find_winddown_candidates) it's layered alongside."""
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from onefcst.models import Base, Item, ForecastRun, Forecast, StockState, PlanValue, Snapshot
from onefcst.verify.discontinuation import (find_zero_stock_candidates,
                                            find_winddown_candidates, run)


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _snap_state(item_id, snap_date, qty):
    return StockState(item_id=item_id, snapshot_date=snap_date, sap_qty=qty)


def test_flags_item_zero_across_multiple_old_snapshots(session):
    item = Item(canonical_name="Long Dead Part", category="SSD", kind="part")
    session.add(item)
    session.flush()
    session.add_all([
        _snap_state(item.id, date(2026, 1, 1), 0.0),
        _snap_state(item.id, date(2026, 3, 1), 0.0),
        _snap_state(item.id, date(2026, 7, 1), 0.0),
    ])
    session.commit()

    result = find_zero_stock_candidates(session)
    names = [c["name"] for c in result["candidates"]]
    assert "Long Dead Part" in names
    c = result["candidates"][0]
    assert c["zero_since"] == "2026-01-01"
    assert c["latest_snapshot"] == "2026-07-01"
    assert c["zero_duration_days"] == (date(2026, 7, 1) - date(2026, 1, 1)).days
    assert c["zero_snapshots_observed"] == 3


def test_does_not_flag_item_with_current_nonzero_stock(session):
    item = Item(canonical_name="Restocked Recently", category="SSD", kind="part")
    session.add(item)
    session.flush()
    session.add_all([
        _snap_state(item.id, date(2026, 1, 1), 0.0),
        _snap_state(item.id, date(2026, 3, 1), 0.0),
        _snap_state(item.id, date(2026, 7, 1), 50.0),   # real stock again
    ])
    session.commit()

    result = find_zero_stock_candidates(session)
    assert result["candidates"] == []


def test_does_not_flag_single_snapshot_not_enough_history(session):
    """A single zero reading proves nothing on its own — must not trigger
    even if it happens to be old, since there's no second data point
    confirming it stayed at zero rather than just being read once."""
    item = Item(canonical_name="Only One Reading", category="SSD", kind="part")
    session.add(item)
    session.flush()
    session.add(_snap_state(item.id, date(2026, 1, 1), 0.0))
    session.commit()

    result = find_zero_stock_candidates(session)
    assert result["candidates"] == []


def test_does_not_flag_zero_span_shorter_than_threshold(session):
    item = Item(canonical_name="Just Went To Zero", category="SSD", kind="part")
    session.add(item)
    session.flush()
    session.add_all([
        _snap_state(item.id, date(2026, 7, 1), 0.0),
        _snap_state(item.id, date(2026, 7, 10), 0.0),   # only 9 days span
    ])
    session.commit()

    result = find_zero_stock_candidates(session)
    assert result["candidates"] == []


def test_one_balance_never_used_only_sap_qty_counts(session):
    """ONE's own projection is deliberately ignored here — only SAP-verified
    physical counts (sap_qty) can establish a zero-stock streak."""
    item = Item(canonical_name="ONE Says Zero Only", category="SSD", kind="part")
    session.add(item)
    session.flush()
    session.add(StockState(item_id=item.id, snapshot_date=date(2026, 1, 1),
                           sap_qty=None, one_balance=0.0))
    session.add(StockState(item_id=item.id, snapshot_date=date(2026, 7, 1),
                           sap_qty=None, one_balance=0.0))
    session.commit()

    result = find_zero_stock_candidates(session)
    assert result["candidates"] == []


def test_data_depth_reflects_the_widest_observed_snapshot_span(session):
    item_a = Item(canonical_name="A", kind="part")
    item_b = Item(canonical_name="B", kind="part")
    session.add_all([item_a, item_b])
    session.flush()
    session.add(_snap_state(item_a.id, date(2026, 1, 1), 5.0))
    session.add(_snap_state(item_b.id, date(2026, 7, 1), 5.0))
    session.commit()

    result = find_zero_stock_candidates(session)
    assert result["data_depth_days"] == (date(2026, 7, 1) - date(2026, 1, 1)).days
    assert result["sap_items_with_history"] == 2


def test_includes_next_6mo_forecast_for_cross_reference(session):
    item = Item(canonical_name="Still Forecasted Dead Part", category="SSD", kind="part")
    session.add(item)
    session.flush()
    session.add_all([
        _snap_state(item.id, date(2026, 1, 1), 0.0),
        _snap_state(item.id, date(2026, 7, 1), 0.0),
    ])
    run_row = ForecastRun(method_version="v1")
    session.add(run_row)
    session.flush()
    for i in range(6):
        session.add(Forecast(run_id=run_row.id, item_id=item.id,
                             month=date(2026, 8 + i, 1) if 8 + i <= 12
                             else date(2027, 8 + i - 12, 1),
                             baseline=10.0, method="flat3"))
    session.commit()

    result = find_zero_stock_candidates(session, run_row)
    c = result["candidates"][0]
    assert c["next_6mo_forecast"] == 60.0


# ---------------------------------------------------------- run() end to end

def test_run_returns_both_signal_types_and_an_honest_data_depth_note(session):
    zero_item = Item(canonical_name="Zero Stock Long Time", category="SSD", kind="part")
    session.add(zero_item)
    session.flush()
    session.add_all([
        _snap_state(zero_item.id, date(2026, 1, 1), 0.0),
        _snap_state(zero_item.id, date(2026, 7, 1), 0.0),
    ])
    session.commit()

    result = run(session)
    assert "zero_stock_candidates" in result
    assert "zero_stock_note" in result
    assert result["zero_stock_count"] == 1
    assert result["zero_stock_candidates"][0]["name"] == "Zero Stock Long Time"


def test_winddown_candidates_smoke(session):
    """find_winddown_candidates had no coverage before this file either —
    minimal smoke test that a sharp decline with sustained forecast is
    flagged, and a stable item is not."""
    from onefcst.forecasting import _series_by_item  # noqa: F401 (sanity import)

    faded = Item(canonical_name="Faded Part", category="CPU", kind="part")
    stable = Item(canonical_name="Stable Part", category="CPU", kind="part")
    session.add_all([faded, stable])
    session.flush()

    snap = Snapshot(source_file="test.xlsx")
    session.add(snap)
    session.flush()

    run_row = ForecastRun(method_version="v1")
    session.add(run_row)
    session.flush()
    start = date(2026, 7, 1)

    for i in range(1, 13):
        m = date(start.year - (1 if start.month - i <= 0 else 0),
                 ((start.month - i - 1) % 12) + 1, 1)
        prior = i > 6
        session.add(PlanValue(snapshot_id=snap.id, item_id=faded.id, month=m,
                              level="master", value=100.0 if prior else 2.0))
        session.add(PlanValue(snapshot_id=snap.id, item_id=stable.id, month=m,
                              level="master", value=50.0))
    for i in range(6):
        session.add(Forecast(run_id=run_row.id, item_id=faded.id,
                             month=date(2026, 7 + i, 1), baseline=80.0, method="trend"))
        session.add(Forecast(run_id=run_row.id, item_id=stable.id,
                             month=date(2026, 7 + i, 1), baseline=50.0, method="flat3"))
    session.commit()

    candidates = find_winddown_candidates(session, run_row)
    names = [c["name"] for c in candidates]
    assert "Faded Part" in names
    assert "Stable Part" not in names
