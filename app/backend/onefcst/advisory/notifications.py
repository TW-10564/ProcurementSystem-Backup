"""In-app notification feed (REQ-018) — replaces the edit-Excel-then-Teams-
message loop described at the planning meeting.

Deliberately NOT a separate table: every mutation already lands in AuditLog
(rules.py, adjustments.py), so the feed is a read view over it. One source of
truth for "what changed" avoids the two logs drifting apart.

FILTERING AND PAGING HAPPEN HERE, NOT IN THE BROWSER
    The page used to fetch the newest 200 rows and filter them in JavaScript.
    That fails in a way nobody reports as a bug: filtering by a colleague
    searches only those 200 rows, so someone with plenty of older activity
    shows "no results", which reads as a broken filter rather than a limit.
    A single bulk-confirm writes one row per rule, so 200 is reachable in one
    click.

CLASSIFICATION IS ALSO SERVER-SIDE, FOR THE SAME REASON
    The UI used to guess an event's type by string-matching `what`. Once the
    server does the filtering it must understand the same categories, and two
    independent classifiers would disagree the day either one is edited. The
    server now decides and ships `kind` with each row.

    The long-term fix is structured columns (entity_type/action) written at the
    point of the mutation, so nothing is inferred from a sentence at all. That
    is a schema change plus a backfill; these patterns are the honest interim,
    and they live in ONE place so replacing them later is contained.

PORTABILITY
    SQLite and PostgreSQL both run this (CLAUDE.md). Hence `ilike` rather than
    a raw LIKE (SQLAlchemy compiles it to lower()-based matching on SQLite),
    no julianday/strftime anywhere, and explicit NULL ordering on every sort.
"""
from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy import func, or_

from ..models import AuditLog
from ..timeutil import as_utc_iso

# Order matters: first match wins. "rejected" is tested before "confirmed",
# because a rejection message frequently contains both words.
_WARN_WORDS = ["reject", "delete", "revoke", "fail", "error",
               "却下", "削除", "失敗", "エラー", "警告"]
_CONFIRM_WORDS = ["confirm", "approve", "accept", "grant",
                  "承認", "確定", "完了"]
_ADJUST_WORDS = ["adjust", "override", "edit", "update", "order",
                 "調整", "上書", "修正", "変更", "発注"]

KIND_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("warn", re.compile("|".join(_WARN_WORDS), re.I)),
    ("confirm", re.compile("|".join(_CONFIRM_WORDS), re.I)),
    ("adjust", re.compile("|".join(_ADJUST_WORDS), re.I)),
]

KINDS = ("adjust", "confirm", "warn", "system")


def classify(what: str) -> str:
    for kind, pattern in KIND_PATTERNS:
        if pattern.search(what or ""):
            return kind
    return "system"


def _any(words: list[str]):
    return or_(*[AuditLog.what.ilike(f"%{w}%") for w in words])


def _kind_filter(query, kind: str):
    """Push the kind test into SQL so paging counts the right rows.

    Classifying in Python after a LIMIT would page over the wrong set — ask for
    50 adjustments and you would get however many of the newest 50 rows
    happened to be adjustments.

    The exclusions mirror the first-match-wins order of `classify`, so the four
    kinds partition the table exactly: every row belongs to one and only one,
    and the counts add up to the total.
    """
    if kind == "warn":
        return query.filter(_any(_WARN_WORDS))
    if kind == "confirm":
        return query.filter(_any(_CONFIRM_WORDS)).filter(~_any(_WARN_WORDS))
    if kind == "adjust":
        return (query.filter(_any(_ADJUST_WORDS))
                .filter(~_any(_WARN_WORDS)).filter(~_any(_CONFIRM_WORDS)))
    if kind == "system":
        return (query.filter(~_any(_WARN_WORDS))
                .filter(~_any(_CONFIRM_WORDS)).filter(~_any(_ADJUST_WORDS)))
    return query


def _row(r: AuditLog) -> dict:
    return {"id": r.id, "who": r.who, "what": r.what, "at": as_utc_iso(r.at),
            "before": r.before, "after": r.after, "kind": classify(r.what)}


def query_activity(session, *, who: str | None = None, kind: str | None = None,
                   q: str | None = None, date_from: datetime | None = None,
                   date_to: datetime | None = None, offset: int = 0,
                   limit: int = 50) -> dict:
    """One page of the feed, plus the TOTAL number that matched.

    `total` is what makes a truncated list honest: the page can say
    「1,234件中 50件を表示」 rather than implying that is everything there is.
    """
    limit = max(1, min(limit, 500))          # bounded — audit finding F4
    offset = max(0, offset)

    query = session.query(AuditLog)
    if who:
        query = query.filter(func.lower(AuditLog.who) == who.strip().lower())
    if q:
        # Matches the message AND the author, which is what "search" means to
        # someone looking for "everything about SSD" or "everything 田中 did".
        needle = f"%{q.strip()}%"
        query = query.filter(or_(AuditLog.what.ilike(needle),
                                 AuditLog.who.ilike(needle)))
    if date_from is not None:
        query = query.filter(AuditLog.at >= date_from)
    if date_to is not None:
        query = query.filter(AuditLog.at < date_to)
    if kind in KINDS:
        query = _kind_filter(query, kind)

    total = query.count()
    # Secondary sort on id: a bulk action writes many rows sharing a timestamp
    # to the second, and without a tiebreaker their relative order — and so
    # which page each lands on — is undefined. Rows would repeat or vanish
    # between pages. nulls_last is explicit because SQLite and PostgreSQL
    # disagree on the default (CLAUDE.md).
    rows = (query.order_by(AuditLog.at.desc().nulls_last(),
                           AuditLog.id.desc())
            .offset(offset).limit(limit).all())
    return {"items": [_row(r) for r in rows], "total": total,
            "offset": offset, "limit": limit,
            "has_more": offset + len(rows) < total}


def recent_activity(session, since=None, limit: int = 50) -> list[dict]:
    """Newest-first, unpaged. Kept for the dashboard's small activity feed."""
    query = session.query(AuditLog).order_by(AuditLog.at.desc().nulls_last(),
                                             AuditLog.id.desc())
    if since is not None:
        query = query.filter(AuditLog.at >= since)
    return [_row(r) for r in query.limit(max(1, min(limit, 500))).all()]


def activity_summary(session) -> dict:
    """Totals for the header, and the author list the filter chips are built
    from — grouped in SQL rather than by pulling every row into Python, which
    is what this did before and would get slower every month."""
    total = session.query(AuditLog).count()
    by_who = {(who or "system"): n for who, n in
              session.query(AuditLog.who, func.count(AuditLog.id))
              .group_by(AuditLog.who).all()}
    return {"total_events": total, "by_author": by_who}
