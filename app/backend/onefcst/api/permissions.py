"""Who may change what.

SSO answers *who is calling*; this module answers *what they may do*. The two
are deliberately separate files: `entra.py` verifies a token cryptographically
and stops there, so a bug in the rules below can never weaken authentication.

THE MODEL
    admin       everything, on every item. Rebuild from the NAS, re-run
                forecasts, maintenance mode, adjust anything.
    purchasing  adjustments and order actions on the parts in their own scope —
                the 1–3 ONE sub-files each buyer personally maintains.
    planning    the four Tier-1 categories 企画部 actually plans
                (CPU / グラフィックカード / ﾏｻﾞｰﾎﾞｰﾄﾞ / PCｹｰｽ), across the board.
    (none)      signed in, but no role assigned: reads everything, writes
                nothing.

TWO RULES THAT ARE EASY TO GET WRONG
  1. **Reads are never scoped.** Only writes. Everyone can see every forecast
     and every audit-log entry, including changes made by other departments.
     That is the point of the audit log, and siloing reads would make covering
     for an absent colleague harder without preventing a single mistake.
  2. **Enforcement is server-side, always.** The UI hides what you cannot
     change, but the API is directly reachable — a hidden button is a hint,
     not a permission.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
from ..models import Item, ItemScopeOverride, UserAccess, UserScope

ADMIN = "admin"
PURCHASING = "purchasing"
PLANNING = "planning"
ROLES = (ADMIN, PURCHASING, PLANNING)


def _email(identity) -> str:
    return (getattr(identity, "email", "") or "").strip().lower()


def role_for(session: Session, identity) -> str | None:
    """The caller's role, or None when they have not been granted one.

    `config.BOOTSTRAP_ADMINS` is the way out of the chicken-and-egg problem:
    the roster lives in the database, and somebody has to be able to edit it
    before anyone is in it. Those addresses are admins regardless of the
    table, so a fresh deployment (or one where the last admin row was deleted
    by accident) is never locked out of its own permissions screen.
    """
    # No SSO configured means there is no identity system to base roles on:
    # every caller is anonymous, so enforcing roles would lock EVERYONE out of
    # the rebuild button rather than protecting anything. Local dev and the
    # pre-SSO deployment therefore behave exactly as they did before, matching
    # the same opt-in rule `auth.effective_author` already follows.
    # Switching SSO on is what turns access control on.
    if not (config.ENTRA_TENANT_ID and config.ENTRA_CLIENT_ID):
        return ADMIN

    if identity is None:
        return None
    email = _email(identity)
    if not email:
        return None
    if email in config.BOOTSTRAP_ADMINS:
        return ADMIN

    row = session.scalars(
        select(UserAccess).where(UserAccess.email == email)).first()
    return row.role if row and row.role in ROLES else None


def scopes_for(session: Session, email: str) -> set[str]:
    """The sub-file keys a purchasing member owns (empty for other roles)."""
    email = (email or "").strip().lower()
    if not email:
        return set()
    return {r.scope_key for r in session.scalars(
        select(UserScope).where(UserScope.email == email))}


def scope_of_item(session: Session, item: Item) -> str | None:
    """Which ONE sub-file owns this item, or None if nothing does.

    Resolution order, and the order matters:

      1. An explicit override — the admin has hand-assigned this part to a
         sub-file. Wins over lineage so a correction actually sticks.
      2. The ingestion lineage: the item has plan rows that came from one of
         the 17 sub-file snapshots.
      3. None — genuinely unowned. About a third of forecastable items are in
         this state (Tier-2 parts the attach-rate engine derives, plus items
         compiled straight into ONE計画.xlsm's master sheet). They are live
         items, not discontinued, and they fall to admin until someone is
         assigned them.

    Deliberately computed rather than stored on Item: build.py drops and
    recreates every Item row, so a column there would need re-deriving on each
    rebuild anyway. Writes are rare enough that a query per write is free.
    """
    if item is None:
        return None

    override = session.scalars(
        select(ItemScopeOverride).where(
            ItemScopeOverride.canonical_name == item.canonical_name)).first()
    if override:
        return override.scope_key

    return _lineage_scope(session, item.id)


def _lineage_scope(session: Session, item_id: int) -> str | None:
    """Sub-file key derived from where this item's plan rows were ingested.

    Imported lazily: subfile_config reads config.ONE_DIR at import time, and
    pulling it in at module scope would drag NAS-path resolution into the
    permission layer.
    """
    from sqlalchemy import text

    from ..ingestion.subfile_config import FILES

    rows = session.execute(text("""
        SELECT DISTINCT s.source_file
        FROM plan_values p JOIN snapshots s ON s.id = p.snapshot_id
        WHERE p.item_id = :item_id
    """), {"item_id": item_id}).scalars().all()

    by_filename = {meta["file"]: key for key, meta in FILES.items()}
    for source_file in rows:
        key = by_filename.get(source_file)
        if key:
            return key
    return None


def can_write_item(session: Session, identity, item: Item) -> bool:
    """May this caller change values for this specific part?"""
    role = role_for(session, identity)
    if role == ADMIN:
        return True
    if role == PLANNING:
        # Planning tunes what planning plans. Tier is set at build time from
        # TIER1_CATEGORIES, so this stays true if that set is ever revised.
        return item is not None and (
            item.tier == 1 or (item.category in config.TIER1_CATEGORIES))
    if role == PURCHASING:
        scope = scope_of_item(session, item)
        # An unowned part is NOT open season: falling through to "allow" would
        # make the least-understood third of the catalogue the least protected.
        return scope is not None and scope in scopes_for(session, _email(identity))
    return False


def require_admin(session: Session, identity) -> None:
    """Gate for the destructive, system-wide operations.

    Rebuilds drop every table; a forecast run replaces the numbers everyone is
    reading; maintenance mode takes the site away. None of these are per-item,
    so none of them are scoped — they are simply admin.
    """
    if role_for(session, identity) != ADMIN:
        raise HTTPException(
            status_code=403,
            detail="Administrator access is required for this operation")


def require_item_write(session: Session, identity, item: Item) -> None:
    """403 with a message that says what to do next, not just 'no'.

    A buyer hitting this is usually looking at a colleague's part, and the
    useful reply names the owner-group so they know who to ask rather than
    assuming the system is broken.
    """
    if can_write_item(session, identity, item):
        return

    role = role_for(session, identity)
    name = getattr(item, "canonical_name", "this item")
    if role is None:
        detail = ("You do not have permission to make changes. "
                  "Ask an administrator to grant you access.")
    elif role == PLANNING:
        detail = (f"{name} is not one of the four categories 企画部 plans "
                  "(CPU / VGA / MB / Case).")
    else:
        scope = scope_of_item(session, item)
        detail = (f"{name} is outside your assigned parts"
                  + (f" (owned by: {scope})." if scope
                     else " — it has no assigned owner. Ask an administrator."))
    raise HTTPException(status_code=403, detail=detail)


def describe(session: Session, identity) -> dict:
    """What the UI needs to hide what the caller cannot use.

    Advisory only — every field here is re-checked server-side on the actual
    write. Shipping it means the frontend does not have to guess the rules and
    then drift from them.
    """
    role = role_for(session, identity)
    email = _email(identity)
    return {
        "role": role,
        "is_admin": role == ADMIN,
        "can_write": role is not None,
        "scopes": sorted(scopes_for(session, email)) if role == PURCHASING else [],
        # Planning writes are decided by the item's tier, not by a scope list.
        "tier1_only": role == PLANNING,
    }
