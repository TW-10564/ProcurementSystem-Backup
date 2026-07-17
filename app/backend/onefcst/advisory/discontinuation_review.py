"""Confirm/reject workflow for draft discontinuation suggestions.

Mirrors advisory/rules.py's CompatRule confirm/reject pattern (BR-010:
system suggests, human confirms) for the SAME underlying signal purchasing's
gray "won't reorder" cell-color marking gives — see ingestion/no_reorder_flag.py
and registry/build.py step 7.

Policy (set 2026-07-17, replacing the same day's earlier auto-promote
behavior): a SKU gray-marked for the FIRST time in a build only gets a
draft Suggestion(kind="discontinuation") — Item.eol_flag is NOT touched
until a human confirms it via confirm_suggestion(). The 15 SKUs that were
already gray-marked as of 2026-07-17 were reviewed and directly promoted
to eol_flag=True in that session (a one-time backfill, not this ongoing
flow) — see project memory. Every NEW gray detection from here on goes
through this queue instead."""
from __future__ import annotations

import json
from datetime import datetime

from ..models import Suggestion, Item, AuditLog

KIND = "discontinuation"


class SuggestionNotFound(Exception):
    pass


class SuggestionNotEditable(Exception):
    pass


def _log(session, who: str, what: str, before, after) -> None:
    session.add(AuditLog(who=who, what=what,
                         before=json.dumps(before, default=str) if before is not None else None,
                         after=json.dumps(after, default=str) if after is not None else None))


def create_draft(session, item: Item, category_file: str | None = None) -> Suggestion:
    """One draft per item — caller (registry/build.py) must check no
    existing draft/confirmed suggestion already covers this item before
    calling, so a re-detected gray cell across successive builds doesn't
    pile up duplicate review rows."""
    payload = {"item_id": item.id, "name": item.canonical_name,
              "category": item.category, "source_category_file": category_file,
              "detected_at": datetime.utcnow().isoformat()}
    s = Suggestion(kind=KIND, payload=json.dumps(payload, ensure_ascii=False),
                   confidence=None, status="draft")
    session.add(s)
    return s


def list_suggestions(session, status: str | None = None, limit: int = 500) -> list[Suggestion]:
    q = session.query(Suggestion).filter(Suggestion.kind == KIND)
    if status:
        q = q.filter(Suggestion.status == status)
    return q.order_by(Suggestion.created_at.desc()).limit(limit).all()


def _item_id_of(s: Suggestion) -> int | None:
    try:
        return json.loads(s.payload).get("item_id")
    except (TypeError, ValueError):
        return None


def confirm_suggestion(session, suggestion_id: int, author: str) -> Suggestion:
    """Human confirms the SKU really is discontinued: sets Item.eol_flag=True
    (the ONLY thing forecasting.py/tier2_forecasting.py check to exclude an
    item from the next forecast run — takes effect on the next regenerate,
    not retroactively on the current run)."""
    s = session.get(Suggestion, suggestion_id)
    if s is None or s.kind != KIND:
        raise SuggestionNotFound(f"discontinuation suggestion {suggestion_id} not found")
    if s.status != "draft":
        raise SuggestionNotEditable(
            f"suggestion {suggestion_id} is '{s.status}', not 'draft'")
    item_id = _item_id_of(s)
    item = session.get(Item, item_id) if item_id else None
    before = {"status": s.status, "item_eol_flag": item.eol_flag if item else None}
    s.status = "confirmed"
    s.reviewed_by = author
    s.reviewed_at = datetime.utcnow()
    if item is not None:
        item.eol_flag = True
    _log(session, author, f"discontinuation_suggestion#{suggestion_id} confirmed "
        f"(item#{item_id})", before, {"status": "confirmed", "item_eol_flag": True})
    session.commit()
    return s


def reject_suggestion(session, suggestion_id: int, author: str, reason: str = "") -> Suggestion:
    """Human says this SKU is NOT actually discontinued (e.g. the gray
    marking was for an unrelated reason, or purchasing changed their mind).
    Item.eol_flag is left untouched — it was never set for a draft."""
    s = session.get(Suggestion, suggestion_id)
    if s is None or s.kind != KIND:
        raise SuggestionNotFound(f"discontinuation suggestion {suggestion_id} not found")
    if s.status != "draft":
        raise SuggestionNotEditable(
            f"suggestion {suggestion_id} is '{s.status}', not 'draft'")
    before = {"status": s.status}
    s.status = "rejected"
    s.reviewed_by = author
    s.reviewed_at = datetime.utcnow()
    _log(session, author, f"discontinuation_suggestion#{suggestion_id} rejected: {reason}",
        before, {"status": "rejected", "reason": reason})
    session.commit()
    return s
