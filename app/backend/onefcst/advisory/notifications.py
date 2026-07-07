"""In-app notification feed (REQ-018) — replaces the edit-Excel-then-Teams-
message loop described at the planning meeting.

Deliberately NOT a separate table: every mutation already lands in AuditLog
(rules.py, adjustments.py), so the feed is a read view over it. One source of
truth for "what changed" avoids the two logs drifting apart.
"""
from __future__ import annotations

from ..models import AuditLog


def recent_activity(session, since=None, limit: int = 50) -> list[dict]:
    q = session.query(AuditLog).order_by(AuditLog.at.desc())
    if since is not None:
        q = q.filter(AuditLog.at >= since)
    rows = q.limit(limit).all()
    return [{"id": r.id, "who": r.who, "what": r.what, "at": r.at.isoformat(),
            "before": r.before, "after": r.after} for r in rows]


def activity_summary(session) -> dict:
    total = session.query(AuditLog).count()
    by_who: dict[str, int] = {}
    for (who,) in session.query(AuditLog.who).all():
        by_who[who or "system"] = by_who.get(who or "system", 0) + 1
    return {"total_events": total, "by_author": by_who}
