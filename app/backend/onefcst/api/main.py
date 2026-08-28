"""onefcst API — the advisory layer's HTTP surface (M4).

Read-only against every NAS source (enforced by construction: this app only
ever imports ingestion/engine/verify modules for READS; the only writes here
are to CompatRule.status, Adjustment, and AuditLog — all in the app's own
SQLite store). No endpoint here writes to Excel/ONE/SAP.

Run:  cd app/backend && uvicorn onefcst.api.main:app --host 0.0.0.0 --port 8600
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from .. import config
from .. import scheduler as scheduler_mod
from ..config import CORS_ORIGINS
from .. import db as db_mod
from ..db import get_session
from ..models import AuditLog, Forecast, Item, Suggestion
from ..timeutil import utc_now
from ..advisory import rules as rules_svc
from ..advisory import adjustments as adj_svc
from ..advisory import notifications as notif_svc
from ..advisory import discontinuation_review as discontinuation_svc
from ..advisory import order_actions as order_svc
from ..advisory import profiles as profiles_svc
from .auth import effective_author, require_identity
from . import permissions as perms
from .oauth import router as oauth_router
from .rate_limit import RateLimitMiddleware
from .schemas import (CompatRuleOut, ConfirmRequest, RejectRequest,
                      EditRuleRequest, AdjustmentIn, AdjustmentOut,
                      NotificationOut, DiscontinuationSuggestionOut,
                      OrderActionOut, MarkOrderedRequest, ExtendDateRequest,
                      GraphProfileIn, AccessGrantIn, ItemScopeIn)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create any table this build knows about but the database does not.
    # `create_all` is additive and idempotent — it never alters or drops an
    # existing table — so this is safe to run on every boot, and it is what
    # lets a NEW table (user_profiles) appear on a deployment without forcing
    # a 4-minute drop-and-rebuild. Until this existed, init_db() ran only from
    # build.run(), so a schema addition was invisible until the next rebuild
    # and every query against it failed with "no such table".
    try:
        db_mod.init_db()
    except Exception as exc:                                   # noqa: BLE001
        # Never block startup on it: /health and /maintenance must answer even
        # when the database is unreachable — that is exactly when someone is
        # trying to find out what is wrong.
        print(f"[startup] init_db skipped: {exc}")

    task = scheduler_mod.start()
    yield
    if task:
        task.cancel()


app = FastAPI(title="onefcst — Forecasting & Procurement Decision Support",
             description="Phase-1 advisory API. Read-only against all NAS "
             "sources; writes only to this app's own database.",
             lifespan=lifespan, dependencies=[Depends(require_identity)])

# Rate limiting added BEFORE CORS below — Starlette applies middleware in
# reverse of add order (last added = outermost), and CORS should stay
# outermost so its headers still get attached even on a 429 response.
app.add_middleware(RateLimitMiddleware)

# Sign-in endpoints. Registered before the SPA catch-all so /auth/* is
# handled by the API rather than served the index page.
app.include_router(oauth_router)

# CORS: the SPA (Vite dev server) runs on a different origin. Defaults to
# localhost-only (see config.CORS_ORIGINS) — set ONEFCST_CORS_ORIGINS to the
# deployed dashboard's real origin(s) before hosting this anywhere else.
# expose_headers: without it the browser hides Content-Disposition from JS on
# cross-origin responses, so every /export/* download silently fell back to
# its generic fallback name ("forecasts.xlsx") instead of the real Japanese
# filename the backend sets — found 2026-07-27.
# allow_credentials: the client (api/client.ts) sends withCredentials=true on
# every request, for the BFF session cookie added alongside oauth.py. Without
# this, the browser accepts the preflight (200 OK) but then refuses to send
# the real request at all — every call on a genuinely cross-origin setup
# (frontend/backend on different ports/hosts) fails as a silent axios
# "Network Error", found 2026-08-05. Safe with CORS_ORIGINS being an explicit
# list rather than "*": credentialed responses may never use a wildcard
# origin, and they don't here.
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"],
                   allow_headers=["*"], expose_headers=["Content-Disposition"],
                   allow_credentials=True)


def db():
    session = get_session()
    try:
        yield session
    finally:
        session.close()


@app.get("/health")
def health():
    return {"status": "ok", "time": utc_now().isoformat()}


# ------------------------------------------------------------------ build ---
# Re-ingest from the NAS on demand. Runs rebuild_preserving.run() (NEVER bare
# build.run()) so human-entered data — Adjustments, CompatRules, AuditLog,
# real Actual sales history — survives the drop+rebuild build.run() does
# internally; see rebuild_preserving.py's module docstring and
# PILOT_DEPLOY_PLAN.md blocker #1 for why that distinction is load-bearing.
# A real rebuild takes several minutes and touches the DB file other
# requests read from, so it runs in a background thread (asyncio.to_thread,
# same pattern scheduler.py already uses for the exact same blocking call)
# rather than blocking the request — the endpoint returns immediately and
# the frontend polls /build/rebuild/status.
#
# Attempts (this button's AND scheduler.py's own automatic weekly cycle)
# are recorded through the shared rebuild_status module — see its
# docstring for why: a scheduled rebuild failing in production used to be
# visible only in server stdout.
from .. import rebuild_status as rebuild_status_mod


async def _run_rebuild_in_background(entry: dict) -> None:
    import asyncio
    from ..rebuild_preserving import run as run_rebuild
    try:
        report = await asyncio.to_thread(run_rebuild)
        rebuild_status_mod.finish(entry, report)
        _invalidate_report_cache()
    except Exception as e:
        rebuild_status_mod.fail(entry, repr(e))


@app.post("/build/rebuild")
async def trigger_rebuild(request: Request, session: Session = Depends(db)):
    import asyncio
    # A rebuild drops every table and takes ~4 minutes. Audit finding F2: this
    # was reachable by anyone who could load the page.
    perms.require_admin(session, getattr(request.state, "identity", None))
    if rebuild_status_mod.is_running():
        raise HTTPException(status_code=409, detail="A rebuild is already running")
    entry = rebuild_status_mod.start("manual")
    asyncio.create_task(_run_rebuild_in_background(entry))
    return {"status": "started"}


@app.get("/auth/config")
def auth_config():
    """What this server expects of a sign-in. Public, and deliberately so.

    Every value here is already public by design: a tenant id and a client id
    are visible in the browser's address bar during any OIDC redirect, and the
    audience/issuer are derived from them. Nothing here authenticates anyone.

    Exposing it means an operator can ask the running server what it believes,
    rather than inferring it from an env file that may not be the one this
    process loaded — the exact confusion that cost us an afternoon when a
    stale .env was still in memory after an edit.
    """
    from . import entra
    cfg = entra.describe_config()
    # With backend sign-in the browser never starts an OIDC redirect itself, so
    # it has no use for the tenant/client ids — and the stated reason for
    # moving the flow server-side was to keep them out of the frontend. Report
    # only what the SPA actually needs: that sign-in is handled elsewhere.
    if config.OAUTH_BFF_ENABLED:
        return {"enabled": True, "backend_sign_in": True,
                "api_key_required": bool(config.API_KEY),
                "api_key_accepted": bool(config.API_KEY)
                                    and config.ENTRA_ALLOW_API_KEY}
    cfg["backend_sign_in"] = False
    # Only ever a boolean: whether a shared key exists, never the key.
    cfg["api_key_required"] = bool(config.API_KEY)
    cfg["api_key_accepted"] = bool(config.API_KEY) and (
        not cfg["enabled"] or config.ENTRA_ALLOW_API_KEY)
    return cfg


@app.get("/me")
def whoami(request: Request, session: Session = Depends(db)):
    """The identity this server sees for the current request.

    The frontend shows this as the signed-in employee, and it is the first
    thing to check when sign-in "works" but writes are attributed wrongly:
    if `source` is `api-key` then the browser is NOT sending a token and the
    audit trail will be anonymous.
    """
    ident = getattr(request.state, "identity", None)
    if ident is None:
        return {"authenticated": False, "source": "api-key" if config.API_KEY else "open"}

    # Record/refresh this person so their NAME can be shown against their past
    # changes — here rather than in the auth dependency on purpose: /me is
    # called about once per page load, whereas the dependency runs on every
    # request and would turn a read-only page into a stream of writes.
    profile = {}
    try:
        profile = profiles_svc.as_dict(profiles_svc.touch(session, ident))
    except Exception as exc:                                   # noqa: BLE001
        # Never let profile bookkeeping break sign-in: /me is what an operator
        # checks when auth is misbehaving, so it has to answer even if this
        # table is mid-rebuild or missing entirely.
        print(f"[profiles] touch failed for {ident.audit_author}: {exc}")

    return {
        "authenticated": True,
        "source": "entra",
        "name": ident.name,
        "email": ident.email,
        "oid": ident.oid,
        "groups": list(ident.groups),
        "roles": list(ident.roles),
        "audit_author": ident.audit_author,
        "profile": profile,
        # Advisory only — the UI uses it to hide what you cannot use. Every
        # field is re-checked server-side on the actual write.
        "permissions": perms.describe(session, ident),
    }


@app.put("/me/profile")
def update_my_profile(request: Request, body: GraphProfileIn,
                      session: Session = Depends(db)):
    """Store the Microsoft Graph details the browser fetched for ITSELF.

    This backend cannot ask Graph directly — it holds no credential of its own
    (public client, no secret). The SPA already has a delegated `User.Read`
    token, so it forwards the record here and everyone else's UI can then
    resolve that email to a name.

    Requires a real token: with only the shared API key there is no verified
    identity to attach the details to, and guessing would be worse than
    refusing.
    """
    ident = getattr(request.state, "identity", None)
    if ident is None:
        raise HTTPException(status_code=401,
                            detail="Sign-in required to update your profile")
    row = profiles_svc.apply_graph(session, ident, body.model_dump())
    if row is None:
        raise HTTPException(status_code=400,
                            detail="Your account has no email claim to key a profile on")
    return profiles_svc.as_dict(row)


# ---------------------------------------------------------------- access ---
# Managing the roster is itself an admin-only act: an endpoint that grants
# roles and is reachable by anyone with a role is not an access-control system.

@app.get("/access")
def list_access(request: Request, session: Session = Depends(db)):
    """The whole roster — who has which role, and which parts they own."""
    perms.require_admin(session, getattr(request.state, "identity", None))
    from ..models import UserAccess, UserScope
    scopes: dict[str, list[str]] = {}
    for s_ in session.query(UserScope).all():
        scopes.setdefault(s_.email, []).append(s_.scope_key)
    rows = []
    for u in session.query(UserAccess).order_by(UserAccess.email).all():
        rows.append({"email": u.email, "role": u.role, "note": u.note,
                     "granted_by": u.granted_by,
                     "scopes": sorted(scopes.get(u.email, []))})
    return {"users": rows,
            "roles": list(perms.ROLES),
            # The 17 sub-file keys, so the admin screen offers real choices
            # rather than a free-text box that silently accepts a typo.
            "scope_keys": _scope_keys(),
            "bootstrap_admins": list(config.BOOTSTRAP_ADMINS)}


def _scope_keys() -> list[str]:
    from ..ingestion.subfile_config import FILES
    return list(FILES.keys())


@app.put("/access")
def grant_access(request: Request, body: AccessGrantIn,
                 session: Session = Depends(db)):
    """Create or update one person's role and owned sub-files."""
    ident = getattr(request.state, "identity", None)
    perms.require_admin(session, ident)

    if body.role not in perms.ROLES:
        raise HTTPException(400, f"role must be one of {list(perms.ROLES)}")
    bad = [s_ for s_ in body.scopes if s_ not in _scope_keys()]
    if bad:
        # A typo'd scope key would silently own nothing, which looks identical
        # to a permissions bug when the buyer is refused.
        raise HTTPException(400, f"unknown sub-file(s): {bad}")

    from ..models import UserAccess, UserScope
    email = body.email.strip().lower()
    if not email:
        raise HTTPException(400, "email is required")
    by = ident.audit_author if ident is not None else "unknown"

    row = session.query(UserAccess).filter(UserAccess.email == email).first()
    if row is None:
        row = UserAccess(email=email, granted_by=by)
        session.add(row)
    row.role, row.note, row.updated_at = body.role, body.note, utc_now()

    # Replace rather than merge: the admin screen sends the complete list, so
    # merging would make removing a sub-file impossible.
    session.query(UserScope).filter(UserScope.email == email).delete()
    for key in dict.fromkeys(body.scopes):
        session.add(UserScope(email=email, scope_key=key, granted_by=by))

    scopes_english = f" scopes={sorted(set(body.scopes))}" if body.scopes else ""
    scopes_display = f"（担当: {'、'.join(sorted(set(body.scopes)))}）" if body.scopes else ""
    session.add(AuditLog(
        who=by, what=f"access granted: {email} -> {body.role}" + scopes_english,
        after=json.dumps(notif_svc.event("access.granted", email=email, role=body.role,
                                        scopes_display=scopes_display))))
    session.commit()
    return {"email": email, "role": body.role, "scopes": sorted(set(body.scopes))}


@app.delete("/access/{email}")
def revoke_access(request: Request, email: str, session: Session = Depends(db)):
    ident = getattr(request.state, "identity", None)
    perms.require_admin(session, ident)
    from ..models import UserAccess, UserScope
    email = email.strip().lower()
    n = session.query(UserAccess).filter(UserAccess.email == email).delete()
    session.query(UserScope).filter(UserScope.email == email).delete()
    by = ident.audit_author if ident is not None else "unknown"
    session.add(AuditLog(who=by, what=f"access revoked: {email}",
                         after=json.dumps(notif_svc.event("access.revoked", email=email))))
    session.commit()
    # Revoking leaves the person able to READ — that is the intended floor,
    # not an oversight. Removing sign-in entirely is an Entra assignment.
    return {"revoked": bool(n), "email": email}


@app.get("/access/unowned-items")
def unowned_items(request: Request, limit: int = 500,
                  session: Session = Depends(db)):
    """Forecastable parts that belong to no sub-file, so only an admin can
    currently change them.

    These are NOT discontinued items — they are mostly Tier-2 parts the
    attach-rate engine derives, plus items compiled straight into ONE計画's
    master sheet. Listing them is how the roster gets finished.
    """
    perms.require_admin(session, getattr(request.state, "identity", None))
    limit = max(1, min(limit, 2000))
    out = []
    seen = {f.item_id for f in session.query(Forecast.item_id).distinct()}
    for item in session.query(Item).filter(Item.id.in_(seen)).all():
        if perms.scope_of_item(session, item) is None:
            out.append({"id": item.id, "canonical_name": item.canonical_name,
                        "category": item.category, "tier": item.tier})
            if len(out) >= limit:
                break
    return {"items": out, "scope_keys": _scope_keys()}


@app.put("/access/item-scope")
def set_item_scope(request: Request, body: ItemScopeIn,
                   session: Session = Depends(db)):
    """Hand-assign an unowned part to a sub-file owner group."""
    ident = getattr(request.state, "identity", None)
    perms.require_admin(session, ident)
    if body.scope_key not in _scope_keys():
        raise HTTPException(400, f"unknown sub-file: {body.scope_key}")

    from ..models import ItemScopeOverride
    name = body.canonical_name.strip()
    row = session.query(ItemScopeOverride).filter(
        ItemScopeOverride.canonical_name == name).first()
    by = ident.audit_author if ident is not None else "unknown"
    if row is None:
        row = ItemScopeOverride(canonical_name=name, assigned_by=by)
        session.add(row)
    row.scope_key = body.scope_key
    session.add(AuditLog(who=by, what=f"item scope set: {name} -> {body.scope_key}",
                         after=json.dumps(notif_svc.event("access.item_scope_set", name=name,
                                                          scope_key=body.scope_key))))
    session.commit()
    return {"canonical_name": name, "scope_key": body.scope_key}


@app.get("/users/directory")
def users_directory(session: Session = Depends(db)):
    """email → display name for everyone who has signed in.

    Lets any view that shows an `author`/`who`/`confirmed_by` string render the
    person's Azure name instead of a mail alias, and — because each row is
    refreshed at that person's next sign-in — a rename in Azure propagates to
    every historical record without touching the stored keys.
    """
    return {"users": profiles_svc.directory(session)}


@app.get("/maintenance")
def get_maintenance():
    """Public by design (see auth.py): every browser polls this, and it must
    answer even when the DB is locked mid-rebuild or the key has rotated."""
    from .. import maintenance
    return maintenance.get_state()


@app.post("/maintenance")
def set_maintenance(request: Request, body: dict, session: Session = Depends(db)):
    """Toggle the site-wide notice. Requires the API key like every other
    write. Takes effect on every client within one poll (~20s), no restart.

    `by` comes from the verified identity when there is one. Maintenance mode
    is the flag that means "a person deliberately closed the site" (policy
    2026-07-30), so the record of WHICH person is the whole point of the field
    — it defaulted to the literal "portal" before.
    """
    # Taking the site away from everyone is an admin act, not a per-item one.
    perms.require_admin(session, getattr(request.state, "identity", None))
    from .. import maintenance
    state = maintenance.set_state(
        bool(body.get("enabled")),
        str(body.get("message") or ""),
        by=effective_author(request, str(body.get("by") or "")) or "portal",
    )
    return state


@app.get("/build/rebuild/status")
def get_rebuild_status():
    return rebuild_status_mod.current() or {"status": "idle", "trigger": None,
                                            "started_at": None, "finished_at": None,
                                            "report": None, "error": None}


@app.get("/build/rebuild/history")
def get_rebuild_history(limit: int = 20):
    return rebuild_status_mod.history(limit)


# ---------------------------------------------------------------- compat ---
@app.get("/compat-rules", response_model=list[CompatRuleOut])
def get_compat_rules(status: str | None = None, min_confidence: float | None = None,
                     limit: int = 200, session: Session = Depends(db)):
    rows = rules_svc.list_rules(session, status=status, min_confidence=min_confidence,
                                limit=limit)
    items = {i.id: i.canonical_name for i in session.query(Item).all()}
    return [CompatRuleOut(id=r.id, part_a_id=r.part_a_id,
                          part_a_name=items.get(r.part_a_id, "?"),
                          part_b_id=r.part_b_id, part_b_name=items.get(r.part_b_id, "?"),
                          relation=r.relation, confidence=r.confidence, origin=r.origin,
                          status=r.status, confirmed_by=r.confirmed_by, note=r.note) for r in rows]


@app.post("/compat-rules/{rule_id}/confirm", response_model=CompatRuleOut)
def confirm_compat_rule(request: Request, rule_id: int, body: ConfirmRequest, session: Session = Depends(db)):
    try:
        r = rules_svc.confirm_rule(session, rule_id, effective_author(request, body.author))
    except rules_svc.RuleNotFound as e:
        raise HTTPException(404, str(e))
    _invalidate_report_cache()  # mismatch's confirmed_rule_classifications depends on this
    items = {i.id: i.canonical_name for i in session.query(Item).all()}
    return CompatRuleOut(id=r.id, part_a_id=r.part_a_id, part_a_name=items.get(r.part_a_id, "?"),
                         part_b_id=r.part_b_id, part_b_name=items.get(r.part_b_id, "?"),
                         relation=r.relation, confidence=r.confidence, origin=r.origin,
                         status=r.status, confirmed_by=r.confirmed_by, note=r.note)


@app.post("/compat-rules/{rule_id}/reject", response_model=CompatRuleOut)
def reject_compat_rule(request: Request, rule_id: int, body: RejectRequest, session: Session = Depends(db)):
    try:
        r = rules_svc.reject_rule(session, rule_id, effective_author(request, body.author), body.reason)
    except rules_svc.RuleNotFound as e:
        raise HTTPException(404, str(e))
    _invalidate_report_cache()
    items = {i.id: i.canonical_name for i in session.query(Item).all()}
    return CompatRuleOut(id=r.id, part_a_id=r.part_a_id, part_a_name=items.get(r.part_a_id, "?"),
                         part_b_id=r.part_b_id, part_b_name=items.get(r.part_b_id, "?"),
                         relation=r.relation, confidence=r.confidence, origin=r.origin,
                         status=r.status, confirmed_by=r.confirmed_by, note=r.note)


@app.patch("/compat-rules/{rule_id}", response_model=CompatRuleOut)
def edit_compat_rule(request: Request, rule_id: int, body: EditRuleRequest, session: Session = Depends(db)):
    """Correct a draft rule's own judgement (confidence/relation/note)
    before deciding confirm/reject — e.g. the AI got the confidence wrong,
    or a reviewer wants to record their own reasoning. Only allowed on
    status='draft' rules; a confirmed/rejected rule is a settled decision
    (see advisory/rules.py::edit_rule's docstring)."""
    try:
        r = rules_svc.edit_rule(session, rule_id, effective_author(request, body.author), relation=body.relation,
                                confidence=body.confidence, note=body.note)
    except rules_svc.RuleNotFound as e:
        raise HTTPException(404, str(e))
    except rules_svc.RuleNotEditable as e:
        raise HTTPException(409, str(e))
    items = {i.id: i.canonical_name for i in session.query(Item).all()}
    return CompatRuleOut(id=r.id, part_a_id=r.part_a_id, part_a_name=items.get(r.part_a_id, "?"),
                         part_b_id=r.part_b_id, part_b_name=items.get(r.part_b_id, "?"),
                         relation=r.relation, confidence=r.confidence, origin=r.origin,
                         status=r.status, confirmed_by=r.confirmed_by, note=r.note)


def _discontinuation_out(s: Suggestion) -> DiscontinuationSuggestionOut:
    import json as _json
    try:
        payload = _json.loads(s.payload) if s.payload else {}
    except (TypeError, ValueError):
        payload = {}
    return DiscontinuationSuggestionOut(
        id=s.id, item_id=payload.get("item_id"), item_name=payload.get("name"),
        category=payload.get("category"), source_category_file=payload.get("source_category_file"),
        detected_at=payload.get("detected_at"), status=s.status, reviewed_by=s.reviewed_by)


# ------------------------------------------------------- discontinuation ---
@app.get("/discontinuation-suggestions", response_model=list[DiscontinuationSuggestionOut])
def get_discontinuation_suggestions(status: str | None = None, limit: int = 500,
                                     session: Session = Depends(db)):
    """Draft queue for purchasing's gray 'won't reorder' cell-marking (see
    registry/build.py step 7): a SKU newly seen gray-marked lands here as
    'draft' — Item.eol_flag is untouched until /confirm is called."""
    rows = discontinuation_svc.list_suggestions(session, status=status, limit=limit)
    return [_discontinuation_out(s) for s in rows]


@app.post("/discontinuation-suggestions/{suggestion_id}/confirm",
         response_model=DiscontinuationSuggestionOut)
def confirm_discontinuation(request: Request, suggestion_id: int, body: ConfirmRequest,
                            session: Session = Depends(db)):
    try:
        s = discontinuation_svc.confirm_suggestion(session, suggestion_id, effective_author(request, body.author))
    except discontinuation_svc.SuggestionNotFound as e:
        raise HTTPException(404, str(e))
    except discontinuation_svc.SuggestionNotEditable as e:
        raise HTTPException(409, str(e))
    return _discontinuation_out(s)


@app.post("/discontinuation-suggestions/{suggestion_id}/reject",
         response_model=DiscontinuationSuggestionOut)
def reject_discontinuation(request: Request, suggestion_id: int, body: RejectRequest,
                           session: Session = Depends(db)):
    try:
        s = discontinuation_svc.reject_suggestion(session, suggestion_id, effective_author(request, body.author), body.reason)
    except discontinuation_svc.SuggestionNotFound as e:
        raise HTTPException(404, str(e))
    except discontinuation_svc.SuggestionNotEditable as e:
        raise HTTPException(409, str(e))
    return _discontinuation_out(s)


# ------------------------------------------------------------ order actions
@app.post("/order-actions/{item_id}/mark-ordered", response_model=OrderActionOut)
def mark_ordered(request: Request, item_id: int, body: MarkOrderedRequest, session: Session = Depends(db)):
    item = session.get(Item, item_id)
    if item is None:
        raise HTTPException(404, f"item {item_id} not found")
    # Recording an order against a colleague's part is the same class of act as
    # adjusting its forecast, so it carries the same scope rule.
    perms.require_item_write(session, getattr(request.state, "identity", None), item)
    try:
        oa = order_svc.mark_ordered(session, item, qty_ordered=body.qty_ordered,
                                    expected_arrival_date=body.expected_arrival_date,
                                    supplier=body.supplier,
                                    note=body.note, author=effective_author(request, body.author))
    except order_svc.OrderActionAlreadyActive as e:
        raise HTTPException(409, str(e))
    return oa


@app.post("/order-actions/{order_action_id}/extend", response_model=OrderActionOut)
def extend_order_action(request: Request, order_action_id: int, body: ExtendDateRequest,
                        session: Session = Depends(db)):
    try:
        oa = order_svc.extend_expected_date(session, order_action_id,
                                            body.expected_arrival_date, effective_author(request, body.author))
    except order_svc.OrderActionNotFound as e:
        raise HTTPException(404, str(e))
    except order_svc.OrderActionNotActive as e:
        raise HTTPException(409, str(e))
    return oa


@app.post("/order-actions/{order_action_id}/confirm-received", response_model=OrderActionOut)
def confirm_order_received(request: Request, order_action_id: int, body: ConfirmRequest,
                           session: Session = Depends(db)):
    try:
        oa = order_svc.confirm_received(session, order_action_id, effective_author(request, body.author))
    except order_svc.OrderActionNotFound as e:
        raise HTTPException(404, str(e))
    except order_svc.OrderActionNotActive as e:
        raise HTTPException(409, str(e))
    return oa


@app.post("/order-actions/{order_action_id}/dismiss", response_model=OrderActionOut)
def dismiss_order_action(request: Request, order_action_id: int, body: RejectRequest,
                         session: Session = Depends(db)):
    try:
        oa = order_svc.dismiss(session, order_action_id, effective_author(request, body.author), body.reason)
    except order_svc.OrderActionNotFound as e:
        raise HTTPException(404, str(e))
    except order_svc.OrderActionNotActive as e:
        raise HTTPException(409, str(e))
    return oa


@app.post("/compat-rules/bulk-confirm")
def bulk_confirm(request: Request, author: str = "", threshold: float = 0.9,
                 session: Session = Depends(db)):
    # One click confirms every draft rule above the threshold — the widest
    # single write in the system, and not something to leave open to all.
    perms.require_admin(session, getattr(request.state, "identity", None))
    # `author` stays in the signature (a query param here, not a body field) so
    # scripted callers without a token keep working, but a verified identity
    # overrides it — this endpoint confirms rules in bulk and is the last place
    # you want a self-asserted name on the record.
    n = rules_svc.bulk_confirm_high_confidence(
        session, effective_author(request, author), threshold)
    _invalidate_report_cache()
    return {"confirmed": n, "threshold": threshold}


@app.post("/compat-rules/draft-llm")
def draft_llm_compat_rules(session: Session = Depends(db)):
    """LLM pass over parts the spec table can't classify (new products).
    Drafts land as origin='llm' with confidence capped below bulk-confirm —
    each needs an individual human decision (BR-010)."""
    from ..advisory.llm_rules import draft_llm_rules
    return draft_llm_rules(session)


# ------------------------------------------------------------ adjustments --
@app.post("/adjustments", response_model=AdjustmentOut)
def create_adjustment(request: Request, body: AdjustmentIn, session: Session = Depends(db)):
    # Checked BEFORE recording: an adjustment the caller may not make must
    # leave no trace at all, not a row plus a rollback.
    perms.require_item_write(session, getattr(request.state, "identity", None),
                             session.get(Item, body.item_id))
    try:
        a = adj_svc.record_adjustment(session, item_id=body.item_id, month=body.month,
                                      old_value=body.old_value, new_value=body.new_value,
                                      scope=body.scope, author=effective_author(request, body.author),
                                      reason=body.reason, factor=body.factor)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _invalidate_report_cache()  # forecasts-summary's orders_needed reflects adjustments
    item = session.get(Item, a.item_id) if a.item_id else None
    a.item_name = item.canonical_name if item else None
    return a


@app.get("/adjustments", response_model=list[AdjustmentOut])
def get_adjustments(item_id: int | None = None, session: Session = Depends(db)):
    rows = adj_svc.list_adjustments(session, item_id=item_id)
    names = {i.id: i.canonical_name for i in session.query(Item)
            .filter(Item.id.in_({r.item_id for r in rows if r.item_id})).all()}
    for r in rows:
        r.item_name = names.get(r.item_id)
    return rows


# ------------------------------------------------------------ notifications-
@app.get("/notifications")
def get_notifications(limit: int = 50, offset: int = 0,
                      who: str | None = None, kind: str | None = None,
                      q: str | None = None,
                      date_from: str | None = None, date_to: str | None = None,
                      lang: str = "en", session: Session = Depends(db)):
    """One page of the change feed, filtered server-side.

    Returns {items, total, offset, limit, has_more} rather than a bare list:
    `total` is what lets the page say 「1,234件中 50件を表示」 instead of
    silently implying the page is everything. Filtering here rather than in the
    browser is the actual fix — the old page fetched 200 rows and filtered
    them locally, so a filter could report "no results" for a colleague who
    simply had no activity in the newest 200.

    `lang="ja"` renders `what` in Japanese for rows that carry a structured
    event code (notifications.EVENT_TEMPLATES) — see notifications.py's
    localize_what(). `what` search (`q`) still matches the underlying
    English text regardless of `lang`; only the returned text changes.
    """
    def _parse(value: str | None):
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            raise HTTPException(400, f"invalid date: {value!r} (use YYYY-MM-DD)")

    # Best-effort: surfaces "order due" alerts (order_svc.raise_due_alerts)
    # the moment anyone opens Notifications or the Dashboard, independent of
    # whether the background scheduler is enabled for this deployment
    # (ONEFCST_SCHEDULER_ENABLED defaults to 0 — see deploy/compose.env.example).
    order_svc.raise_due_alerts(session)

    return notif_svc.query_activity(
        session, who=who, kind=kind, q=q, offset=offset, limit=limit,
        date_from=_parse(date_from), date_to=_parse(date_to), lang=lang)


@app.get("/notifications/summary")
def get_notification_summary(session: Session = Depends(db)):
    order_svc.raise_due_alerts(session)
    return notif_svc.activity_summary(session)


# ------------------------------------------------------------------ items --
@app.get("/items")
def search_items(q: str = "", limit: int = 20, session: Session = Depends(db)):
    query = session.query(Item)
    if q:
        query = query.filter(Item.canonical_name.ilike(f"%{q}%"))
    rows = query.order_by(Item.canonical_name).limit(limit).all()
    return [{"id": i.id, "canonical_name": i.canonical_name, "category": i.category,
            "tier": i.tier} for i in rows]


@app.get("/items/{item_id}")
def get_item(item_id: int, session: Session = Depends(db)):
    item = session.get(Item, item_id)
    if item is None:
        raise HTTPException(404, "item not found")
    return {"id": item.id, "canonical_name": item.canonical_name,
           "category": item.category, "tier": item.tier, "lifecycle": item.lifecycle,
           "moq": item.moq, "lead_time_days": item.lead_time_days,
           "exclusive_flag": item.exclusive_flag}


# ------------------------------------------------------------- forecasts ---
@app.post("/forecasts/run")
def run_forecast(request: Request, horizon: int = 6, session: Session = Depends(db)):
    """Generate a new forecast run (writes only to the app DB). Every run
    validates itself out-of-sample and stores the accuracy with the run.

    Admin-only: it replaces the numbers every other user is currently reading
    and acting on, so it is not something to trigger from a shared PC by
    accident."""
    perms.require_admin(session, getattr(request.state, "identity", None))
    from ..forecasting import generate
    result = generate(session, horizon=horizon)
    _invalidate_report_cache()
    return result


@app.get("/forecasts/summary")
def forecast_summary(session: Session = Depends(db)):
    # Cached like the reports below. This is the dashboard's landing call —
    # every page mount and every open tab asks for it — and recomputing it
    # costs seconds. Uncached, several clients polling at once serialised
    # behind one worker and pushed the response past 100s (measured
    # 2026-07-28 with two browsers open), which the frontend surfaces as an
    # empty page. run_forecast() above (and a rebuild) explicitly invalidate
    # the cache, so this stays fresh exactly when the numbers actually move.
    from .. import forecast_views
    return _cached("forecasts-summary", lambda: forecast_views.summary(session))


@app.get("/forecasts")
def list_forecasts(q: str = "", category: str | None = None, method: str | None = None,
                   tier: int | None = None, limit: int = 50, offset: int = 0,
                   session: Session = Depends(db)):
    from .. import forecast_views
    return forecast_views.list_forecasts(session, q=q, category=category, method=method,
                                         tier=tier, limit=limit, offset=offset)


@app.get("/forecasts/order-suggestions")
def get_order_suggestions(session: Session = Depends(db)):
    """Detail behind the dashboard's "orders needed" count — every item
    that needs a purchase order, with stock/quantity/timing so a buyer can
    assess real need, not just see a number. Must be registered BEFORE
    /forecasts/{item_id} or FastAPI tries to parse "order-suggestions" as
    an item_id and 422s."""
    from .. import forecast_views
    return forecast_views.order_suggestions(session)


@app.get("/forecasts/{item_id}")
def forecast_detail(item_id: int, session: Session = Depends(db)):
    from .. import forecast_views
    detail = forecast_views.item_detail(session, item_id)
    if detail is None:
        raise HTTPException(404, "item not found")
    return detail


@app.get("/forecasts/{item_id}/plan-vs-actual")
def get_plan_vs_actual(item_id: int, history_months: int = 1, session: Session = Depends(db)):
    """System-planned quantity (weekly-disaggregated, adjustments already
    applied) vs real weekly SAP-shipped quantity, including a trailing
    window of already-closed history — see forecast_views.plan_vs_actual
    for the week-alignment caveat and why history needs its own plan
    source. Tuning goes through the existing POST /adjustments
    (scope="sku"), not a write path on this endpoint — see that
    function's own docstring."""
    from .. import forecast_views
    result = forecast_views.plan_vs_actual(session, item_id, history_months=history_months)
    if result is None:
        raise HTTPException(404, "item not found or no forecast run yet")
    return result


# -------------------------------------------------------------- reports ----
# mismatch/backtest recompute over the full plan-value history (~6-10s each);
# the dashboard requests both on every mount, so cache until something that
# feeds them actually changes.
#
# Used to key on os.path.getmtime(DB_PATH) instead of invalidating
# explicitly — DB_PATH is the SQLite path, which happens to get touched by
# every SQLite write (so it accidentally worked there), but is completely
# unrelated to the DB once DATABASE_URL points at Postgres. On Postgres that
# mtime never changes, so a report cached before the first rebuild/forecast
# run stayed stuck at that stale value (e.g. forecasts-summary showing
# run: null and empty categories) for the process's entire lifetime — hit in
# production on the DGX deployment 2026-07-31: "Recalculate" ran successfully
# but the dashboard kept showing nothing, because it was still serving the
# cache entry computed before the first real forecast run existed. Explicit
# invalidation at every mutating endpoint below works identically on both
# backends.
_report_cache: dict[str, object] = {}


def _invalidate_report_cache() -> None:
    _report_cache.clear()


def _cached(key: str, compute):
    if key in _report_cache:
        return _report_cache[key]
    result = compute()
    _report_cache[key] = result
    return result


@app.get("/reports/mismatch")
def get_mismatch_report(session: Session = Depends(db)):
    from ..verify.mismatch import compute_mismatch
    return _cached("mismatch", lambda: compute_mismatch(session))


@app.get("/reports/source-reconciliation")
def get_source_reconciliation(session: Session = Depends(db)):
    """Master vs sub-file (one_row) disagreement for near-term item-months
    — flags, does not resolve (see verify/reconciliation.py). A listed item
    isn't necessarily wrong in master; it needs a human look."""
    from ..verify.reconciliation import run as run_reconciliation
    return _cached("source-reconciliation", lambda: run_reconciliation(session))


@app.get("/reports/backtest")
def get_backtest_report():
    from ..verify.backtest import run as run_backtest
    return _cached("backtest", run_backtest)


@app.get("/reports/fva")
def get_fva_report():
    from ..verify.fva import run as run_fva
    return run_fva()


@app.get("/reports/planner-accuracy")
def get_planner_accuracy(session: Session = Depends(db)):
    """The planner's OWN forecast accuracy, measured the same way the system
    grades itself — see planner_accuracy.py. Honestly empty until archived
    snapshots' target months have passed and closed."""
    from ..planner_accuracy import planner_wape
    return planner_wape(session)


@app.get("/reports/adjustment-rate")
def get_adjustment_rate(session: Session = Depends(db)):
    return adj_svc.adjustment_rate(session)


@app.get("/reports/discontinuation-risk")
def get_discontinuation_risk(session: Session = Depends(db)):
    """Items with a sharp recent demand falloff the forecast hasn't caught
    up to yet — see verify/discontinuation.py for the exact heuristic and
    why is_stale/apply_discontinuation_signal/Item.lifecycle don't already
    cover this case for most categories."""
    from ..verify.discontinuation import run as run_discontinuation
    return _cached("discontinuation-risk", lambda: run_discontinuation(session))


@app.get("/reports/sales-reconciliation")
def get_sales_reconciliation(session: Session = Depends(db)):
    """ONE's closed-month plan_value vs SAP's shipped-quantity Actual for
    the SAME item-month — no assumed winner (see verify/sales_reconciliation.py).
    The forecasting engine trains on ONE's number; FVA scoring grades
    against SAP's — a disagreement here means those two processes are
    working from different ground truths for that item-month."""
    from ..verify.sales_reconciliation import run as run_sales_reconciliation
    return _cached("sales-reconciliation", lambda: run_sales_reconciliation(session))


@app.get("/reports/stock-reconciliation")
def get_stock_reconciliation(session: Session = Depends(db)):
    """SAP vs ONE stock disagreement, and which items have no SAP coverage
    at all — see verify/stock_reconciliation.py. SAP is always the value
    actually used (trusted_current_balance); this report is for visibility
    into how much of the catalogue rests on unverified ONE-only data."""
    from ..verify.stock_reconciliation import run as run_stock_reconciliation
    return _cached("stock-reconciliation", lambda: run_stock_reconciliation(session))


# --------------------------------------------------------------- exports ---
# Every view has an Excel export — new workbooks only, written to
# app/data/exports/. Staff who don't trust a screen number can verify it in
# the tool they already trust (POC_BUILD_PLAN §"everything checkable in Excel").
XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@app.get("/export/mismatch")
def export_mismatch_xlsx():
    from ..export.excel import export_mismatch
    path = export_mismatch()
    return FileResponse(path, filename=path.name, media_type=XLSX_MEDIA)


@app.get("/export/compat-rules")
def export_compat_rules_xlsx(status: str | None = None):
    from ..export.excel import export_compat_rules
    path = export_compat_rules(status)
    return FileResponse(path, filename=path.name, media_type=XLSX_MEDIA)


@app.get("/export/adjustments")
def export_adjustments_xlsx():
    from ..export.excel import export_adjustments
    path = export_adjustments()
    return FileResponse(path, filename=path.name, media_type=XLSX_MEDIA)


@app.get("/export/backtest")
def export_backtest_xlsx():
    from ..export.excel import export_backtest
    path = export_backtest()
    return FileResponse(path, filename=path.name, media_type=XLSX_MEDIA)


@app.get("/export/forecasts")
def export_forecasts_xlsx(tier: int | None = None):
    # tier: None = all items, 1 = planning's four categories (CPU/VGA/MB/Case),
    # 2 = everything purchasing derives — same split as Item.tier / GET /forecasts.
    if tier not in (None, 1, 2):
        raise HTTPException(status_code=422, detail="tier must be 1 or 2 (omit for all)")
    from ..export.excel import export_forecasts
    path = export_forecasts(tier=tier)
    return FileResponse(path, filename=path.name, media_type=XLSX_MEDIA)


@app.get("/export/audit-log")
def export_audit_log_xlsx(year: int | None = None, month: int | None = None):
    """The change history as Excel.

    With year+month: that whole calendar month, with each author's name and
    department resolved — the monthly report the team files. Without: the
    latest 500 entries, which is the quick "what happened recently" download.
    """
    if year and month:
        from ..export.excel import export_audit_month
        if not 1 <= month <= 12:
            raise HTTPException(400, "month must be 1-12")
        path, _ = export_audit_month(year, month)
    else:
        from ..export.excel import export_audit_log
        path = export_audit_log()
    return FileResponse(path, filename=path.name, media_type=XLSX_MEDIA)


@app.post("/audit/archive")
def archive_audit_month(request: Request, year: int, month: int,
                        commit: bool = False, force: bool = False,
                        session: Session = Depends(db)):
    """Export a finished month and clear it from the feed.

    Admin-only and dry-run by default: this is the one endpoint that DELETES
    audit records, and it will not do so unless the exported workbook has been
    written, reopened and counted (see audit_archive). A failed verification
    aborts with nothing deleted.
    """
    ident = getattr(request.state, "identity", None)
    perms.require_admin(session, ident)
    if not 1 <= month <= 12:
        raise HTTPException(400, "month must be 1-12")
    from ..audit_archive import archive
    by = ident.audit_author if ident is not None else "audit-archive"
    result = archive(year, month, commit=commit, force=force, by=by)
    if result["status"] == "verification-failed":
        # 500, not 400: the caller did nothing wrong — the server could not
        # produce a trustworthy export, and nothing was deleted.
        raise HTTPException(500, f"archive aborted, nothing deleted: "
                                 f"{result.get('verification')}")
    return result


# --- production frontend serving --------------------------------------------
# Serve the built SPA (app/frontend/dist) from this same process, so a server
# deployment needs no separate web server: `npm run build` once, then this
# app serves both the API and the dashboard on one port. Mounted LAST so
# every API route above wins first; unknown non-API paths fall back to
# index.html (BrowserRouter deep links like /forecast must not 404).
# Static files are deliberately outside the X-API-Key gate — the bundle is
# not secret; the key protects the API responses, which the SPA still has
# to present the key to read. Skipped silently when no dist/ exists (dev:
# the Vite dev server on :5173 serves the frontend instead).
from pathlib import Path as _Path

from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as _StarletteHTTPException


class _SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        # newer Starlette RAISES HTTPException(404) for a missing file
        # rather than returning a 404 response — handle both. Must catch
        # Starlette's class: fastapi.HTTPException is a SUBCLASS of it,
        # and StaticFiles raises the parent
        try:
            resp = await super().get_response(path, scope)
        except _StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            return await super().get_response("index.html", scope)
        if resp.status_code == 404:
            resp = await super().get_response("index.html", scope)
        return resp


_FRONTEND_DIST = _Path(__file__).resolve().parents[3] / "frontend" / "dist"
if (_FRONTEND_DIST / "index.html").exists():
    app.mount("/", _SPAStaticFiles(directory=_FRONTEND_DIST, html=True),
              name="frontend")
