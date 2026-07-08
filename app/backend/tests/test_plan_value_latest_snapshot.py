"""Regression tests for FIX_PLAN C4 — re-ingesting a source file with a
corrected month must use ONLY the latest snapshot's value, not average it
with the stale value it replaced."""
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from onefcst.models import Base, Item, Snapshot, PlanValue
from onefcst.forecasting import _series_by_item


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def test_series_by_item_uses_latest_snapshot_not_average(session):
    item = Item(canonical_name="X870E", kind="part")
    session.add(item)
    session.flush()
    stale = Snapshot(source_file="ONE計画_old.xlsm", file_mtime=datetime(2026, 1, 1))
    fresh = Snapshot(source_file="ONE計画_corrected.xlsm", file_mtime=datetime(2026, 6, 1))
    session.add_all([stale, fresh])
    session.flush()

    month = date(2026, 5, 1)
    # same (item, month) ingested twice: a stale bad value, then a correction
    session.add(PlanValue(snapshot_id=stale.id, item_id=item.id, month=month,
                          level="master", value=10.0))
    session.add(PlanValue(snapshot_id=fresh.id, item_id=item.id, month=month,
                          level="master", value=90.0))
    session.commit()

    series = _series_by_item(session)
    # averaging (the old bug) would give 50.0 — must be the latest value only
    assert series[item.id][month] == 90.0
