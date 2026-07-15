"""Tests for the compat-rule edit action (advisory/rules.py::edit_rule) —
lets a human correct a draft rule's own judgement before confirm/reject,
but never rewrites an already-settled decision."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from onefcst.advisory import rules as rules_svc
from onefcst.models import AuditLog, Base, CompatRule, Item


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def seed_rule(session, status="draft", **overrides):
    cpu = Item(canonical_name="TESTCPU", category="CPU", kind="part")
    mb = Item(canonical_name="TESTMB", category="ﾏｻﾞｰﾎﾞｰﾄﾞ", kind="part")
    session.add_all([cpu, mb])
    session.commit()
    rule = CompatRule(part_a_id=cpu.id, part_b_id=mb.id, relation="fits",
                      confidence=0.65, origin="llm", status=status,
                      note="original AI reasoning", **overrides)
    session.add(rule)
    session.commit()
    return rule


def test_edit_updates_confidence_and_relation(session):
    rule = seed_rule(session)
    updated = rules_svc.edit_rule(session, rule.id, "tanaka",
                                  relation="requires", confidence=0.4)
    assert updated.relation == "requires"
    assert updated.confidence == 0.4
    assert updated.status == "draft"           # editing doesn't decide it


def test_edit_note_preserves_original_reasoning(session):
    rule = seed_rule(session)
    updated = rules_svc.edit_rule(session, rule.id, "tanaka",
                                  note="actually this is LGA1200, not LGA1151")
    assert "tanaka" in updated.note
    assert "actually this is LGA1200" in updated.note
    assert "original AI reasoning" in updated.note   # not overwritten, kept


def test_edit_only_touches_provided_fields(session):
    rule = seed_rule(session)
    rules_svc.edit_rule(session, rule.id, "tanaka", confidence=0.3)
    session.refresh(rule)
    assert rule.confidence == 0.3
    assert rule.relation == "fits"              # untouched
    assert rule.note == "original AI reasoning"  # untouched


def test_edit_confirmed_rule_raises_not_editable(session):
    rule = seed_rule(session, status="confirmed", confirmed_by="sato")
    with pytest.raises(rules_svc.RuleNotEditable):
        rules_svc.edit_rule(session, rule.id, "tanaka", confidence=0.5)
    session.refresh(rule)
    assert rule.confidence == 0.65              # untouched


def test_edit_rejected_rule_raises_not_editable(session):
    rule = seed_rule(session, status="rejected", confirmed_by="sato")
    with pytest.raises(rules_svc.RuleNotEditable):
        rules_svc.edit_rule(session, rule.id, "tanaka", confidence=0.5)


def test_edit_missing_rule_raises_not_found(session):
    with pytest.raises(rules_svc.RuleNotFound):
        rules_svc.edit_rule(session, 9999, "tanaka", confidence=0.5)


def test_edit_writes_audit_log_entry(session):
    rule = seed_rule(session)
    rules_svc.edit_rule(session, rule.id, "tanaka", confidence=0.4)
    entries = session.query(AuditLog).all()
    assert len(entries) == 1
    assert entries[0].who == "tanaka"
    assert f"compat_rule#{rule.id} edited" in entries[0].what


def test_edited_draft_can_still_be_confirmed_afterward(session):
    rule = seed_rule(session)
    rules_svc.edit_rule(session, rule.id, "tanaka", confidence=0.4)
    confirmed = rules_svc.confirm_rule(session, rule.id, "tanaka")
    assert confirmed.status == "confirmed"
    assert confirmed.confidence == 0.4          # edit stuck through confirm


def test_draft_spec_heuristic_rules_only_clears_its_own_prior_drafts(session):
    """draft_spec_heuristic_rules (moved here from verify/mismatch.py so all
    CompatRule draft-writes live in advisory/) must not wipe llm_rules.py's
    origin='llm' drafts, or any already human-confirmed rule of either
    origin, when the deterministic drafter re-runs after an LLM pass — the
    two drafters must only ever clear their own prior draft rows."""
    # names that don't match any known socket pattern, so
    # draft_cpu_mb_rules drafts nothing for these pairs — isolates the
    # test to the delete-scope bug, not engine/compat.py's classification
    cpu_a = Item(canonical_name="UNCLASSIFIABLE CPU A", category="CPU", kind="part")
    mb_a = Item(canonical_name="UNCLASSIFIABLE MB A", category="ﾏｻﾞｰﾎﾞｰﾄﾞ", kind="part")
    cpu_b = Item(canonical_name="UNCLASSIFIABLE CPU B", category="CPU", kind="part")
    mb_b = Item(canonical_name="UNCLASSIFIABLE MB B", category="ﾏｻﾞｰﾎﾞｰﾄﾞ", kind="part")
    session.add_all([cpu_a, mb_a, cpu_b, mb_b])
    session.commit()

    # a prior LLM draft, and a prior human-confirmed deterministic rule —
    # neither should be touched by re-running the deterministic drafter
    llm_draft = CompatRule(part_a_id=cpu_a.id, part_b_id=mb_a.id,
                           relation="fits", confidence=0.65, origin="llm",
                           status="draft", note="llm reasoning")
    confirmed = CompatRule(part_a_id=cpu_b.id, part_b_id=mb_b.id,
                           relation="fits", confidence=0.95,
                           origin="ai_spec_heuristic", status="confirmed",
                           confirmed_by="tester")
    session.add_all([llm_draft, confirmed])
    session.commit()
    llm_draft_id, confirmed_id = llm_draft.id, confirmed.id

    rules_svc.draft_spec_heuristic_rules(session)

    survivors = {r.id: r for r in session.query(CompatRule).all()}
    assert llm_draft_id in survivors
    assert survivors[llm_draft_id].status == "draft"
    assert survivors[llm_draft_id].origin == "llm"
    assert confirmed_id in survivors
    assert survivors[confirmed_id].status == "confirmed"
