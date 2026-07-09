# ONE Consolidation / Forecasting Portal — Claude Context

## What this is
AI procurement decision-support system for PC parts buying at サードウェーブ.

**Active product (current focus):** demand forecasting + advisory dashboard,
in `app/` (`onefcst` backend + React/Vite frontend). Reads the ONE 部品予測/
部品マスター system, SAP stock, and sales-plan sources read-only from NAS;
produces per-SKU forecasts, order suggestions, a CPU/MB/VGA/Case compatibility
gate, and human-adjustment/accuracy tracking. No SAP write-back, no ML models
(statistical baseline + backtest harness only) — humans always confirm.

**Frozen POC (do not extend, kept for reference):** `consolidation/` — the
original Phase-1 Excel-automation portal (4 functions: consolidate+reconcile,
anomaly detection, purchasing checks, SAP stock-paste automation). Completed
and frozen 2026-07-03 when Phase-1 scope changed to forecasting-only. Only
touch this if explicitly asked to work on the legacy Excel-automation flow.

---

## How to run (active product — `app/`)

```bash
# Backend (FastAPI)
cd app/backend && python3 -m uvicorn onefcst.api.main:app --host 0.0.0.0 --port 8600

# Frontend (React + Vite)
cd app/frontend && npm run dev -- --host 0.0.0.0 --port 5173

# Rebuild DB from NAS sources (only if source files changed; ~4 min, full drop+rebuild)
cd app/backend && python3 -m onefcst.build

# Generate a forecast run after a DB rebuild
cd app/backend && python3 -m onefcst.forecasting   # or POST /forecasts/run

# Tests
cd app/backend && python3 -m pytest tests/ -q
```

Start both servers with `run_in_background: true` (not bare `nohup` — see
project memory `running-portal` for why nohup fails in this environment).

### Frozen POC (`consolidation/`, reference only)

```bash
cd consolidation && python3 -m uvicorn app:app --host 0.0.0.0 --port 8501
```

---

## Architecture — `app/` (active product)

```
app/
  IMPLEMENTATION_PLAN.md   ← build log + approved architecture flow (read this for pipeline detail)
  data/                    onefcst.db (SQLite, gitignored) + exports
  backend/
    onefcst/
      config.py            source paths (env-overridable), TIER1_CATEGORIES
      models.py             full schema: items, aliases, plan values, stock state,
                            forecasts, adjustments, compat rules, accuracy scores…
      db.py                SQLite engine/session (NullPool — see constraint #2 below)
      build.py             M1: ingest all sources → unified item registry, Gate A report
      scheduler.py          background poll loop that triggers forecasting.generate()
      forecasting.py        per-SKU forecast generation (5-candidate + combo competition)
      forecast_views.py     read-side API assembly: summary/list/detail, adjustment
                            application (_latest_adjustments/_apply_adjustments), order
                            suggestions
      tier2_forecasting.py  Tier-2 (derived/attach-rate) forecasting for non-planned categories
      planner_accuracy.py   planner-forecast snapshotting + historical planner-accuracy reference
      ingestion/            per-source parsers (excel.py helpers, one_master.py, sap.py,
                            planning1.py/planning4.py, plan1_chain.py, note_plan.py,
                            notepc_forecast.py/notepc_seasonality.py, keisuu.py,
                            mb_procurement.py, name_crosswalk.py, subfiles.py)
      registry/build.py     alias resolution across naming systems, conflict detection
      engine/               pure functions: baseline.py (forecast candidates), seasonality.py,
                            chain.py, transition.py, weekly.py, tier2.py (attach-rate),
                            compat.py (BOM/compatibility), stock_projection.py (order
                            suggestions), fva.py (forecast-value-added scoring)
      advisory/             the only writable layer: adjustments.py, rules.py,
                            llm_rules.py (AI-drafted compat rules), notifications.py
      verify/               accuracy/QA gates: reconciliation.py (Gate A), parity.py
                            (Gate B, cell-level), backtest.py (Gate C), fva.py (Gate D),
                            mismatch.py (take-or-pay mismatch gate + acceptance test),
                            order_suggestions.py (M5 plausibility check — NOTE: its
                            _future_demand() is a separate code path from
                            forecast_views.order_suggestions and must be kept in sync
                            manually; check both when fixing demand-calculation bugs)
      export/excel.py       formatted Excel export per view
      api/main.py            FastAPI routes (see below), auth.py (API-key dependency),
                            schemas.py
    tests/                 pytest suite, one file per subsystem/fix
  frontend/
    src/
      pages/               Dashboard.tsx, Forecast.tsx, Mismatch.tsx, Accuracy.tsx,
                           Adjustments.tsx, CompatReview.tsx, Notifications.tsx
      api/client.ts, types.ts
      i18n/                ja/en translations
```

**Key API routes** (`app/backend/onefcst/api/main.py`): `/items`, `/forecasts/run`
(POST), `/forecasts/summary`, `/forecasts` (list), `/forecasts/{item_id}` (detail),
`/forecasts/order-suggestions`, `/compat-rules` (+confirm/reject/bulk-confirm/draft-llm),
`/adjustments`, `/notifications`, `/reports/{mismatch,source-reconciliation,backtest,
fva,planner-accuracy,adjustment-rate}`, `/export/{mismatch,compat-rules,adjustments,
backtest,forecasts,audit-log}`. Auth is a shared `X-API-Key` header, app-level
dependency in `main.py`; disabled (open) unless `ONEFCST_API_KEY` is set — do not
deploy this off localhost without setting it and tightening the wildcard CORS config.

**Two-tier forecast architecture**: planning dept (経営企画部) plans only
VGA/CPU/MB/Case — the demand-driving categories (`engine/baseline.py` +
`forecasting.py`, planning-facing). Purchasing derives everything else (RAM,
PSU, cables, mouse/KB, FAN, SSD/HDD, OS, 梱包, etc.) via attach-rate/compat
rules (`tier2_forecasting.py`, `engine/tier2.py`, `engine/compat.py`,
purchasing-facing). The compatibility gate (`engine/compat.py` +
`advisory/llm_rules.py` human-confirm flow) keeps Tier-2 quantities tracking
Tier-1 plans.

**Method**: no-ML per-SKU competition — 5 candidates (seasonal / damped-trend /
flat3 / flat6 / median6) plus a `combo` (equal-weight average) compete on each
SKU's own 6-month walk-forward hold-out; winner forecasts 12 months. Reports
both an `overall` (6-month blended) and `near_term` (M+1) WAPE, since accuracy
is highly horizon-dependent and buyers act on the near-term number.

---

## Architecture — `consolidation/` (frozen POC, reference only)

```
consolidation/
  config_all.py       ← per-file column layout for all 17 ONE files (the "spec")
  consolidate.py      ← parses 部品詳細 sheets → SQLite (consolidated.db)
  reconcile.py        ← accuracy gate: parsed vs expected SKU counts, PASS/WARN/FAIL
  anomalies.py        ← data-entry anomaly detection (negative stock, outliers, etc.)
  purchasing.py       ← overdue POs, price/supplier history, incoming-excess warnings
  master_desktop.py   ← auto-generates 部品予測 master from 17 sub-files
  stock_paste.py      ← maps SAP 実在庫 → ONE 綽名, builds paste Excel
  excel_writer.py     ← exports formatted 統合出力 Excel
  app.py              ← FastAPI portal (routes: / /refresh /anomalies /procurement /master /stock)
  index.html           ← single-page dashboard (6 tabs, lazy-loaded, ja/en toggle) — the
                        UI users interact with here, NOT app/frontend
  consolidated.db     ← SQLite, generated on first run (gitignored)
```

ONE source files live at: `../ONE/ONE/` (relative to `consolidation/`), set by
`BASE` in `config_all.py`, override with `ONE_SOURCE_DIR`.

### Portal tabs (`consolidation/index.html`, frozen POC only)

| Tab | Route | Load | Description |
|-----|-------|------|-------------|
| サマリー | embedded | eager | Category roll-up |
| 統合一覧 | embedded | eager | All 534 SKUs, searchable |
| 照合レポート | embedded | eager | Per-file PASS/WARN/FAIL |
| 異常検出 | /anomalies | lazy | Data-entry anomaly findings |
| 発注・価格 | /procurement | lazy | Overdue POs, price history, incoming-excess |
| マスター自動化 | /master | lazy | 部品予測 master auto-generation |
| 在庫自動設定 | /stock | lazy | SAP 実在庫 → ONE 綽名 match coverage |

---

## Key data concepts

**Join key**: `綽名` (あだ名) — the short product nickname used across all ONE
files. Every SKU in 部品詳細 has a 綽名; it is also the join key to the SAP
stock file. Both `app/`'s registry and the frozen `consolidation/` reuse this
same join key — `app/backend/onefcst/ingestion/name_crosswalk.py` and
`registry/build.py` are the current (`app/`) equivalent of `consolidation/`'s
綽名 matching logic.

**ONE file layout** (部品詳細 sheet, relevant to both `app/`'s ingestion and
the frozen `consolidation/` parser): 4-row repeating blocks —
```
row 0: 生産計画  | [綽名]   | [date cols →]
row 1: 購買計画  | [safety] |
row 2: 在庫残高  |          | [stock balance per date]
row 3: 在庫日数  |          | [stock days per date]
```
Column offsets differ per file — captured in `consolidation/config_all.py`
(`label_col / name_col / first_date_col`); `app/backend/onefcst/ingestion/`
has its own per-source parsers for the same underlying files.

**17 ONE sub-files, 534 SKUs total** — the parts-level data scope both apps
ingest (CPU, MB, VGA, RTX, MEM, SSD, HDD, PSU, Case, Case_other, Case_梱包,
FAN, ODD, NotePC, OS, Mouse&KB, 国内一般品; full file list unchanged from
the original Phase-1 spec, see `consolidation/config_all.py`). Note: `app/`'s
forecasting scope is broader than this table implies — the two-tier
architecture above also covers ONE計画's master 部品予測/部品マスター sheets,
NotePC lifecycle data, and ①②③④ sales-plan sources.

**SAP stock file** (`260629在庫.xlsx`-style weekly ZM13 extract, `あですな`
→ `SBRCODE | SBRNAME | あだ名 | SYOCODE | SYONAME | QTYの合計`): joins to
ONE 綽名; multiple SYOCODE rows per あだ名 are summed. Blank-あだ名 rows are
matched via a SYONAME/keyword heuristic in both `consolidation/stock_paste.py`
and `app/backend/onefcst/ingestion/name_crosswalk.py`.

---

## CRITICAL constraints

1. **Never rewrite ONE source files with openpyxl.** A no-op load+save strips
   all formula cached values (proven: stock balance goes `6695 → None`). Only
   ever create NEW workbooks. `consolidation/stock_paste.py` exists specifically
   to avoid this. `app/`'s ingestion is read-only by design for the same reason.

2. **`app/data/onefcst.db` and `consolidation/consolidated.db` are always
   regenerated** — never treat either as source of truth across sessions.
   Rebuild via `python3 -m onefcst.build` (app) or `python3 consolidate.py`
   (consolidation) / hit `/refresh`.

3. **Snapshot / staleness logic**: both apps pick the first date column ≥
   today per source file; if a file is stale (no column reaches today), they
   fall back to the last column and flag it (`snapshot_stale=True` in
   `consolidation`). `app/`'s forecast detail exposes `snapshot_date` on
   `ForecastDetail.stock` but the frontend does not currently render it —
   check before assuming staleness is visible to users.

4. **`app/backend/onefcst/verify/order_suggestions.py`'s `_future_demand()`
   is a separate code path from `forecast_views.order_suggestions()`** — a fix
   to demand-calculation, snapshot-selection, or adjustment-application logic
   in one does NOT automatically apply to the other. Check both when touching
   order-suggestion correctness.

5. **No incoming-PO / in-transit quantity is modeled** in
   `engine/stock_projection.py` — order suggestions assume zero incoming
   supply. A buyer using `/forecasts/order-suggestions` should still check
   purchasing's own PO tracker before ordering.

---

## Dependencies

`app/backend`: `fastapi uvicorn sqlalchemy openpyxl pyxlsb pydantic` (see
`app/backend/requirements.txt`). `app/frontend`: React + TS + Vite (see
`app/frontend/package.json`). `consolidation/` (frozen POC): `fastapi uvicorn
openpyxl pyxlsb python-docx`.

---

## What is NOT in this repo (gitignored, lives on NAS/OneDrive)

- `ONE/` — all 17 ONE Excel source files
- SAP stock exports (`*在庫.xlsx`, ZM13 extracts) — replace with the latest each Monday
- `販売計画*.xlsx` — sales forecast files
- `app/data/onefcst.db`, `consolidation/consolidated.db`, `__pycache__/`,
  generated export Excel files, `app/frontend/node_modules`
