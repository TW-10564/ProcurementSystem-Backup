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
    # compatibility attributes (AI/spec-filled later)
    socket = Column(String)
    chipset = Column(String)
    mem_type = Column(String)
    form_factor = Column(String)
    # commercial attributes (部品マスター / 製品一覧)
    moq = Column(Float)
    lead_time_days = Column(Float)
    min_stock_days = Column(Float)
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


class AuditLog(Base):
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True)
    who = Column(String)
    what = Column(String, nullable=False)
    before = Column(Text)
    after = Column(Text)
    at = Column(DateTime, default=datetime.utcnow)


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
