# ONE Consolidation / Forecasting Portal — Claude Context

## What this is
AI procurement decision-support system for PC parts buying at サードウェーブ.

**Active product:** demand forecasting + advisory dashboard, in `app/`
(`onefcst` backend + React/Vite frontend). Reads the ONE 部品予測/部品マスター
system, SAP stock, and sales-plan sources read-only from NAS; produces
per-SKU forecasts, order suggestions, a CPU/MB/VGA/Case compatibility gate,
and human-adjustment/accuracy tracking. No SAP write-back, no ML models
(statistical baseline + backtest harness only) — humans always confirm.

**Note:** the original Phase-1 Excel-automation POC (`consolidation/` —
consolidate+reconcile, anomaly detection, purchasing checks, SAP
stock-paste automation) was removed from this repo 2026-07-21; the user
keeps a separate backup outside this repo. It is not part of this phase —
if asked about it, it no longer exists here.

---

## How to run (active product — `app/`)

```bash
# Backend (FastAPI)
cd app/backend && python3 -m uvicorn onefcst.api.main:app --host 0.0.0.0 --port 8600

# Frontend (React + Vite)
cd app/frontend && npm run dev -- --host 0.0.0.0 --port 5173

# Rebuild DB from NAS sources (only if source files changed; ~4 min, full drop+rebuild).
# Once real human data exists (Adjustments/CompatRules/audit log), use the
# preserving wrapper instead of bare `onefcst.build` — see CRITICAL constraint #2.
cd app/backend && python3 -m onefcst.rebuild_preserving   # or: python3 -m onefcst.build (POC/no data to lose)

# Generate a forecast run after a DB rebuild
cd app/backend && python3 -m onefcst.forecasting   # or POST /forecasts/run

# Tests
cd app/backend && python3 -m pytest tests/ -q
```

Start both servers with `run_in_background: true` (not bare `nohup` — see
project memory `running-portal` for why nohup fails in this environment).

### Database backend — SQLite *or* PostgreSQL

`db.py` picks the backend from `DATABASE_URL`; unset it and you are on the
SQLite file at `app/data/onefcst.db` (the dev/rollback path, unchanged).
Set it (`postgresql+psycopg://…`) and the same code runs on Postgres 17 with
an explicit 10+20 pool and `pool_pre_ping`. Both are supported and both must
keep working — the test suite runs on either via `ONEFCST_TEST_DATABASE_URL`:

```bash
cd app/backend && ONEFCST_TEST_DATABASE_URL=postgresql+psycopg://… python3 -m pytest tests/ -q
```

Portability rules that apply to *all* new query code:
- **Never** use SQLite-only SQL functions (`julianday`, `strftime`) — they
  raise `UndefinedFunction` on Postgres. Fallback branches count; one of these
  shipped untested for months.
- **Always** pin NULL ordering explicitly with `.nulls_first()` /
  `.nulls_last()`. SQLite sorts NULLs first on ASC, Postgres last — in the
  "last row wins" loops this codebase uses, that silently inverts which row
  wins (it reached order quantities via `forecast_views._stock_by_item`).
- String sort order also differs (byte order vs locale); display-only.

One-time SQLite → Postgres data migration (dry-run by default, verifies by
content not count):

```bash
cd app/backend && python3 -m onefcst.migrate_to_postgres            # dry run
cd app/backend && python3 -m onefcst.migrate_to_postgres --commit
```

### Docker deployment

`Dockerfile` + `docker-compose.yml` build one multi-stage image that serves
both the API and the built SPA on 8600, with Postgres alongside; nginx stays
on the *host*. Runbook, NAS mount unit, nginx config and the oauth2-proxy SSO
overlay are all in `deploy/` — start from `deploy/README-docker.md`.

Deployment gotchas that have already bitten:
- **One uvicorn worker only.** Two would run two schedulers and could drop
  the database concurrently. Never add `--workers`.
- **Code changes need a container restart** — the scheduler holds imported
  modules in memory, so an auto-rebuild can resurrect a bug already fixed on
  disk.
- **Rotating `ONEFCST_API_KEY` requires an image rebuild** — `VITE_API_KEY`
  is compiled into the JS bundle.
- **Check the frontend with `npm run build`, not `vite build`.**
  `tsc --noEmit -p tsconfig.json` checks nothing here (solution file with
  references only) and will mask real type errors.
- `ONEFCST_NAS_MODE=mount` makes a missing/empty NAS mount an error rather
  than the old silent skip (`nas_sync.py`).

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
deploy this off localhost without setting it. CORS defaults to localhost-only
(`config.CORS_ORIGINS`, no more wildcard) — set `ONEFCST_CORS_ORIGINS` to the
real deployed dashboard origin(s) before hosting anywhere else.

**Two-tier forecast architecture**: planning dept (企画部) plans only
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

## Key data concepts

**Join key**: `綽名` (あだ名) — the short product nickname used across all ONE
files. Every SKU in 部品詳細 has a 綽名; it is also the join key to the SAP
stock file. `app/backend/onefcst/ingestion/name_crosswalk.py` and
`registry/build.py` handle this matching.

**ONE file layout** (部品詳細 sheet): 4-row repeating blocks —
```
row 0: 生産計画  | [綽名]   | [date cols →]
row 1: 購買計画  | [safety] |
row 2: 在庫残高  |          | [stock balance per date]
row 3: 在庫日数  |          | [stock days per date]
```
Column offsets differ per file — captured in
`app/backend/onefcst/ingestion/subfile_config.py`
(`label_col / name_col / first_date_col`).

**17 ONE sub-files, 534 SKUs total** — the parts-level data scope `app/`
ingests (CPU, MB, VGA, RTX, MEM, SSD, HDD, PSU, Case, Case_other, Case_梱包,
FAN, ODD, NotePC, OS, Mouse&KB, 国内一般品; full file list in
`ingestion/subfile_config.py`). Note: `app/`'s
forecasting scope is broader than this table implies — the two-tier
architecture above also covers ONE計画's master 部品予測/部品マスター sheets,
NotePC lifecycle data, and ①②③④ sales-plan sources.

**SAP stock file** (`260629在庫.xlsx`-style weekly ZM13 extract, `あですな`
→ `SBRCODE | SBRNAME | あだ名 | SYOCODE | SYONAME | QTYの合計`): joins to
ONE 綽名; multiple SYOCODE rows per あだ名 are summed. Blank-あだ名 rows are
matched via a SYONAME/keyword heuristic in
`app/backend/onefcst/ingestion/name_crosswalk.py`.

---

## CRITICAL constraints

1. **Never rewrite ONE source files with openpyxl.** A no-op load+save strips
   all formula cached values (proven: stock balance goes `6695 → None`). Only
   ever create NEW workbooks. `app/`'s ingestion is read-only by design for
   this reason.

2. **`app/data/onefcst.db` is always regenerated** — never treat it as
   source of truth across sessions. Rebuild via `python3 -m onefcst.build`
   or `python3 -m onefcst.rebuild_preserving`.

3. **Snapshot / staleness logic**: `app/` picks the first date column ≥
   today per source file; if a file is stale (no column reaches today), it
   falls back to the last column and flags it. `app/`'s forecast detail
   exposes `snapshot_date` on `ForecastDetail.stock` but the frontend does
   not currently render it — check before assuming staleness is visible to
   users.

4. **`app/backend/onefcst/verify/order_suggestions.py`'s `_future_demand()`
   is a separate code path from `forecast_views.order_suggestions()`** — a fix
   to demand-calculation, snapshot-selection, or adjustment-application logic
   in one does NOT automatically apply to the other. Check both when touching
   order-suggestion correctness. Confirmed 2026-07-14 the divergence is wider
   than just data source: `_future_demand()` sums raw `PlanValue` rows over
   only 4 months, while `forecast_views.order_suggestions()` consumes the
   full 12-month `make_forecast` pipeline output — a horizon mismatch, not
   just a recomputation difference.

5. **No incoming-PO / in-transit quantity is modeled** in
   `engine/stock_projection.py` — order suggestions assume zero incoming
   supply. A buyer using `/forecasts/order-suggestions` should still check
   purchasing's own PO tracker before ordering.

---

## Dependencies

`app/backend`: `fastapi uvicorn sqlalchemy openpyxl pyxlsb pydantic` (see
`app/backend/requirements.txt`), plus the system package `mdbtools` (`sudo
apt-get install -y mdbtools`) for reading the live SAP stock source
(`SAP在庫抽出.accdb`, an Access DB — see `ingestion/sap.py`); soft
dependency, falls back to an xlsx snapshot if unavailable. `app/frontend`:
React + TS + Vite (see `app/frontend/package.json`).

For the Postgres backend: `psycopg[binary]`, plus the `pg_dump` client binary
— `rebuild_preserving.backup_db()` shells out to `pg_dump -Fc` and now
**raises** on every failure mode instead of returning `"skipped"`. That means
a failed backup aborts the rebuild by design; if rebuilds start failing, check
`pg_dump` availability and disk space first.

---

## What is NOT in this repo (gitignored, lives on NAS/OneDrive)

- `ONE/` — all 17 ONE Excel source files
- SAP stock exports (`*在庫.xlsx`, ZM13 extracts) — replace with the latest each Monday
- `販売計画*.xlsx` — sales forecast files
- `app/data/onefcst.db`, `__pycache__/`, generated export Excel files,
  `app/frontend/node_modules`
