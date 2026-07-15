"""Tests for the LLM compat-rule drafting pipeline — fake transport, no
network. Confidence is the model's raw 0.0-1.0 self-report, stored as-is
(no ceiling — see llm_rules.py's module docstring for the tradeoff this
was an explicit, informed choice about). The remaining guardrails ARE the
feature: invented families rejected, human decisions never overwritten."""
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
        "NEWCHIP 9X": {"family": "LGA1851", "confidence": 0.93,
                       "reasoning": "next-gen Arrow Lake successor"},
        "FUTUREBOARD Z": {"family": "unknown", "confidence": 0.1,
                          "reasoning": "no recognizable chipset"},
    })
    result = llm_rules.draft_llm_rules(session, transport)
    assert result["ok"] is True
    assert result["rules_drafted"] == 1
    rule = session.query(CompatRule).one()
    assert (rule.part_a_id, rule.part_b_id) == (new_cpu.id, known_mb.id)
    assert rule.origin == "llm" and rule.status == "draft"
    assert rule.confidence == 0.93          # stored exactly as reported
    assert "Arrow Lake" in rule.note
    # the unknown-family board went to review, not into a rule
    assert result["needs_review"] == 1


def test_high_confidence_score_is_stored_uncapped(session):
    """The explicit, informed design choice: a real 0-1 score, no ceiling —
    unlike the old high/medium/low mapping, nothing here caps this below
    the bulk-confirm threshold (0.9)."""
    seed(session)
    transport = make_transport({
        "NEWCHIP 9X": {"family": "LGA1851", "confidence": 0.99,
                       "reasoning": "extremely confident"},
        "FUTUREBOARD Z": {"family": "unknown", "confidence": 0.0, "reasoning": ""},
    })
    result = llm_rules.draft_llm_rules(session, transport)
    rule = session.query(CompatRule).one()
    assert rule.confidence == 0.99
    assert rule.confidence > 0.9            # would now qualify for bulk-confirm


def test_invented_family_is_rejected_not_stored(session):
    seed(session)
    transport = make_transport({
        "NEWCHIP 9X": {"family": "LGA9999", "confidence": 0.9,
                       "reasoning": "made up"},
        "FUTUREBOARD Z": {"family": "unknown", "confidence": 0.1, "reasoning": ""},
    })
    result = llm_rules.draft_llm_rules(session, transport)
    assert result["rules_drafted"] == 0
    assert result["needs_review"] == 2                    # both routed to review
    assert session.query(CompatRule).count() == 0


def test_rerun_replaces_drafts_but_never_confirmed_rules(session):
    known_mb, _, new_cpu, _ = seed(session)
    # a human already CONFIRMED an LLM rule for this pair
    session.add(CompatRule(part_a_id=new_cpu.id, part_b_id=known_mb.id,
                           relation="fits", confidence=0.65, origin="llm",
                           status="confirmed", confirmed_by="tanaka",
                           confirmed_at=datetime(2026, 7, 1)))
    session.commit()
    transport = make_transport({
        "NEWCHIP 9X": {"family": "LGA1851", "confidence": 0.9, "reasoning": "x"},
        "FUTUREBOARD Z": {"family": "unknown", "confidence": 0.1, "reasoning": ""},
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


# ---------------------------------------------------------------- edge cases ---

def test_hallucinated_name_not_in_batch_is_ignored(session):
    """The model answers for a name it was never asked about — must not be
    stored under any real item (there may not even BE a matching Item row)."""
    seed(session)
    def transport(payload):
        return json.dumps([
            {"name": "NEWCHIP 9X", "family": "LGA1851", "confidence": 0.9,
             "reasoning": "real answer"},
            {"name": "TOTALLY MADE UP MODEL", "family": "AM5",
             "confidence": 0.9, "reasoning": "hallucinated, not in batch"},
        ])
    result = llm_rules.draft_llm_rules(session, transport)
    assert result["rules_drafted"] == 1                    # only the real one
    assert all(r.note is None or "MADE UP" not in r.note
              for r in session.query(CompatRule).all())


def test_duplicate_name_in_response_only_counted_once(session):
    seed(session)
    def transport(payload):
        return json.dumps([
            {"name": "NEWCHIP 9X", "family": "LGA1851", "confidence": 0.9,
             "reasoning": "first answer"},
            {"name": "NEWCHIP 9X", "family": "AM5",
             "confidence": 0.9, "reasoning": "duplicate, conflicting answer"},
        ])
    result = llm_rules.draft_llm_rules(session, transport)
    assert result["classified"] == 1                       # deduped, not 2
    notes = [r.note for r in session.query(CompatRule).all()]
    assert all("first answer" in n for n in notes)          # first one wins
    assert not any("duplicate" in n for n in notes)


def test_invalid_confidence_value_routes_to_review(session):
    """Model answers with something that isn't a valid 0-1 number (free
    text, or a number outside range) — must not crash, must not be stored."""
    seed(session)
    def transport(payload):
        return json.dumps([{"name": "NEWCHIP 9X", "family": "LGA1851",
                            "confidence": "very sure", "reasoning": "x"}])
    result = llm_rules.draft_llm_rules(session, transport)
    assert result["ok"] is True
    assert result["rules_drafted"] == 0
    assert result["needs_review"] >= 1


def test_out_of_range_confidence_routes_to_review(session):
    seed(session)
    def transport(payload):
        return json.dumps([{"name": "NEWCHIP 9X", "family": "LGA1851",
                            "confidence": 5.0, "reasoning": "x"}])
    result = llm_rules.draft_llm_rules(session, transport)
    assert result["rules_drafted"] == 0
    assert result["needs_review"] >= 1


def test_non_dict_row_in_response_array_is_skipped(session):
    """Model returns a malformed array (e.g. bare strings) alongside a good
    row — the good row must still be processed, the bad ones skipped."""
    seed(session)
    def transport(payload):
        return json.dumps([
            "just a string, not an object",
            {"name": "NEWCHIP 9X", "family": "LGA1851", "confidence": 0.9,
             "reasoning": "valid"},
        ])
    result = llm_rules.draft_llm_rules(session, transport)
    assert result["ok"] is True
    assert result["rules_drafted"] == 1


def test_response_wrapped_in_markdown_code_fence_still_parses(session):
    """Real models often wrap JSON in ```json ... ``` despite instructions
    asking for raw JSON only — the tolerant extractor must handle this."""
    seed(session)
    def transport(payload):
        return ("Here is the classification:\n```json\n" +
               json.dumps([{"name": "NEWCHIP 9X", "family": "LGA1851",
                           "confidence": 0.9, "reasoning": "fenced"}]) +
               "\n```\nLet me know if you need anything else.")
    result = llm_rules.draft_llm_rules(session, transport)
    assert result["ok"] is True
    assert result["rules_drafted"] == 1


def test_batching_splits_large_input_into_multiple_calls(session):
    """More than BATCH_SIZE unclassified items on one side must produce
    multiple transport calls, not one oversized prompt."""
    items = [Item(canonical_name=f"WEIRDCPU{i}", category="CPU", kind="part")
            for i in range(llm_rules.BATCH_SIZE + 5)]
    known_mb = Item(canonical_name="B860M Pro RS", category="ﾏｻﾞｰﾎﾞｰﾄﾞ", kind="part")
    session.add_all(items + [known_mb])
    session.commit()

    calls = []
    def transport(payload):
        calls.append(payload)
        return json.dumps([])          # nothing classified — just count calls
    llm_rules.draft_llm_rules(session, transport)
    assert len(calls) == 2             # BATCH_SIZE+5 items -> 2 batches


def test_empty_unclassified_side_makes_no_calls_for_that_side(session):
    """All MBs are engine-classifiable; only a CPU is unclassified — the MB
    side must not generate a wasted transport call."""
    known_mb = Item(canonical_name="B860M Pro RS", category="ﾏｻﾞｰﾎﾞｰﾄﾞ", kind="part")
    new_cpu = Item(canonical_name="NEWCHIP 9X", category="CPU", kind="part")
    session.add_all([known_mb, new_cpu])
    session.commit()

    calls = []
    def transport(payload):
        calls.append(payload["messages"][0]["content"])
        return json.dumps([{"name": "NEWCHIP 9X", "family": "LGA1851",
                           "confidence": 0.9, "reasoning": "x"}])
    llm_rules.draft_llm_rules(session, transport)
    assert len(calls) == 1
    assert "NEWCHIP 9X" in calls[0]


def test_llm_classified_cpu_and_llm_classified_mb_cross_pair_at_weakest_confidence(session):
    """Both sides of a pair are LLM-classified (not engine-known) — must
    still cross-pair, using the WEAKER (lower) of the two numeric scores."""
    new_cpu = Item(canonical_name="NEWCHIP 9X", category="CPU", kind="part")
    new_mb = Item(canonical_name="FUTUREBOARD Z", category="ﾏｻﾞｰﾎﾞｰﾄﾞ", kind="part")
    session.add_all([new_cpu, new_mb])
    session.commit()
    transport = make_transport({
        "NEWCHIP 9X": {"family": "LGA1851", "confidence": 0.95,
                       "reasoning": "cpu evidence"},
        "FUTUREBOARD Z": {"family": "LGA1851", "confidence": 0.6,
                          "reasoning": "mb evidence"},
    })
    result = llm_rules.draft_llm_rules(session, transport)
    assert result["rules_drafted"] == 1
    rule = session.query(CompatRule).one()
    assert rule.confidence == 0.6           # weaker of the two, taken exactly
    assert "cpu evidence" in rule.note and "mb evidence" in rule.note


def test_no_same_category_pairs_are_ever_created(session):
    """draft_llm_rules must only pair CPU x MB, never CPU x CPU or MB x MB,
    even when both unclassified items land in the same family."""
    new_cpu = Item(canonical_name="NEWCHIP 9X", category="CPU", kind="part")
    known_cpu = Item(canonical_name="U7-265K", category="CPU", kind="part")
    session.add_all([new_cpu, known_cpu])
    session.commit()
    transport = make_transport({
        "NEWCHIP 9X": {"family": "LGA1851", "confidence": 0.9, "reasoning": "x"},
    })
    result = llm_rules.draft_llm_rules(session, transport)
    assert result["rules_drafted"] == 0     # no MB at all -> nothing to pair


def test_known_families_excludes_families_not_in_any_source_table():
    fams = llm_rules.known_families()
    assert "LGA1851" in fams            # real family, present
    assert "made_up_socket" not in fams
    assert len(fams) == len(set(fams))  # no duplicates
