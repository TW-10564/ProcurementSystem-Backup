"""Regression tests for master vs sub-file (one_row) source reconciliation
— found 2026-07-08: for 13 CPU/国内一般品 items, master (a chain-formula
rollup) and one_row (raw daily production plan) disagreed for the same
item-month even though both snapshots were captured ~1 second apart. This
module FLAGS disagreement; it never picks a side (master might be a
deliberate planning-meeting revision, not a bug — see module docstring)."""
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from onefcst.models import Base, Item, Snapshot, PlanValue
from onefcst.verify.reconciliation import compute_discrepancies, run


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _snap(session):
    snap = Snapshot(source_file="ONE計画.xlsm", file_mtime=datetime(2026, 6, 17, 10, 40))
    session.add(snap)
    session.flush()
    return snap


def test_flags_a_real_disagreement_between_master_and_one_row(session):
    item = Item(canonical_name="ry77700", category="CPU", kind="part")
    session.add(item)
    session.flush()
    snap = _snap(session)
    month = date(2026, 7, 1)
    session.add(PlanValue(snapshot_id=snap.id, item_id=item.id, month=month,
                          level="master", value=3640.0))
    session.add(PlanValue(snapshot_id=snap.id, item_id=item.id, month=month,
                          level="one_row", value=4876.0))
    session.commit()

    out = compute_discrepancies(session, start=date(2026, 7, 1))
    assert len(out) == 1
    assert out[0]["item_name"] == "ry77700"
    assert out[0]["master"] == 3640.0
    assert out[0]["one_row"] == 4876.0
    assert out[0]["diff"] == pytest.approx(-1236.0)
    assert out[0]["classification"] == "insufficient_data"   # only 1 flagged month


def test_does_not_flag_small_differences_below_threshold(session):
    item = Item(canonical_name="Close Enough", kind="part")
    session.add(item)
    session.flush()
    snap = _snap(session)
    month = date(2026, 7, 1)
    session.add(PlanValue(snapshot_id=snap.id, item_id=item.id, month=month,
                          level="master", value=1000.0))
    session.add(PlanValue(snapshot_id=snap.id, item_id=item.id, month=month,
                          level="one_row", value=950.0))    # 5.3% off — noise, not signal
    session.commit()

    out = compute_discrepancies(session, start=date(2026, 7, 1))
    assert out == []


def test_does_not_flag_when_only_one_source_has_data(session):
    """A month where one_row simply has no coverage (common beyond its
    ~4-month window) is not a disagreement — there's nothing to compare."""
    item = Item(canonical_name="Master Only", kind="part")
    session.add(item)
    session.flush()
    snap = _snap(session)
    session.add(PlanValue(snapshot_id=snap.id, item_id=item.id, month=date(2026, 7, 1),
                          level="master", value=5000.0))
    session.commit()

    out = compute_discrepancies(session, start=date(2026, 7, 1))
    assert out == []


def test_requires_both_relative_and_absolute_thresholds(session):
    """A tiny item swinging 200% on a 2-unit difference isn't worth a
    human's time — both thresholds must be cleared."""
    item = Item(canonical_name="Tiny Item", kind="part")
    session.add(item)
    session.flush()
    snap = _snap(session)
    month = date(2026, 7, 1)
    session.add(PlanValue(snapshot_id=snap.id, item_id=item.id, month=month,
                          level="master", value=3.0))
    session.add(PlanValue(snapshot_id=snap.id, item_id=item.id, month=month,
                          level="one_row", value=1.0))       # 200% relative, but tiny absolute
    session.commit()

    out = compute_discrepancies(session, start=date(2026, 7, 1),
                                min_relative_pct=15.0, min_absolute=5.0)
    assert out == []


def _add(session, snap, item, month, master, one_row):
    session.add(PlanValue(snapshot_id=snap.id, item_id=item.id, month=month,
                          level="master", value=master))
    session.add(PlanValue(snapshot_id=snap.id, item_id=item.id, month=month,
                          level="one_row", value=one_row))


def test_classifies_a_frozen_constant_ratio_as_likely_formula_bug(session):
    """Direct reproduction of the D2T finding: the master/one_row ratio
    holds at the same value (here 0.8) across every flagged month —
    the signature of one frozen coefficient, not organic drift."""
    item = Item(canonical_name="D2T", category="HDD(内蔵)", kind="part")
    session.add(item)
    session.flush()
    snap = _snap(session)
    for i, one_row in enumerate([620.0, 642.0, 545.0, 534.0]):
        _add(session, snap, item, date(2026, 6 + i, 1), one_row * 0.8, one_row)
    session.commit()

    out = compute_discrepancies(session, start=date(2026, 6, 1), months=4)
    assert len(out) == 4
    assert all(r["classification"] == "likely_formula_bug" for r in out)
    assert all(r["ratio_cv"] < 0.01 for r in out)


def test_classifies_a_drifting_ratio_as_methodology_disagreement(session):
    """Direct reproduction of the ry77700 finding: the ratio moves
    meaningfully — and gets worse — each month, consistent with two
    independently-maintained forecasts diverging rather than one bug."""
    item = Item(canonical_name="ry77700", category="CPU", kind="part")
    session.add(item)
    session.flush()
    snap = _snap(session)
    ratios = [0.83, 0.75, 0.62, 0.71]
    for i, (one_row, ratio) in enumerate(zip([3821.0, 4876.0, 3838.0, 3548.0], ratios)):
        _add(session, snap, item, date(2026, 6 + i, 1), one_row * ratio, one_row)
    session.commit()

    out = compute_discrepancies(session, start=date(2026, 6, 1), months=4)
    assert len(out) == 4
    assert all(r["classification"] == "methodology_disagreement" for r in out)
    assert all(r["ratio_cv"] >= 0.05 for r in out)


def test_run_reports_classification_breakdown(session):
    bug_item = Item(canonical_name="D2T", category="HDD(内蔵)", kind="part")
    drift_item = Item(canonical_name="ry77700", category="CPU", kind="part")
    session.add_all([bug_item, drift_item])
    session.flush()
    snap = _snap(session)
    for i, one_row in enumerate([620.0, 642.0, 545.0]):
        _add(session, snap, bug_item, date(2026, 6 + i, 1), one_row * 0.8, one_row)
    for i, (one_row, ratio) in enumerate(zip([3821.0, 4876.0, 3838.0], [0.83, 0.6, 0.71])):
        _add(session, snap, drift_item, date(2026, 6 + i, 1), one_row * ratio, one_row)
    session.commit()

    result = run(session, start=date(2026, 6, 1))
    assert result["by_classification"]["likely_formula_bug"]["affected_items"] == 1
    assert result["by_classification"]["methodology_disagreement"]["affected_items"] == 1
    assert result["by_classification"]["likely_formula_bug"]["count"] == 3
    assert result["by_classification"]["methodology_disagreement"]["count"] == 3
