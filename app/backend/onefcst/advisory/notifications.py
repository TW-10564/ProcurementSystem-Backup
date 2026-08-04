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

import json
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


# ------------------------------------------------------------ localization
# `what` stays plain English prose, written once at the point of the
# mutation (advisory/*.py) — that's the searchable/classifiable column
# above, and it never changes shape, so `_kind_filter`/`q` search and every
# existing row (including everything written before this) keep working
# exactly as before. What's new: a caller MAY also merge `event(code, **args)`
# into the `after` dict it was already building (no schema change — `after`
# is free-form JSON text already). If it did, this renders the SAME event in
# Japanese from those args instead of guessing by parsing the English
# sentence back apart, which would be one regex per template and silently
# wrong the day a template's wording changes. Rows with no `_code` (not
# migrated yet, or written before this existed) just keep showing `what` —
# English-only, same as today, not an error.
EVENT_TEMPLATES: dict[str, str] = {
    "order_action.marked_ordered":
        "{item_name} の発注を記録しました（数量: {qty_display}、到着予定日: {date_display}）",
    "order_action.date_extended":
        "{item_name} の到着予定日を {date_display} に変更しました",
    "order_action.received":
        "{item_name} の入荷を確認しました",
    "order_action.dismissed":
        "{item_name} の発注を却下しました{reason_display}",
    "order_action.arrival_due":
        "{item_name} は {date_display} までに到着予定でした。入荷確認または予定日の変更をお願いします（警告）",
    "compat_rule.confirmed":
        "{part_a} と {part_b} の適合ルールを承認しました",
    "compat_rule.rejected":
        "{part_a} と {part_b} の適合ルールを却下しました{reason_display}",
    "compat_rule.edited":
        "{part_a} と {part_b} の適合ルールを変更しました",
    "adjustment.recorded":
        "{item_name} の {month_display} の予測を調整しました（{scope_ja}）：{old_display} → {new_display}",
    "discontinuation.confirmed":
        "{item_name} を生産終了として確定しました",
    "discontinuation.rejected":
        "{item_name} の生産終了提案を却下しました{reason_display}",
    "access.granted":
        "{email} に {role} 権限を付与しました{scopes_display}",
    "access.revoked":
        "{email} のアクセス権を削除しました",
    "access.item_scope_set":
        "{name} を {scope_key} の担当範囲に割り当てました",
}

_SCOPE_JA = {"global": "全体", "category": "カテゴリ", "sku": "SKU"}

# For `q`: `what` is stored in English (see the localization comment above),
# so a search typed in Japanese — including copy-pasted straight out of the
# Japanese-rendered feed the user is looking at — would otherwise never match
# anything. This expands a recognised Japanese term into the English
# substring(s) it corresponds to and ORs that into the SAME SQL query, so
# `total`/pagination stay correct (no fetch-everything-then-filter-in-Python,
# which is exactly what query_activity's docstring already warns against).
# Deliberately coarse — same spirit as _WARN_WORDS/_CONFIRM_WORDS/_ADJUST_WORDS
# above, not a full-sentence translator.
_JA_SEARCH_EXPANSIONS: dict[str, list[str]] = {
    "却下": ["reject", "dismiss"],
    "削除": ["delete", "dismiss"],
    "取り消し": ["revoke", "dismiss"],
    "取消": ["revoke", "dismiss"],
    "失敗": ["fail"],
    "エラー": ["error"],
    "警告": ["warn", "due"],
    "承認": ["confirm"],
    "確定": ["confirm"],
    "完了": ["confirm", "received"],
    "調整": ["adjust", "recorded"],
    "上書": ["override"],
    "修正": ["edit"],
    "編集": ["edit"],
    "変更": ["edit", "extend", "pushed"],
    "発注": ["order"],
    "入荷": ["received"],
    "受領": ["received"],
    "記録": ["recorded"],
    "付与": ["grant"],
    "割り当て": ["scope set"],
    "権限": ["role", "access"],
}


def _search_conditions(q: str):
    needle = f"%{q.strip()}%"
    conditions = [AuditLog.what.ilike(needle), AuditLog.who.ilike(needle)]
    for ja_word, en_terms in _JA_SEARCH_EXPANSIONS.items():
        if ja_word in q:
            conditions += [AuditLog.what.ilike(f"%{term}%") for term in en_terms]
    return conditions


def event(code: str, **args) -> dict:
    """Build the extra keys a write site merges into the `after` dict it
    already constructs, so localize_what() can render this event in another
    language later. `scope` args are auto-translated for the templates that
    use `scope_ja` — see _SCOPE_JA — everything else (names, quantities,
    dates) is language-neutral and passed straight through to the template."""
    if "scope" in args:
        args["scope_ja"] = _SCOPE_JA.get(args["scope"], args["scope"])
    return {"_code": code, "_args": args}


def _event_from_after(after: str | None) -> dict | None:
    if not after:
        return None
    try:
        obj = json.loads(after)
    except (TypeError, ValueError):
        return None
    if not isinstance(obj, dict) or "_code" not in obj:
        return None
    return obj


def localize_what(what: str, after: str | None, lang: str) -> str:
    """English `what` unchanged unless the caller asked for Japanese AND the
    row carries a recognised `_code` — see the module-level comment above."""
    if lang != "ja":
        return what
    ev = _event_from_after(after)
    if ev is None:
        return what
    template = EVENT_TEMPLATES.get(ev.get("_code", ""))
    if template is None:
        return what
    try:
        return template.format(**ev.get("_args", {}))
    except (KeyError, IndexError, ValueError):
        return what


def _row(r: AuditLog, lang: str = "en") -> dict:
    return {"id": r.id, "who": r.who, "what": localize_what(r.what, r.after, lang),
            "at": as_utc_iso(r.at),
            "before": r.before, "after": r.after, "kind": classify(r.what)}


def query_activity(session, *, who: str | None = None, kind: str | None = None,
                   q: str | None = None, date_from: datetime | None = None,
                   date_to: datetime | None = None, offset: int = 0,
                   limit: int = 50, lang: str = "en") -> dict:
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
        # someone looking for "everything about SSD" or "everything 田中 did"
        # — PLUS, if `q` contains a recognised Japanese term, the English
        # substring(s) it maps to (see _JA_SEARCH_EXPANSIONS above).
        query = query.filter(or_(*_search_conditions(q)))
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
    return {"items": [_row(r, lang) for r in rows], "total": total,
            "offset": offset, "limit": limit,
            "has_more": offset + len(rows) < total}


def recent_activity(session, since=None, limit: int = 50, lang: str = "en") -> list[dict]:
    """Newest-first, unpaged. Kept for the dashboard's small activity feed."""
    query = session.query(AuditLog).order_by(AuditLog.at.desc().nulls_last(),
                                             AuditLog.id.desc())
    if since is not None:
        query = query.filter(AuditLog.at >= since)
    return [_row(r, lang) for r in query.limit(max(1, min(limit, 500))).all()]


def activity_summary(session) -> dict:
    """Totals for the header, and the author list the filter chips are built
    from — grouped in SQL rather than by pulling every row into Python, which
    is what this did before and would get slower every month."""
    total = session.query(AuditLog).count()
    by_who = {(who or "system"): n for who, n in
              session.query(AuditLog.who, func.count(AuditLog.id))
              .group_by(AuditLog.who).all()}
    return {"total_events": total, "by_author": by_who}
