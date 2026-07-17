"""onefcst API — the advisory layer's HTTP surface (M4).

Read-only against every NAS source (enforced by construction: this app only
ever imports ingestion/engine/verify modules for READS; the only writes here
are to CompatRule.status, Adjustment, and AuditLog — all in the app's own
SQLite store). No endpoint here writes to Excel/ONE/SAP.

Run:  cd app/backend && uvicorn onefcst.api.main:app --host 0.0.0.0 --port 8600
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from .. import scheduler as scheduler_mod
from ..config import CORS_ORIGINS
from ..db import get_session
from ..models import Item, CompatRule, Adjustment, Suggestion
from ..advisory import rules as rules_svc
from ..advisory import adjustments as adj_svc
from ..advisory import notifications as notif_svc
from ..advisory import discontinuation_review as discontinuation_svc
from .auth import require_api_key
from .schemas import (CompatRuleOut, ConfirmRequest, RejectRequest,
                      EditRuleRequest, AdjustmentIn, AdjustmentOut,
                      NotificationOut, DiscontinuationSuggestionOut)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = scheduler_mod.start()
    yield
    if task:
        task.cancel()


app = FastAPI(title="onefcst — Forecasting & Procurement Decision Support",
             description="Phase-1 advisory API. Read-only against all NAS "
             "sources; writes only to this app's own database.",
             lifespan=lifespan, dependencies=[Depends(require_api_key)])

# CORS: the SPA (Vite dev server) runs on a different origin. Defaults to
# localhost-only (see config.CORS_ORIGINS) — set ONEFCST_CORS_ORIGINS to the
# deployed dashboard's real origin(s) before hosting this anywhere else.
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"],
                   allow_headers=["*"])


def db():
    session = get_session()
    try:
        yield session
    finally:
        session.close()


@app.get("/health")
def health():
    return {"status": "ok", "time": datetime.utcnow().isoformat()}


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
    except Exception as e:
        rebuild_status_mod.fail(entry, repr(e))


@app.post("/build/rebuild")
async def trigger_rebuild():
    import asyncio
    if rebuild_status_mod.is_running():
        raise HTTPException(status_code=409, detail="A rebuild is already running")
    entry = rebuild_status_mod.start("manual")
    asyncio.create_task(_run_rebuild_in_background(entry))
    return {"status": "started"}


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
def confirm_compat_rule(rule_id: int, body: ConfirmRequest, session: Session = Depends(db)):
    try:
        r = rules_svc.confirm_rule(session, rule_id, body.author)
    except rules_svc.RuleNotFound as e:
        raise HTTPException(404, str(e))
    items = {i.id: i.canonical_name for i in session.query(Item).all()}
    return CompatRuleOut(id=r.id, part_a_id=r.part_a_id, part_a_name=items.get(r.part_a_id, "?"),
                         part_b_id=r.part_b_id, part_b_name=items.get(r.part_b_id, "?"),
                         relation=r.relation, confidence=r.confidence, origin=r.origin,
                         status=r.status, confirmed_by=r.confirmed_by, note=r.note)


@app.post("/compat-rules/{rule_id}/reject", response_model=CompatRuleOut)
def reject_compat_rule(rule_id: int, body: RejectRequest, session: Session = Depends(db)):
    try:
        r = rules_svc.reject_rule(session, rule_id, body.author, body.reason)
    except rules_svc.RuleNotFound as e:
        raise HTTPException(404, str(e))
    items = {i.id: i.canonical_name for i in session.query(Item).all()}
    return CompatRuleOut(id=r.id, part_a_id=r.part_a_id, part_a_name=items.get(r.part_a_id, "?"),
                         part_b_id=r.part_b_id, part_b_name=items.get(r.part_b_id, "?"),
                         relation=r.relation, confidence=r.confidence, origin=r.origin,
                         status=r.status, confirmed_by=r.confirmed_by, note=r.note)


@app.patch("/compat-rules/{rule_id}", response_model=CompatRuleOut)
def edit_compat_rule(rule_id: int, body: EditRuleRequest, session: Session = Depends(db)):
    """Correct a draft rule's own judgement (confidence/relation/note)
    before deciding confirm/reject — e.g. the AI got the confidence wrong,
    or a reviewer wants to record their own reasoning. Only allowed on
    status='draft' rules; a confirmed/rejected rule is a settled decision
    (see advisory/rules.py::edit_rule's docstring)."""
    try:
        r = rules_svc.edit_rule(session, rule_id, body.author, relation=body.relation,
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
def confirm_discontinuation(suggestion_id: int, body: ConfirmRequest,
                            session: Session = Depends(db)):
    try:
        s = discontinuation_svc.confirm_suggestion(session, suggestion_id, body.author)
    except discontinuation_svc.SuggestionNotFound as e:
        raise HTTPException(404, str(e))
    except discontinuation_svc.SuggestionNotEditable as e:
        raise HTTPException(409, str(e))
    return _discontinuation_out(s)


@app.post("/discontinuation-suggestions/{suggestion_id}/reject",
         response_model=DiscontinuationSuggestionOut)
def reject_discontinuation(suggestion_id: int, body: RejectRequest,
                           session: Session = Depends(db)):
    try:
        s = discontinuation_svc.reject_suggestion(session, suggestion_id, body.author, body.reason)
    except discontinuation_svc.SuggestionNotFound as e:
        raise HTTPException(404, str(e))
    except discontinuation_svc.SuggestionNotEditable as e:
        raise HTTPException(409, str(e))
    return _discontinuation_out(s)


@app.post("/compat-rules/bulk-confirm")
def bulk_confirm(author: str, threshold: float = 0.9, session: Session = Depends(db)):
    n = rules_svc.bulk_confirm_high_confidence(session, author, threshold)
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
def create_adjustment(body: AdjustmentIn, session: Session = Depends(db)):
    try:
        a = adj_svc.record_adjustment(session, item_id=body.item_id, month=body.month,
                                      old_value=body.old_value, new_value=body.new_value,
                                      scope=body.scope, author=body.author,
                                      reason=body.reason, factor=body.factor)
    except ValueError as e:
        raise HTTPException(400, str(e))
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
@app.get("/notifications", response_model=list[NotificationOut])
def get_notifications(limit: int = 50, session: Session = Depends(db)):
    return notif_svc.recent_activity(session, limit=limit)


@app.get("/notifications/summary")
def get_notification_summary(session: Session = Depends(db)):
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
def run_forecast(horizon: int = 6, session: Session = Depends(db)):
    """Generate a new forecast run (writes only to the app DB). Every run
    validates itself out-of-sample and stores the accuracy with the run."""
    from ..forecasting import generate
    return generate(session, horizon=horizon)


@app.get("/forecasts/summary")
def forecast_summary(session: Session = Depends(db)):
    from .. import forecast_views
    return forecast_views.summary(session)


@app.get("/forecasts")
def list_forecasts(q: str = "", category: str | None = None, method: str | None = None,
                   limit: int = 50, offset: int = 0, session: Session = Depends(db)):
    from .. import forecast_views
    return forecast_views.list_forecasts(session, q=q, category=category, method=method,
                                         limit=limit, offset=offset)


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
# the dashboard requests both on every mount, so cache until the next build.
# Inputs only change when onefcst.build rewrites the DB — key on the DB mtime.
_report_cache: dict[str, tuple[float, object]] = {}


def _cached(key: str, compute):
    from ..config import DB_PATH
    import os
    mtime = os.path.getmtime(DB_PATH)
    hit = _report_cache.get(key)
    if hit and hit[0] == mtime:
        return hit[1]
    result = compute()
    _report_cache[key] = (mtime, result)
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
def export_forecasts_xlsx():
    from ..export.excel import export_forecasts
    path = export_forecasts()
    return FileResponse(path, filename=path.name, media_type=XLSX_MEDIA)


@app.get("/export/audit-log")
def export_audit_log_xlsx():
    from ..export.excel import export_audit_log
    path = export_audit_log()
    return FileResponse(path, filename=path.name, media_type=XLSX_MEDIA)
