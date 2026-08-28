"""SQLAlchemy ORM — the app's own store (SQLite now, PostgreSQL-portable).

Schema follows POC_BUILD_PLAN.md §1. Sources are never written; this DB holds
ingested copies (with lineage), engine outputs, and the advisory layer.
"""
from datetime import datetime

from sqlalchemy import (Boolean, Column, Date, DateTime, Float, ForeignKey,
                        Integer, String, Text, UniqueConstraint)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Item(Base):
    """One canonical part or PC model (the unified registry spine)."""
    __tablename__ = "items"
    id = Column(Integer, primary_key=True)
    canonical_name = Column(String, nullable=False, index=True)   # 綽名 preferred
    kind = Column(String, nullable=False, default="part")         # part | pc_model
    category = Column(String)            # 大分類 (CPU/グラフィックカード/…)
    subcategory = Column(String)         # 中分類 (Intel/AMD/ATX/…)
    tier = Column(Integer)               # 1=planned by planning dept, 2=derived
    lifecycle = Column(String)           # 新規/発売中/EOL在庫あり/完全終息/開発中止
    successor_id = Column(Integer, ForeignKey("items.id"))
    eol_flag = Column(Boolean, default=False)
    # Purchasing's own "won't reorder" gray-cell marking in 部品詳細 (see
    # ingestion/no_reorder_flag.py) — a human decision that can PRECEDE
    # eol_flag, not a replacement for it. Only readable for the 6 category
    # files stored as .xlsx (VGA/RTX/MEM/SSD/PSU/Mouse&KB); .xlsb categories
    # (CPU/MB/etc — read via pyxlsb, which exposes no cell styling at all)
    # always get False here regardless of their real status.
    marked_no_reorder = Column(Boolean, default=False)
    # compatibility attributes (AI/spec-filled later)
    socket = Column(String)
    chipset = Column(String)
    mem_type = Column(String)
    form_factor = Column(String)
    # commercial attributes (部品マスター / 製品一覧)
    moq = Column(Float)
    lead_time_days = Column(Float)          # 部品マスター's static estimate
    # Real observed order->delivery lead time (median days, from completed
    # 購買計画 PO history — see ingestion/lead_time.py). Found 2026-07-14:
    # for many items this diverges sharply from lead_time_days above (e.g.
    # a real SSD showing 112 real days vs master's static 7) — order timing
    # should prefer this when there's enough real history to trust it (see
    # forecast_views.effective_lead_time), not silently replace the master
    # field, so both remain visible for a human to compare.
    observed_lead_time_days = Column(Float)
    observed_lead_time_samples = Column(Integer)
    # Genuine per-item FLOOR — 部品マスター's own 最小在庫 when purchasing set
    # one directly on this item ("never let this item run below N days").
    min_stock_days = Column(Float)
    # Category-level CEILING — 在庫の持ち方's 在庫日数上限 ("don't let this
    # category build up past N days"), applied via registry/build.py's
    # category lookup. FIX 2026-08-20: this used to get merged into
    # min_stock_days above and used as a reorder floor — the opposite of
    # what the source column means — see stock_projection.suggest_order()'s
    # docstring. Kept in its own field so a ceiling can never again be
    # silently reused as someone's floor.
    max_stock_days = Column(Float)
    supplier_a = Column(String)
    supplier_b = Column(String)
    supplier_c = Column(String)
    exclusive_flag = Column(Boolean, default=False)   # 当社専用 (take-or-pay risk)
    takeorpay_n1 = Column(String)        # あり / 要交渉 / なし
    takeorpay_n2 = Column(String)
    takeorpay_n3 = Column(String)
    release_date = Column(Date)          # 販売開始
    eol_date = Column(Date)              # 終息予定
    created_at = Column(DateTime, default=datetime.utcnow)
    aliases = relationship("ItemAlias", back_populates="item")


class ItemAlias(Base):
    """A name for an item in one of the source naming systems."""
    __tablename__ = "item_aliases"
    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False)
    alias = Column(String, nullable=False, index=True)
    source_system = Column(String, nullable=False)  # ONE綽名/商品コード/④ﾏｽﾀ/ノートマスタ/…
    item = relationship("Item", back_populates="aliases")
    __table_args__ = (UniqueConstraint("alias", "source_system"),)


class Snapshot(Base):
    """One ingestion of one source file (lineage root)."""
    __tablename__ = "snapshots"
    id = Column(Integer, primary_key=True)
    source_file = Column(String, nullable=False)
    sheet = Column(String)
    file_mtime = Column(DateTime)
    checksum = Column(String)
    ingested_at = Column(DateTime, default=datetime.utcnow)
    row_count = Column(Integer)
    gate_status = Column(String)         # PASS / WARN / FAIL
    gate_note = Column(Text)


class PlanValue(Base):
    """A plan/forecast number as found in a source, with full lineage.

    level: planning4 (④ONE計画貼り付け) | subfile (販売計画/生産予測) |
           master (ONE計画 部品予測) | note (ノート在庫使用計画)
    """
    __tablename__ = "plan_values"
    id = Column(Integer, primary_key=True)
    snapshot_id = Column(Integer, ForeignKey("snapshots.id"), nullable=False)
    item_id = Column(Integer, ForeignKey("items.id"), index=True)
    raw_name = Column(String)            # name as it appeared (pre-resolution)
    month = Column(Date, nullable=False)
    level = Column(String, nullable=False)
    value = Column(Float)
    source_cell = Column(String)         # e.g. 部品予測!DH42


class Actual(Base):
    __tablename__ = "actuals"
    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey("items.id"), index=True)
    month = Column(Date, nullable=False)
    qty = Column(Float)
    source = Column(String)              # ZP21 / ④全部合算 / ②CSV / SAP
    snapshot_id = Column(Integer, ForeignKey("snapshots.id"))
    __table_args__ = (UniqueConstraint("item_id", "month", "source"),)


class WeeklyActual(Base):
    """Real per-item WEEKLY shipped quantity — the raw grain
    ingestion/sap_shipments.py reads before aggregating into Actual's
    monthly buckets. Kept as a separate table (not a finer-grained Actual)
    because Actual's month grain is load-bearing elsewhere (FVA scoring,
    forecast_views recency sort both key off calendar months matching
    Forecast.month) — this table exists ONLY to feed the plan-vs-actual
    weekly comparison view (forecast_views.plan_vs_actual), which
    disaggregates the monthly Forecast the same way engine/weekly.py's
    near-term UI view already does, then compares week-for-week."""
    __tablename__ = "weekly_actuals"
    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey("items.id"), index=True)
    week_start = Column(Date, nullable=False)
    qty = Column(Float)
    source = Column(String)
    __table_args__ = (UniqueConstraint("item_id", "week_start", "source"),)


class StockState(Base):
    """Current stock snapshot per item (ONE 在庫残高 row + SAP physical)."""
    __tablename__ = "stock_states"
    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey("items.id"), index=True)
    raw_name = Column(String)
    snapshot_date = Column(Date)
    one_balance = Column(Float)          # ONE 在庫残高 (snapshot col)
    one_stock_days = Column(Float)       # ONE 在庫日数
    safety_days = Column(Float)
    sap_qty = Column(Float)              # SAP physical stock (ZM13)
    snapshot_id = Column(Integer, ForeignKey("snapshots.id"))


class ForecastRun(Base):
    __tablename__ = "forecast_runs"
    id = Column(Integer, primary_key=True)
    method_version = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    note = Column(Text)


class Forecast(Base):
    __tablename__ = "forecasts"
    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("forecast_runs.id"), nullable=False)
    item_id = Column(Integer, ForeignKey("items.id"), index=True)
    month = Column(Date, nullable=False)
    baseline = Column(Float)
    method = Column(String)              # which candidate won this SKU's hold-out
    __table_args__ = (UniqueConstraint("run_id", "item_id", "month"),)


class Adjustment(Base):
    """Recorded human override on top of a baseline (the adjustment layer)."""
    __tablename__ = "adjustments"
    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("forecast_runs.id"))
    item_id = Column(Integer, ForeignKey("items.id"))
    month = Column(Date)
    old_value = Column(Float)
    new_value = Column(Float)
    factor = Column(Float)               # for global/systematic adjustments
    scope = Column(String)               # global / category / sku
    reason = Column(Text)
    author = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)


class CompatRule(Base):
    __tablename__ = "compat_rules"
    id = Column(Integer, primary_key=True)
    part_a_id = Column(Integer, ForeignKey("items.id"), nullable=False)
    part_b_id = Column(Integer, ForeignKey("items.id"), nullable=False)
    relation = Column(String, nullable=False)   # fits / requires
    confidence = Column(Float)
    origin = Column(String)              # ai_spec_heuristic / llm / history / human
    status = Column(String, default="draft")    # draft / confirmed / rejected
    confirmed_by = Column(String)
    confirmed_at = Column(DateTime)
    note = Column(Text)                  # drafting rationale (e.g. LLM reasoning)
    __table_args__ = (UniqueConstraint("part_a_id", "part_b_id", "relation"),)


class Suggestion(Base):
    """AI proposal awaiting human review (compat / ramp / order)."""
    __tablename__ = "suggestions"
    id = Column(Integer, primary_key=True)
    kind = Column(String, nullable=False)
    payload = Column(Text)               # JSON
    confidence = Column(Float)
    status = Column(String, default="draft")
    reviewed_by = Column(String)
    reviewed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)


class OrderAction(Base):
    """Manual order-status tracking for order suggestions (Phase-1: no PO/
    ERP integration — a human records what they actually did). One ACTIVE
    (status='ordered') row per item at a time; callers must check for an
    existing active row before creating a new one (same discipline
    advisory/discontinuation_review.py's create_draft already documents for
    Suggestion). status='received'/'dismissed' rows are historical."""
    __tablename__ = "order_actions"
    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False)
    status = Column(String, nullable=False, default="ordered")  # ordered | received | dismissed
    qty_ordered = Column(Float)
    expected_arrival_date = Column(Date)
    supplier = Column(String)
    note = Column(Text)
    ordered_by = Column(String)
    ordered_at = Column(DateTime, default=datetime.utcnow)
    received_by = Column(String)
    received_at = Column(DateTime)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True)
    # Indexed because the feed's every query sorts by `at` and filters by
    # `who`. Unindexed, a date-sorted scan is what makes a log page feel
    # broken once it holds tens of thousands of rows — and the monthly archive
    # keeps it smaller than that only if somebody remembers to run it.
    who = Column(String, index=True)
    what = Column(String, nullable=False)
    before = Column(Text)
    after = Column(Text)
    at = Column(DateTime, default=datetime.utcnow, index=True)


class UserAccess(Base):
    """What a signed-in employee is allowed to do (the RBAC roster).

    Three roles, matching how the work is actually divided:

      * ``admin``      — the system owner. Rebuild from the NAS, re-run
                         forecasts, maintenance mode, and adjustments on ANY
                         item. Everything is still written to the audit log.
      * ``purchasing`` — the 7–10 購買本部 buyers. May adjust only the parts in
                         their own scope (see UserScope) — normally the 1–3 ONE
                         sub-files they personally maintain.
      * ``planning``   — 企画部. May tune only the four Tier-1 categories they
                         actually plan (CPU / VGA / MB / Case), across the board
                         rather than per sub-file.

    Anyone signed in but absent from this table can READ everything and write
    nothing. Read stays global on purpose: scoping writes prevents mistakes,
    while scoping reads would just create information silos — and the whole
    point of the audit log is that everyone can see what everyone changed.

    Why here and not in Entra: the three ROLES could live in Entra as app
    roles, and may later. Per-person part ownership cannot — it would need one
    app role per sub-file and an IT ticket every time two buyers swap files.
    Keeping the roster here means the admin manages it in the portal, in the
    same place they see who has been signing in.
    """
    __tablename__ = "user_access"
    id = Column(Integer, primary_key=True)
    email = Column(String, nullable=False, unique=True, index=True)
    role = Column(String, nullable=False)        # admin | purchasing | planning
    note = Column(Text)                          # e.g. "MEM/SSD担当"
    granted_by = Column(String)                  # email of the admin who set it
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class UserScope(Base):
    """Which parts a purchasing member owns — one row per (person, sub-file).

    `scope_key` is the ONE sub-file's short name as it appears in
    ingestion/subfile_config.py: CPU, MB, VGA, RTX, MEM, SSD, HDD, PSU, Case,
    Case_other, Case_梱包, FAN, ODD, NotePC, OS, Mouse&KB, 国内一般品.

    Ignored for admin (everything) and planning (the Tier-1 categories, which
    are a property of the item, not an ownership assignment).
    """
    __tablename__ = "user_scopes"
    id = Column(Integer, primary_key=True)
    email = Column(String, nullable=False, index=True)
    scope_key = Column(String, nullable=False)
    granted_by = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("email", "scope_key"),)


class ItemScopeOverride(Base):
    """Hand-assigned owner for an item the sub-file lineage cannot place.

    About a third of forecastable items belong to no sub-file: Tier-2 parts the
    attach-rate engine derives (サプライ用品, ベアボーンキット, マウス…) and
    items the manager compiles straight into ONE計画.xlsm's master sheet. They
    are live items, not discontinued — they simply have no sub-file owner, so
    without this table nobody but an admin could ever adjust them.

    Keyed by ``canonical_name``, NOT item_id: build.py drops and recreates
    every Item row, so an id-keyed override would silently repoint at a
    different part after a rebuild — the same failure mode
    rebuild_preserving.py already documents for adjustments. The name is the
    stable identifier across rebuilds.
    """
    __tablename__ = "item_scope_overrides"
    id = Column(Integer, primary_key=True)
    canonical_name = Column(String, nullable=False, unique=True, index=True)
    scope_key = Column(String, nullable=False)
    assigned_by = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)


class UserProfile(Base):
    """Who an email address belongs to, as Entra/Graph currently describes them.

    Every human record in this database — adjustments, confirmed compat rules,
    order actions, the audit log — is keyed by EMAIL, deliberately: a display
    name is neither unique nor stable, so it must never be the identifier
    (see Identity.audit_author). But an email is not what a buyer wants to read
    in a list of who changed what, and 綽名-style mail aliases like
    `m_balamurukan@` do not tell you who that is at a glance.

    So this table is a cache, not a source of truth: email → the person's
    current Azure details, refreshed from the verified token (and from Graph,
    when the SPA forwards it) each time they sign in. Two consequences that are
    the whole point:

      * Renaming someone in Azure updates every historical record's DISPLAYED
        name at their next sign-in, while the stored email keys never move.
      * Nothing here is trusted for authorization. It is display metadata,
        written only for the caller's OWN verified identity, so one user can
        never edit how another user is shown.

    Rebuilds must preserve this table like any other human data
    (rebuild_preserving.py) — it is not derivable from the NAS sources.
    """
    __tablename__ = "user_profiles"
    id = Column(Integer, primary_key=True)
    # Lowercased, and the join key back to every `author`/`who`/`confirmed_by`
    # string in this schema.
    email = Column(String, nullable=False, unique=True, index=True)
    oid = Column(String, index=True)     # Entra object id: stable across renames
    display_name = Column(String)
    given_name = Column(String)
    surname = Column(String)
    job_title = Column(String)
    department = Column(String)
    office_location = Column(String)
    # Where the details came from: "token" (ID-token claims only) or "graph"
    # (the SPA forwarded Microsoft Graph's richer record). Graph must not be
    # silently overwritten by the thinner token data — see api/main.py.
    source = Column(String, default="token")
    updated_at = Column(DateTime, default=datetime.utcnow)
    last_seen_at = Column(DateTime, default=datetime.utcnow)


class PlannerSnapshot(Base):
    """Archived copy of the planner's OWN future-month forecast, captured at
    a point in time, so it can be graded later against the real result.

    Why this exists: 部品予測 overwrites each month's forecast cell with the
    actual once that month closes — the source has no memory of what the
    planner predicted before the fact. Every forecast run archives the
    planner's current future values here (idempotent per snapshot_date) so
    that months from now, once real outcomes land, the planner's forecast
    accuracy becomes independently measurable — the same way the system
    measures its own via AccuracyScore/holdout."""
    __tablename__ = "planner_snapshots"
    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey("items.id"), index=True)
    month = Column(Date, nullable=False)          # the target month being forecast
    value = Column(Float, nullable=False)          # planner's figure for that month
    snapshot_date = Column(Date, nullable=False, index=True)  # when this was captured
    __table_args__ = (UniqueConstraint("item_id", "month", "snapshot_date"),)


class AccuracyScore(Base):
    __tablename__ = "accuracy_scores"
    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("forecast_runs.id"))
    item_id = Column(Integer, ForeignKey("items.id"))
    category = Column(String)
    month = Column(Date)
    level = Column(String)               # baseline / adjusted / their_plan
    mape = Column(Float)
    wape = Column(Float)                 # 6-month blended (full-horizon) WAPE
    wape_m1 = Column(Float)              # near-term (M+1-only) WAPE — FIX_PLAN
                                          # M1: the number a buyer actually acts
                                          # on given lead times; promoted to the
                                          # primary per-item confidence signal
    bias = Column(Float)
