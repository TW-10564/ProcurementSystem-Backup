"""Confirm/reject workflow for draft compatibility rules (M3 output).

Per BR-010 (AI suggests, human confirms): a rule only affects the mismatch
gate's PRODUCTION calculation once status='confirmed'. Confirmation is open
to any authorised planning or purchasing staff (per meeting 2026-07-06 —
role-based routing is a later phase), and every action is written to
AuditLog with who/what/before/after/when.
"""
from __future__ import annotations

import json
from datetime import datetime

from ..models import CompatRule, AuditLog, Item
from ..engine.compat import draft_cpu_mb_rules, unclassified_report


class RuleNotFound(Exception):
    pass


class RuleNotEditable(Exception):
    """Raised when editing a rule that's already confirmed/rejected — those
    are locked decisions (see confirm_rule/reject_rule's audit trail); to
    change a settled decision, reject it and record a new rule instead of
    silently rewriting history."""
    pass


def _log(session, who: str, what: str, before, after) -> None:
    session.add(AuditLog(who=who, what=what,
                         before=json.dumps(before, default=str) if before is not None else None,
                         after=json.dumps(after, default=str) if after is not None else None))


def confirm_rule(session, rule_id: int, author: str) -> CompatRule:
    rule = session.get(CompatRule, rule_id)
    if rule is None:
        raise RuleNotFound(f"compat_rule {rule_id} not found")
    before = {"status": rule.status}
    rule.status = "confirmed"
    rule.confirmed_by = author
    rule.confirmed_at = datetime.utcnow()
    _log(session, author, f"compat_rule#{rule_id} confirmed", before,
        {"status": "confirmed", "confirmed_by": author})
    session.commit()
    return rule


def reject_rule(session, rule_id: int, author: str, reason: str = "") -> CompatRule:
    rule = session.get(CompatRule, rule_id)
    if rule is None:
        raise RuleNotFound(f"compat_rule {rule_id} not found")
    before = {"status": rule.status}
    rule.status = "rejected"
    rule.confirmed_by = author
    rule.confirmed_at = datetime.utcnow()
    _log(session, author, f"compat_rule#{rule_id} rejected: {reason}", before,
        {"status": "rejected", "confirmed_by": author, "reason": reason})
    session.commit()
    return rule


def edit_rule(session, rule_id: int, author: str, relation: str | None = None,
             confidence: float | None = None, note: str | None = None) -> CompatRule:
    """Human correction of a draft rule's own judgement (e.g. the AI/engine
    guessed the wrong confidence, or a reviewer wants to record their own
    reasoning) BEFORE deciding confirm/reject — not a replacement for
    confirm/reject, a correction to what's being decided on. Only allowed
    on status='draft' rules: editing an already-confirmed/rejected rule
    would silently rewrite a recorded decision (BR-010's audit trail exists
    specifically so a decision, once made, is traceable) — reject it and
    create a fresh rule instead if a settled decision needs to change.
    Preserves the ORIGINAL model/engine reasoning in the note (prefixed,
    not overwritten) so a reviewer can still see what the AI actually said
    versus what a human corrected it to."""
    rule = session.get(CompatRule, rule_id)
    if rule is None:
        raise RuleNotFound(f"compat_rule {rule_id} not found")
    if rule.status != "draft":
        raise RuleNotEditable(
            f"compat_rule {rule_id} is '{rule.status}', not 'draft' — "
            "reject it and record a new rule instead of editing a decision")
    before = {"relation": rule.relation, "confidence": rule.confidence,
             "note": rule.note}
    if relation is not None:
        rule.relation = relation
    if confidence is not None:
        rule.confidence = confidence
    if note is not None:
        original = rule.note or ""
        rule.note = (f"[edited by {author}] {note}"
                    + (f" | original: {original}" if original else ""))
    after = {"relation": rule.relation, "confidence": rule.confidence,
            "note": rule.note}
    _log(session, author, f"compat_rule#{rule_id} edited", before, after)
    session.commit()
    return rule


def draft_spec_heuristic_rules(session) -> dict:
    """Deterministic spec-heuristic compat-rule drafter (engine/compat.py's
    rule table) — the non-LLM counterpart to draft_llm_rules (llm_rules.py),
    same origin='ai_spec_heuristic'-scoped clear-then-insert pattern.

    Moved here from verify/mismatch.py (2026-07-14) so every CompatRule
    draft-write lives in advisory/, this project's one writable layer —
    verify/mismatch.py now only reads the table it used to also write."""
    cpu = session.query(Item).filter(Item.category == "CPU").all()
    mb = session.query(Item).filter(Item.category == "ﾏｻﾞｰﾎﾞｰﾄﾞ").all()
    rules = draft_cpu_mb_rules(cpu, mb)
    unclassified = unclassified_report(cpu, mb)

    # idempotent: clear only THIS drafter's own prior drafts before
    # re-drafting. Must NOT filter by relation alone — that would also wipe
    # llm_rules.draft_llm_rules's origin='llm' drafts, and any already
    # human-confirmed rule of either origin. draft_llm_rules scopes its own
    # clear the same way (origin='llm', status='draft' only) for the same
    # reason.
    session.query(CompatRule).filter(
        CompatRule.relation == "fits",
        CompatRule.origin == "ai_spec_heuristic",
        CompatRule.status == "draft",
    ).delete()
    for r in rules:
        session.add(CompatRule(part_a_id=r["part_a_id"], part_b_id=r["part_b_id"],
                               relation=r["relation"], confidence=r["confidence"],
                               origin=r["origin"], status="draft"))
    session.commit()
    return {"cpu_total": len(cpu), "mb_total": len(mb), "rules_drafted": len(rules),
           "cpu_unclassified": len(unclassified["cpu_unclassified"]),
           "mb_unclassified": len(unclassified["mb_unclassified"]),
           "unclassified_detail": unclassified}


def list_rules(session, status: str | None = None, min_confidence: float | None = None,
               limit: int = 200, exclude_origin: str | None = None) -> list[CompatRule]:
    q = session.query(CompatRule)
    if status:
        q = q.filter(CompatRule.status == status)
    if min_confidence is not None:
        q = q.filter(CompatRule.confidence >= min_confidence)
    if exclude_origin is not None:
        q = q.filter(CompatRule.origin != exclude_origin)
    # nulls_last(): confidence is nullable, and SQLite puts NULLs last on a
    # DESC sort while Postgres puts them FIRST — which would float unscored
    # draft rules to the top of the human review queue ahead of the
    # high-confidence ones this ordering exists to surface.
    return (q.order_by(CompatRule.confidence.desc().nulls_last())
            .limit(limit).all())


def bulk_confirm_high_confidence(session, author: str, threshold: float = 0.9) -> int:
    """Convenience for the review queue's fast path: confirm every draft rule
    at/above `threshold` in one action, still fully logged (one audit entry
    per rule — no silent bulk state changes).

    Excludes origin='llm' entirely (2026-07-17, found live: all 46 existing
    LLM-drafted rules report confidence=1.0 with zero variation, despite the
    prompt explicitly telling the model to use the full range — a model
    calibration failure, not a code bug; see llm_rules.py's own docstring,
    which already documents a past confidently-wrong LLM misclassification.
    A confidence value that never varies gives bulk-confirm nothing to
    threshold on, so every LLM draft was passing regardless of how
    trustworthy the classification actually was. LLM-origin drafts now only
    leave 'draft' status via individual confirm/reject in the review queue,
    where the model's stated reasoning (note field) is visible before
    deciding — the deterministic ai_spec_heuristic path (confidence capped
    at 0.95/0.7/0.4, not a raw LLM self-report) is unaffected."""
    rules = list_rules(session, status="draft", min_confidence=threshold,
                       exclude_origin="llm")
    for r in rules:
        confirm_rule(session, r.id, author)
    return len(rules)
