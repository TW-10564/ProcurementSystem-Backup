"""Regression tests for two related bugs found during the 2026-07-08 Data
Summary cross-check:

1. Items whose demand history lives ONLY under PlanValue.level ==
   "one_row" (11 OS licenses + a handful of others) got zero forecast,
   full stop — _series_by_item was the pipeline's only history source and
   it read exclusively level == "master".

2. The fix for #1 fell back to one_row only when an item had NO master
   rows AT ALL — but the far more common real case is an item whose
   master coverage simply STOPS partway through (e.g. a memory SKU being
   phased out: master has real history through some month, then goes
   silent, while the sub-file's one_row series keeps recording explicit
   zeros for the months after). A whole-item fallback never looks at
   one_row for such an item, since it "already has" master data — it just
   silently drops the months master doesn't cover. The merge must be
   per (item, month), not per item."""
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from onefcst.models import Base, Item, Snapshot, PlanValue
from onefcst.forecasting import _series_by_item
from onefcst.forecast_views import item_detail


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _snap(session, mtime):
    snap = Snapshot(source_file="ONE_08_OS_社外秘.xlsx", file_mtime=mtime)
    session.add(snap)
    session.flush()
    return snap


def test_series_by_item_falls_back_to_one_row_when_no_master_history(session):
    item = Item(canonical_name="W11HAD", category="ｿﾌﾄｳｪｱS", kind="part")
    session.add(item)
    session.flush()
    snap = _snap(session, datetime(2026, 6, 1))
    session.add(PlanValue(snapshot_id=snap.id, item_id=item.id,
                          month=date(2026, 5, 1), level="one_row", value=9173.1))
    session.commit()

    series = _series_by_item(session)
    assert series[item.id][date(2026, 5, 1)] == 9173.1


def test_series_by_item_prefers_master_when_both_exist(session):
    """481 items have both — master (cross-file-reconciled) must always
    win, one_row is a fallback only, never a competing/blended source."""
    item = Item(canonical_name="X870E", kind="part")
    session.add(item)
    session.flush()
    snap = _snap(session, datetime(2026, 6, 1))
    month = date(2026, 5, 1)
    session.add(PlanValue(snapshot_id=snap.id, item_id=item.id,
                          month=month, level="master", value=100.0))
    session.add(PlanValue(snapshot_id=snap.id, item_id=item.id,
                          month=month, level="one_row", value=999.0))
    session.commit()

    series = _series_by_item(session)
    assert series[item.id][month] == 100.0


def test_item_detail_history_falls_back_to_one_row(session):
    item = Item(canonical_name="W11HAD", category="ｿﾌﾄｳｪｱS", kind="part")
    session.add(item)
    session.flush()
    snap = _snap(session, datetime(2026, 6, 1))
    session.add(PlanValue(snapshot_id=snap.id, item_id=item.id,
                          month=date(2026, 5, 1), level="one_row", value=9173.1))
    session.commit()

    detail = item_detail(session, item.id)
    assert any(h["qty"] == 9173.1 for h in detail["history"])


def test_series_by_item_merges_per_month_when_master_coverage_stops_partway(session):
    """Direct reproduction: LD5-16G48 has real master history through
    April, then master goes silent — but the sub-file (one_row) keeps
    recording explicit zeros for May onward because it's still a tracked,
    just-discontinued SKU. Both must show up in the merged series: master
    for the months it covers, one_row filling the months after."""
    item = Item(canonical_name="LD5-16G48", category="メモリ", kind="part")
    session.add(item)
    session.flush()
    snap = _snap(session, datetime(2026, 6, 1))
    session.add(PlanValue(snapshot_id=snap.id, item_id=item.id,
                          month=date(2026, 4, 1), level="master", value=10678.0))
    for m in (date(2026, 5, 1), date(2026, 6, 1), date(2026, 7, 1)):
        session.add(PlanValue(snapshot_id=snap.id, item_id=item.id,
                              month=m, level="one_row", value=0.0))
    session.commit()

    series = _series_by_item(session)
    obs = series[item.id]
    assert obs[date(2026, 4, 1)] == 10678.0          # from master
    assert obs[date(2026, 5, 1)] == 0.0               # from one_row — must not be missing
    assert obs[date(2026, 6, 1)] == 0.0
    assert obs[date(2026, 7, 1)] == 0.0


def test_series_by_item_master_wins_only_for_months_it_actually_covers(session):
    item = Item(canonical_name="Mixed", kind="part")
    session.add(item)
    session.flush()
    snap = _snap(session, datetime(2026, 6, 1))
    month = date(2026, 5, 1)
    session.add(PlanValue(snapshot_id=snap.id, item_id=item.id,
                          month=month, level="master", value=100.0))
    session.add(PlanValue(snapshot_id=snap.id, item_id=item.id,
                          month=month, level="one_row", value=999.0))
    other_month = date(2026, 6, 1)
    session.add(PlanValue(snapshot_id=snap.id, item_id=item.id,
                          month=other_month, level="one_row", value=42.0))
    session.commit()

    series = _series_by_item(session)
    obs = series[item.id]
    assert obs[month] == 100.0            # master wins where it exists
    assert obs[other_month] == 42.0       # one_row fills the month master lacks
