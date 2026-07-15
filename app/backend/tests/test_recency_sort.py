"""list_forecasts()'s default order should put the most-recently-selling
parts on top, then progressively older parts, with items that have no
recency signal at all (no Actual, no PlanValue) sorted last."""
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from onefcst.models import Base, Item, ForecastRun, Forecast, Actual, PlanValue, Snapshot
from onefcst.forecast_views import list_forecasts


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _item_with_forecast(session, run, name, next12_total=100.0):
    item = Item(canonical_name=name, kind="part")
    session.add(item)
    session.flush()
    session.add(Forecast(run_id=run.id, item_id=item.id, month=date(2026, 8, 1),
                         baseline=next12_total, method="combo"))
    return item


def test_most_recently_sold_item_sorts_first(session):
    run = ForecastRun(method_version="v1")
    session.add(run)
    session.flush()

    stale = _item_with_forecast(session, run, "Stale Part")
    fresh = _item_with_forecast(session, run, "Fresh Part")
    session.flush()
    session.add(Actual(item_id=stale.id, month=date(2026, 1, 1), qty=5.0, source="ZP21"))
    session.add(Actual(item_id=fresh.id, month=date(2026, 6, 1), qty=5.0, source="ZP21"))
    session.commit()

    rows = list_forecasts(session)["rows"]
    names = [r["name"] for r in rows]
    assert names.index("Fresh Part") < names.index("Stale Part")
    assert rows[names.index("Fresh Part")]["last_active_month"] == "2026-06-01"
    assert rows[names.index("Stale Part")]["last_active_month"] == "2026-01-01"


def test_zero_qty_actuals_do_not_count_as_selling(session):
    run = ForecastRun(method_version="v1")
    session.add(run)
    session.flush()
    item = _item_with_forecast(session, run, "Dead Stock")
    session.flush()
    session.add(Actual(item_id=item.id, month=date(2026, 6, 1), qty=0.0, source="ZP21"))
    session.commit()

    row = list_forecasts(session)["rows"][0]
    assert row["last_active_month"] is None


def test_falls_back_to_plan_value_when_no_actuals_exist(session):
    """Tier-2 accessory parts are often derived-only, with no Actual rows —
    they should still get a recency signal from PlanValue, not silently
    sort as if they have none."""
    run = ForecastRun(method_version="v1")
    session.add(run)
    session.flush()
    item = _item_with_forecast(session, run, "Accessory Part")
    snap = Snapshot(source_file="test.xlsx")
    session.add(snap)
    session.flush()
    session.add(PlanValue(snapshot_id=snap.id, item_id=item.id, month=date(2026, 5, 1),
                          level="master", value=20.0))
    session.commit()

    row = list_forecasts(session)["rows"][0]
    assert row["last_active_month"] == "2026-05-01"


def test_future_plan_value_does_not_count_as_already_selling(session):
    """PlanValue is a forward-looking plan — a nonzero FUTURE month must not
    be read as "just sold"; only past/current PlanValue months count."""
    run = ForecastRun(method_version="v1")
    session.add(run)
    session.flush()
    item = _item_with_forecast(session, run, "Future Only Part")
    snap = Snapshot(source_file="test.xlsx")
    session.add(snap)
    session.flush()
    future = date(date.today().year + 1, 1, 1)
    session.add(PlanValue(snapshot_id=snap.id, item_id=item.id, month=future,
                          level="master", value=20.0))
    session.commit()

    row = list_forecasts(session)["rows"][0]
    assert row["last_active_month"] is None


def test_items_with_no_recency_signal_at_all_sort_last(session):
    run = ForecastRun(method_version="v1")
    session.add(run)
    session.flush()

    known = _item_with_forecast(session, run, "Known Recency")
    unknown = _item_with_forecast(session, run, "No Recency At All")
    session.flush()
    session.add(Actual(item_id=known.id, month=date(2026, 1, 1), qty=1.0, source="ZP21"))
    session.commit()

    rows = list_forecasts(session)["rows"]
    assert rows[-1]["name"] == "No Recency At All"
    assert rows[-1]["last_active_month"] is None
