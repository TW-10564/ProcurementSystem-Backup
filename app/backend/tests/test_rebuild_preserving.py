"""Regression test for PILOT_DEPLOY_PLAN blocker #1 — build.run()'s full
drop+rebuild must not silently erase human-entered data. Exercises
rebuild_preserving._snapshot/_restore directly against an in-memory DB,
simulating a rebuild by dropping and re-inserting Items with the same
canonical_name but new (shuffled) ids — exactly what a real
build.run() does."""
from datetime import date
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from onefcst.models import (Base, Item, ForecastRun, Forecast, Adjustment,
                            AccuracyScore, PlannerSnapshot, CompatRule, AuditLog, Actual,
                            WeeklyActual)
from onefcst.rebuild_preserving import _snapshot, _restore


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _simulate_rebuild(session, insert_order):
    """Mimic build.run()'s Base.metadata.drop_all/create_all: wipe every
    table, then re-insert items in `insert_order` under brand new ids —
    callers pass an order different from original creation order so the
    old-id -> new-id mapping is deliberately NOT the identity, proving
    translation (not just luck) is what makes restore work."""
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(table.delete())
    session.commit()
    for name in insert_order:
        session.add(Item(canonical_name=name, kind="part"))
    session.commit()


def test_adjustment_and_compat_rule_and_audit_log_survive_a_rebuild(session):
    cpu = Item(canonical_name="CPU_A", kind="part")
    mb = Item(canonical_name="MB_A", kind="part")
    session.add_all([cpu, mb])
    session.flush()

    run = ForecastRun(method_version="v1", note="original")
    session.add(run)
    session.flush()

    session.add(Forecast(run_id=run.id, item_id=cpu.id, month=date(2026, 8, 1), baseline=10.0, method="seasonal"))
    session.add(Adjustment(run_id=run.id, item_id=cpu.id, month=date(2026, 8, 1),
                           old_value=10.0, new_value=15.0, scope="sku",
                           reason="promo", author="alice"))
    session.add(AccuracyScore(run_id=run.id, item_id=cpu.id, category="CPU",
                              month=date(2026, 8, 1), level="sku", mape=5.0, wape=4.0,
                              wape_m1=3.0, bias=0.1))
    session.add(PlannerSnapshot(item_id=cpu.id, month=date(2026, 8, 1), value=12.0,
                                snapshot_date=date(2026, 7, 1)))
    session.add(CompatRule(part_a_id=cpu.id, part_b_id=mb.id, relation="fits",
                           confidence=0.95, origin="human", status="confirmed",
                           confirmed_by="bob"))
    session.add(AuditLog(who="alice", what="adjustment#1 recorded (sku)",
                         before="{}", after="{}"))
    session.commit()

    snap = _snapshot(session)

    _simulate_rebuild(session, ["MB_A", "CPU_A"])  # reversed insert order -> new ids swapped

    dropped, superseded = _restore(session, snap)

    assert dropped == {"forecasts": 0, "adjustments": 0, "scores": 0,
                       "snapshots": 0, "compat_rules": 0, "actuals": 0,
                       "weekly_actuals": 0}
    assert superseded == {"actuals": 0, "weekly_actuals": 0}

    new_cpu = session.query(Item).filter_by(canonical_name="CPU_A").one()
    new_mb = session.query(Item).filter_by(canonical_name="MB_A").one()
    assert new_cpu.id != cpu.id  # ids genuinely changed, proving translation was needed

    adj = session.query(Adjustment).one()
    assert adj.item_id == new_cpu.id
    assert adj.new_value == 15.0
    assert adj.reason == "promo"

    rule = session.query(CompatRule).one()
    assert rule.part_a_id == new_cpu.id
    assert rule.part_b_id == new_mb.id
    assert rule.status == "confirmed"

    log = session.query(AuditLog).one()
    assert log.who == "alice"
    assert log.what == "adjustment#1 recorded (sku)"

    score = session.query(AccuracyScore).one()
    assert score.item_id == new_cpu.id

    snapshot_row = session.query(PlannerSnapshot).one()
    assert snapshot_row.item_id == new_cpu.id

    restored_run = session.query(ForecastRun).one()
    assert restored_run.note == "original"
    assert restored_run.created_at == run.created_at


def test_rows_for_items_no_longer_present_after_rebuild_are_dropped_not_dangling(session):
    cpu = Item(canonical_name="CPU_A", kind="part")
    discontinued = Item(canonical_name="DISCONTINUED", kind="part")
    session.add_all([cpu, discontinued])
    session.flush()

    session.add(Adjustment(item_id=discontinued.id, month=date(2026, 8, 1),
                           old_value=1.0, new_value=2.0, scope="sku",
                           reason="test", author="alice"))
    session.add(CompatRule(part_a_id=cpu.id, part_b_id=discontinued.id,
                           relation="fits", origin="human", status="confirmed"))
    session.commit()

    snap = _snapshot(session)

    # rebuild drops DISCONTINUED entirely — it's no longer in the source data
    _simulate_rebuild(session, ["CPU_A"])

    dropped, _superseded = _restore(session, snap)

    assert dropped["adjustments"] == 1
    assert dropped["compat_rules"] == 1
    assert session.query(Adjustment).count() == 0
    assert session.query(CompatRule).count() == 0


def test_audit_log_carries_over_verbatim_even_with_no_items_at_all(session):
    session.add(AuditLog(who="system", what="note recorded", before=None, after=None))
    session.commit()

    snap = _snapshot(session)
    _simulate_rebuild(session, [])
    _restore(session, snap)

    log = session.query(AuditLog).one()
    assert log.who == "system"
    assert log.what == "note recorded"


# --------------------------------------------------------------- Actual ---

def test_actual_sales_history_survives_a_rebuild(session):
    item = Item(canonical_name="CPU_A", kind="part")
    session.add(item)
    session.flush()
    session.add(Actual(item_id=item.id, month=date(2026, 1, 1), qty=50.0, source="SAP"))
    session.commit()

    snap = _snapshot(session)
    _simulate_rebuild(session, ["CPU_A"])
    dropped, superseded = _restore(session, snap)

    assert dropped["actuals"] == 0
    assert superseded["actuals"] == 0
    new_item = session.query(Item).filter_by(canonical_name="CPU_A").one()
    row = session.query(Actual).one()
    assert row.item_id == new_item.id
    assert row.qty == 50.0
    assert row.source == "SAP"


def test_actual_row_for_a_removed_item_is_dropped_not_dangling(session):
    gone = Item(canonical_name="DISCONTINUED", kind="part")
    session.add(gone)
    session.flush()
    session.add(Actual(item_id=gone.id, month=date(2026, 1, 1), qty=10.0, source="SAP"))
    session.commit()

    snap = _snapshot(session)
    _simulate_rebuild(session, [])
    dropped, _superseded = _restore(session, snap)

    assert dropped["actuals"] == 1
    assert session.query(Actual).count() == 0


def test_fresh_ingest_wins_over_preserved_actual_for_the_same_item_month_source(session):
    """build.run() (which runs between _snapshot and _restore in the real
    run() flow) re-ingests the current rolling SAP-shipment window itself —
    a preserved row for the same (item, month, source) must be skipped,
    not inserted, or it would violate Actual's unique constraint / silently
    duplicate. Simulated here by inserting a "fresh" row directly between
    the rebuild and the restore call, exactly where build.run() would."""
    item = Item(canonical_name="CPU_A", kind="part")
    session.add(item)
    session.flush()
    session.add(Actual(item_id=item.id, month=date(2026, 6, 1), qty=100.0, source="SAP"))
    session.commit()

    snap = _snapshot(session)
    _simulate_rebuild(session, ["CPU_A"])

    # simulates build.run()'s own fresh SAP-shipments write, with a
    # corrected/updated qty for the same real-world week
    fresh_item = session.query(Item).filter_by(canonical_name="CPU_A").one()
    session.add(Actual(item_id=fresh_item.id, month=date(2026, 6, 1), qty=120.0, source="SAP"))
    session.commit()

    dropped, superseded = _restore(session, snap)

    assert dropped["actuals"] == 0
    assert superseded["actuals"] == 1
    rows = session.query(Actual).all()
    assert len(rows) == 1               # not duplicated
    assert rows[0].qty == 120.0         # the fresh value wins, not the stale preserved one


def test_actual_rows_for_different_months_both_survive_alongside_a_fresh_one(session):
    item = Item(canonical_name="CPU_A", kind="part")
    session.add(item)
    session.flush()
    session.add(Actual(item_id=item.id, month=date(2026, 1, 1), qty=10.0, source="SAP"))
    session.commit()

    snap = _snapshot(session)
    _simulate_rebuild(session, ["CPU_A"])
    fresh_item = session.query(Item).filter_by(canonical_name="CPU_A").one()
    session.add(Actual(item_id=fresh_item.id, month=date(2026, 6, 1), qty=99.0, source="SAP"))
    session.commit()

    dropped, superseded = _restore(session, snap)

    assert dropped["actuals"] == 0
    assert superseded["actuals"] == 0
    months = {(r.month, r.qty) for r in session.query(Actual).all()}
    assert months == {(date(2026, 1, 1), 10.0), (date(2026, 6, 1), 99.0)}


# --------------------------------------------------------- WeeklyActual ---

def test_weekly_actual_survives_a_rebuild(session):
    item = Item(canonical_name="CPU_A", kind="part")
    session.add(item)
    session.flush()
    session.add(WeeklyActual(item_id=item.id, week_start=date(2026, 6, 2),
                             qty=13.0, source="SAP"))
    session.commit()

    snap = _snapshot(session)
    _simulate_rebuild(session, ["CPU_A"])
    dropped, superseded = _restore(session, snap)

    assert dropped["weekly_actuals"] == 0
    assert superseded["weekly_actuals"] == 0
    new_item = session.query(Item).filter_by(canonical_name="CPU_A").one()
    row = session.query(WeeklyActual).one()
    assert row.item_id == new_item.id
    assert row.qty == 13.0


def test_weekly_actual_for_a_removed_item_is_dropped_not_dangling(session):
    gone = Item(canonical_name="DISCONTINUED", kind="part")
    session.add(gone)
    session.flush()
    session.add(WeeklyActual(item_id=gone.id, week_start=date(2026, 6, 2),
                             qty=13.0, source="SAP"))
    session.commit()

    snap = _snapshot(session)
    _simulate_rebuild(session, [])
    dropped, _superseded = _restore(session, snap)

    assert dropped["weekly_actuals"] == 1
    assert session.query(WeeklyActual).count() == 0


def test_fresh_weekly_ingest_wins_over_preserved_row_for_the_same_key(session):
    item = Item(canonical_name="CPU_A", kind="part")
    session.add(item)
    session.flush()
    session.add(WeeklyActual(item_id=item.id, week_start=date(2026, 6, 2),
                             qty=13.0, source="SAP"))
    session.commit()

    snap = _snapshot(session)
    _simulate_rebuild(session, ["CPU_A"])
    fresh_item = session.query(Item).filter_by(canonical_name="CPU_A").one()
    session.add(WeeklyActual(item_id=fresh_item.id, week_start=date(2026, 6, 2),
                             qty=15.0, source="SAP"))   # corrected figure
    session.commit()

    dropped, superseded = _restore(session, snap)

    assert dropped["weekly_actuals"] == 0
    assert superseded["weekly_actuals"] == 1
    rows = session.query(WeeklyActual).all()
    assert len(rows) == 1
    assert rows[0].qty == 15.0
