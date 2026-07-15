"""Regression tests for FIX_PLAN H1 (NULL master value crashes the mismatch
gate) and H2 (the acceptance test could only ever print PASS, or crash)."""
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from onefcst.models import Base, CompatRule, Item, PlanValue, Snapshot
from onefcst.verify.mismatch import compute_mismatch, acceptance_test


@pytest.fixture()
def session(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    # avoid touching the real NAS take-or-pay workbook — irrelevant to the
    # NULL-guard behavior under test
    monkeypatch.setattr("onefcst.verify.mismatch.parse_mb_takeorpay", lambda one_dir: [])
    yield s
    s.close()


def test_compute_mismatch_does_not_crash_on_null_master_value(session):
    """A classified CPU item with a NULL value in the anchor month used to
    raise TypeError (v[0] unguarded) and abort the whole tab."""
    month = date.today().replace(day=1)
    cpu_null = Item(canonical_name="Ry56003D", category="CPU", kind="part")
    mb = Item(canonical_name="B550M Pro", category="ﾏｻﾞｰﾎﾞｰﾄﾞ", kind="part")
    session.add_all([cpu_null, mb])
    snap = Snapshot(source_file="ONE計画.xlsm")
    session.add(snap)
    session.flush()
    session.add(PlanValue(snapshot_id=snap.id, item_id=cpu_null.id, month=month,
                          level="master", value=None))
    session.add(PlanValue(snapshot_id=snap.id, item_id=mb.id, month=month,
                          level="master", value=50.0))
    session.commit()

    result = compute_mismatch(session)              # must not raise
    am4 = next(g for g in result["groups"] if g["socket"] == "AM4")
    assert am4["cpu_demand"] == 0.0                  # NULL treated as 0, not crashed on
    assert am4["mb_demand"] == 50.0


def test_acceptance_test_fails_cleanly_on_empty_history():
    """Previously this would raise IndexError on hist[0] instead of
    reporting FAIL."""
    result = acceptance_test([])
    assert result["passed"] is False
    assert "FAIL" in result["message"]


def test_acceptance_test_fails_when_gap_is_not_actually_persistent():
    """A single-month blip must not be reported as PASS."""
    hist = [{"month": "2026-01-01", "socket": "AM4", "cpu_demand": 60.0,
            "mb_demand": 200.0, "pct": 233.3}]
    result = acceptance_test(hist)
    assert result["passed"] is False


def test_acceptance_test_passes_on_a_genuinely_persistent_mismatch():
    hist = [{"month": f"2026-{m:02d}-01", "socket": "AM4", "cpu_demand": 60.0,
            "mb_demand": 200.0, "pct": 233.3} for m in range(1, 8)]
    result = acceptance_test(hist)
    assert result["passed"] is True
    assert result["persistent_months"] == 7


def test_confirmed_rule_lets_an_unclassifiable_cpu_count_toward_its_partner_socket(session):
    """An item the deterministic table can't classify (e.g. a genuinely new
    part an LLM drafted a pairing for) must count toward the mismatch gate
    ONCE a human has confirmed that pairing — this is what makes confirming
    a rule in CompatReview.tsx actually change this report."""
    month = date.today().replace(day=1)
    weird_cpu = Item(canonical_name="UNCLASSIFIABLE NEWCHIP", category="CPU", kind="part")
    known_mb = Item(canonical_name="B550M Pro", category="ﾏｻﾞｰﾎﾞｰﾄﾞ", kind="part")  # AM4
    session.add_all([weird_cpu, known_mb])
    snap = Snapshot(source_file="ONE計画.xlsm")
    session.add(snap)
    session.flush()
    session.add(PlanValue(snapshot_id=snap.id, item_id=weird_cpu.id, month=month,
                          level="master", value=30.0))
    session.add(CompatRule(part_a_id=weird_cpu.id, part_b_id=known_mb.id,
                           relation="fits", confidence=0.95, origin="llm",
                           status="confirmed", confirmed_by="tanaka"))
    session.commit()

    result = compute_mismatch(session)
    am4 = next(g for g in result["groups"] if g["socket"] == "AM4")
    assert am4["cpu_demand"] == 30.0             # counted via the confirmed rule
    assert "UNCLASSIFIABLE NEWCHIP" in result["confirmed_rule_classifications"]["cpu"]


def test_draft_rule_never_counts_toward_the_mismatch_gate(session):
    """Same setup as above, but the rule is still a DRAFT — must NOT count.
    This is the exact gap that made confirming feel like it did nothing."""
    month = date.today().replace(day=1)
    weird_cpu = Item(canonical_name="UNCLASSIFIABLE NEWCHIP", category="CPU", kind="part")
    known_mb = Item(canonical_name="B550M Pro", category="ﾏｻﾞｰﾎﾞｰﾄﾞ", kind="part")
    session.add_all([weird_cpu, known_mb])
    snap = Snapshot(source_file="ONE計画.xlsm")
    session.add(snap)
    session.flush()
    session.add(PlanValue(snapshot_id=snap.id, item_id=weird_cpu.id, month=month,
                          level="master", value=30.0))
    session.add(CompatRule(part_a_id=weird_cpu.id, part_b_id=known_mb.id,
                           relation="fits", confidence=0.95, origin="llm",
                           status="draft"))
    session.commit()

    result = compute_mismatch(session)
    am4 = next((g for g in result["groups"] if g["socket"] == "AM4"), None)
    assert am4 is None or am4["cpu_demand"] == 0.0
    assert result["confirmed_rule_classifications"]["cpu"] == []


def test_conflicting_confirmed_rules_stay_unclassified_not_guessed(session):
    """Two confirmed rules for the same item implying DIFFERENT socket
    families — must not pick one arbitrarily; stays unclassified, same
    'don't substitute a plausible answer' principle as the LLM prompt."""
    month = date.today().replace(day=1)
    weird_cpu = Item(canonical_name="UNCLASSIFIABLE NEWCHIP", category="CPU", kind="part")
    mb_am4 = Item(canonical_name="B550M Pro", category="ﾏｻﾞｰﾎﾞｰﾄﾞ", kind="part")     # AM4
    mb_am5 = Item(canonical_name="B650 Pro", category="ﾏｻﾞｰﾎﾞｰﾄﾞ", kind="part")     # AM5
    session.add_all([weird_cpu, mb_am4, mb_am5])
    snap = Snapshot(source_file="ONE計画.xlsm")
    session.add(snap)
    session.flush()
    session.add(PlanValue(snapshot_id=snap.id, item_id=weird_cpu.id, month=month,
                          level="master", value=30.0))
    session.add(CompatRule(part_a_id=weird_cpu.id, part_b_id=mb_am4.id,
                           relation="fits", confidence=0.9, origin="llm",
                           status="confirmed", confirmed_by="tanaka"))
    session.add(CompatRule(part_a_id=weird_cpu.id, part_b_id=mb_am5.id,
                           relation="fits", confidence=0.9, origin="llm",
                           status="confirmed", confirmed_by="sato"))
    session.commit()

    result = compute_mismatch(session)
    assert "UNCLASSIFIABLE NEWCHIP" not in result["confirmed_rule_classifications"]["cpu"]
    for g in result["groups"]:
        assert g["cpu_demand"] == 0.0            # never counted toward either socket


