"""Tests for the LLM compat-rule drafting pipeline — fake transport, no
network. The guardrails ARE the feature: invented families rejected,
confidence capped, human decisions never overwritten."""
import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from onefcst.advisory import llm_rules
from onefcst.models import Base, CompatRule, Item


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def make_transport(rows_by_name: dict):
    """Answers every batch with canned rows for the names it was asked."""
    def transport(payload):
        prompt = payload["messages"][0]["content"]
        rows = [rows_by_name[n] | {"name": n}
                for n in rows_by_name if f"- {n}" in prompt]
        return json.dumps(rows)
    return transport


def seed(session):
    # engine-classifiable LGA1851 board + CPU, plus unclassifiable newcomers
    known_mb = Item(canonical_name="B860M Pro RS", category="ﾏｻﾞｰﾎﾞｰﾄﾞ", kind="part")
    known_cpu = Item(canonical_name="U7-265K", category="CPU", kind="part")
    new_cpu = Item(canonical_name="NEWCHIP 9X", category="CPU", kind="part")
    new_mb = Item(canonical_name="FUTUREBOARD Z", category="ﾏｻﾞｰﾎﾞｰﾄﾞ", kind="part")
    session.add_all([known_mb, known_cpu, new_cpu, new_mb])
    session.commit()
    return known_mb, known_cpu, new_cpu, new_mb


def test_llm_draft_pairs_new_cpu_with_known_family_boards(session):
    known_mb, _, new_cpu, new_mb = seed(session)
    transport = make_transport({
        "NEWCHIP 9X": {"family": "LGA1851", "confidence": "high",
                       "reasoning": "next-gen Arrow Lake successor"},
        "FUTUREBOARD Z": {"family": "unknown", "confidence": "low",
                          "reasoning": "no recognizable chipset"},
    })
    result = llm_rules.draft_llm_rules(session, transport)
    assert result["ok"] is True
    assert result["rules_drafted"] == 1
    rule = session.query(CompatRule).one()
    assert (rule.part_a_id, rule.part_b_id) == (new_cpu.id, known_mb.id)
    assert rule.origin == "llm" and rule.status == "draft"
    assert rule.confidence == llm_rules.LLM_CONF_CAP      # capped, < 0.7
    assert "Arrow Lake" in rule.note
    # the unknown board went to review, not into a rule
    assert result["needs_review"] == 1


def test_invented_family_is_rejected_not_stored(session):
    seed(session)
    transport = make_transport({
        "NEWCHIP 9X": {"family": "LGA9999", "confidence": "high",
                       "reasoning": "made up"},
        "FUTUREBOARD Z": {"family": "unknown", "confidence": "low", "reasoning": ""},
    })
    result = llm_rules.draft_llm_rules(session, transport)
    assert result["rules_drafted"] == 0
    assert result["needs_review"] == 2                    # both routed to review
    assert session.query(CompatRule).count() == 0


def test_confidence_never_reaches_bulk_confirm_threshold():
    assert max(llm_rules.CONF_MAP.values()) < 0.7 < 0.9


def test_rerun_replaces_drafts_but_never_confirmed_rules(session):
    known_mb, _, new_cpu, _ = seed(session)
    # a human already CONFIRMED an LLM rule for this pair
    session.add(CompatRule(part_a_id=new_cpu.id, part_b_id=known_mb.id,
                           relation="fits", confidence=0.65, origin="llm",
                           status="confirmed", confirmed_by="tanaka",
                           confirmed_at=datetime(2026, 7, 1)))
    session.commit()
    transport = make_transport({
        "NEWCHIP 9X": {"family": "LGA1851", "confidence": "high", "reasoning": "x"},
        "FUTUREBOARD Z": {"family": "unknown", "confidence": "low", "reasoning": ""},
    })
    result = llm_rules.draft_llm_rules(session, transport)
    # pair already exists as a confirmed rule → skipped, not duplicated/reset
    assert result["rules_drafted"] == 0
    assert result["skipped_existing"] == 1
    rule = session.query(CompatRule).one()
    assert rule.status == "confirmed" and rule.confirmed_by == "tanaka"


def test_unparseable_response_degrades_to_review(session):
    seed(session)
    result = llm_rules.draft_llm_rules(session, lambda p: "sorry, no JSON here")
    assert result["ok"] is True                            # no crash
    assert result["rules_drafted"] == 0
    assert result["needs_review"] == 2


def test_unreachable_server_reports_error(session):
    seed(session)
    def dead(payload):
        raise OSError("connection refused")
    result = llm_rules.draft_llm_rules(session, dead)
    assert result["ok"] is False
    assert "unreachable" in result["error"]
    assert session.query(CompatRule).count() == 0
