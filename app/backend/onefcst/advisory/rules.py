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

from ..models import CompatRule, AuditLog


class RuleNotFound(Exception):
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


def list_rules(session, status: str | None = None, min_confidence: float | None = None,
               limit: int = 200) -> list[CompatRule]:
    q = session.query(CompatRule)
    if status:
        q = q.filter(CompatRule.status == status)
    if min_confidence is not None:
        q = q.filter(CompatRule.confidence >= min_confidence)
    return q.order_by(CompatRule.confidence.desc()).limit(limit).all()


def bulk_confirm_high_confidence(session, author: str, threshold: float = 0.9) -> int:
    """Convenience for the review queue's fast path: confirm every draft rule
    at/above `threshold` in one action, still fully logged (one audit entry
    per rule — no silent bulk state changes)."""
    rules = list_rules(session, status="draft", min_confidence=threshold)
    for r in rules:
        confirm_rule(session, r.id, author)
    return len(rules)
