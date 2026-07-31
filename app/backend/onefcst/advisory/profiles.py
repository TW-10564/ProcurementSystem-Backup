"""Employee display details, cached per email address.

The database records WHO did something by email, because that is the only
identifier Entra gives us that is both unique and stable across a rename
(`Identity.audit_author`). This module keeps the human-readable half of that
— name, job title, department — so a list of adjustments can say
「山田 太郎 / 購買本部」 instead of `m_yamada@twave.co.jp`.

Two rules hold everything else together:

  1. **A profile is only ever written for the caller's own verified identity.**
     Nothing accepts an email from the request body. Otherwise any signed-in
     user could rewrite how a colleague's past changes are attributed, which
     would quietly undo the point of taking the author from the token
     (`api/auth.effective_author`).

  2. **This is a cache, never an authorization input.** Roles and group
     membership come from the token on each request, not from here — a stale
     row must not be able to grant anything.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import UserProfile

# How long a row is considered fresh for `last_seen_at` bookkeeping. The point
# is to avoid a write on every single API call while still noticing an Azure
# rename promptly: any CHANGE to the details is written immediately regardless.
SEEN_INTERVAL = timedelta(hours=1)

# Fields the SPA may forward from Microsoft Graph. Anything else in the payload
# is ignored rather than stored — Graph returns a lot we have no use for, and
# an allowlist means a future Graph change cannot start writing surprise
# columns.
GRAPH_FIELDS = ("display_name", "given_name", "surname", "job_title",
                "department", "office_location")


def _clean(value: Any) -> str | None:
    """Graph and Entra both use null, "", and whitespace interchangeably."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def get(session: Session, email: str) -> UserProfile | None:
    key = (email or "").strip().lower()
    if not key:
        return None
    return session.scalars(
        select(UserProfile).where(UserProfile.email == key)).first()


def touch(session: Session, identity) -> UserProfile | None:
    """Record/refresh the caller from their ID-token claims.

    Called on `/me`, i.e. once per page load rather than per request. Returns
    None for an identity with no email — an API-key caller, or a tenant that
    populates neither `email` nor `preferred_username` — because a profile row
    with no join key would never be readable again.

    Deliberately does NOT clobber richer Graph-sourced details with the
    token's thinner ones: the ID token carries a name and nothing else, so a
    plain `touch()` after a Graph sync would blank out job title and
    department on every page load.
    """
    email = (getattr(identity, "email", "") or "").strip().lower()
    if not email:
        return None

    now = datetime.utcnow()
    row = get(session, email)
    if row is None:
        row = UserProfile(email=email, source="token", updated_at=now)
        session.add(row)

    changed = False
    oid = _clean(getattr(identity, "oid", None))
    if oid and row.oid != oid:
        row.oid, changed = oid, True

    # The token's `name` is authoritative for the display name — it is the
    # same string Azure shows — but only overwrite when we actually have one.
    name = _clean(getattr(identity, "name", None))
    if name and row.display_name != name:
        row.display_name, changed = name, True

    if changed:
        row.updated_at = now
    if changed or row.last_seen_at is None or now - row.last_seen_at > SEEN_INTERVAL:
        row.last_seen_at = now
        session.commit()
    return row


def apply_graph(session: Session, identity, details: dict[str, Any]) -> UserProfile | None:
    """Store the richer record the SPA fetched from Microsoft Graph.

    The backend cannot call Graph itself: it holds no credential of its own by
    design (public-client SSO, no secret — see api/entra.py). The browser
    already has a delegated `User.Read` token, so it fetches and forwards.

    That means the payload is client-supplied and is treated as such: the email
    key comes from the VERIFIED token, never from the body, so the worst a
    tampered payload can do is misspell the sender's own job title.
    """
    email = (getattr(identity, "email", "") or "").strip().lower()
    if not email:
        return None

    now = datetime.utcnow()
    row = get(session, email)
    if row is None:
        row = UserProfile(email=email)
        session.add(row)

    for field in GRAPH_FIELDS:
        value = _clean(details.get(field))
        # A field Graph left empty (no job title set in the directory) must not
        # erase a value we already have from the token.
        if value is not None:
            setattr(row, field, value)

    oid = _clean(getattr(identity, "oid", None))
    if oid:
        row.oid = oid
    row.source = "graph"
    row.updated_at = now
    row.last_seen_at = now
    session.commit()
    return row


def as_dict(row: UserProfile | None) -> dict[str, Any]:
    if row is None:
        return {}
    return {
        "email": row.email,
        "display_name": row.display_name,
        "given_name": row.given_name,
        "surname": row.surname,
        "job_title": row.job_title,
        "department": row.department,
        "office_location": row.office_location,
        "source": row.source,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def directory(session: Session) -> list[dict[str, Any]]:
    """Everyone who has ever signed in, for resolving `author` strings in the UI.

    Names and departments of colleagues, to people who are already
    authenticated members of the same small team — the same information the
    company address book shows them. No oid, no last-seen timestamp: neither
    helps the UI and both are needless to hand out.
    """
    rows = session.scalars(select(UserProfile).order_by(UserProfile.email)).all()
    return [{"email": r.email,
             "display_name": r.display_name,
             "job_title": r.job_title,
             "department": r.department} for r in rows]
