# ONE Consolidation / Forecasting Portal — Claude Context

## Where things are

Root holds only what must be there (`CLAUDE.md`, `Dockerfile`,
`docker-compose.yml`, dotfiles). Code in `app/`, deployment in `deploy/`,
everything written for humans in `docs/` — see `docs/README.md` for the map.
Current plan of record: `docs/SSO_TO_GOLIVE_TASKS.md`. Security posture and open
findings: `docs/SECURITY_AUDIT_2026-07-30.md`.

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

`.github/workflows/ci.yml` runs exactly that on both dialects (plus
`npm run build` and a `docker build`) on every push/PR. `deploy.yml` then
deploys to the DGX from a self-hosted runner once CI is green on `main` —
runbook in `deploy/README-cd.md`; never give that workflow a `pull_request`
trigger, since it executes on our own hardware. It runs on a bare
checkout, so the 27 tests needing NAS-built data skip — a green run is
**329 passed / 27 skipped**. The skip guard is `tests/realdata.py`
(`HAS_BUILT_DB`), which asks the database whether `items` has rows; do not
go back to `DB_PATH.exists()` — an empty SQLite file is created as a side
effect of connecting, so that guard let 24 tests run against an empty DB.

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

### Maintenance mode is for deployments, not rebuilds

Policy set 2026-07-30, and the two paths must stay separate:

- **Deployment** (you push new code/features) → turn maintenance mode on by
  hand: `python3 -m onefcst.maintenance on "<message>"`, `… off` after.
  `MaintenanceGate` shows a full-screen notice. Nothing sets this flag
  automatically, so "the site is in maintenance" always means a person did it.
- **NAS rebuild** (weekly scheduler, or the UI button) → **must not** touch
  maintenance mode. `rebuild_preserving.run()` deliberately no longer reads or
  writes that flag. Users instead get a non-blocking 「再構築中」 strip driven
  by `/build/rebuild/status`, which keeps answering while the tables are gone
  because that state lives in memory, not the DB.

Consequences to keep in mind: data endpoints DO return 500 intermittently
during the drop→restore window (verified — the strip is what explains it, and
any 5xx makes the gate re-check at once via `SERVER_ERROR_EVENT`). And on
Postgres, a long in-flight report query can now block `drop_all` until
`ONEFCST_DROP_LOCK_TIMEOUT`; that aborts safely with nothing dropped, and the
remedy is to enable maintenance mode for that rebuild deliberately.

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

**SAP stock** comes from the weekly ZM13 drop `★週頭在庫(ZM13)` on the NAS
(`data/sap/週頭在庫_ZM13/在庫_MMDD.xlsx` locally, mirrored by `nas_sync`). The
NEWEST file is "current stock", the older ones are history — one code path for
both (`build._aggregate_zm13`), so they cannot drift. Resolution order per row:
品目 (=SYOCODE) as 商品コード → 品目テキスト as ONE綽名 → the 製品名称読みかえ
crosswalk. `SAP在庫抽出.accdb` is **no longer read** (2026-07-30): it was the
same weekly pull in a second format, agreeing on 496/497 items.

**Legacy SAP stock file** (`260629在庫.xlsx`-style weekly ZM13 extract, `あですな`
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
`app/backend/requirements.txt`). `app/frontend`: React + TS + Vite (see
`app/frontend/package.json`).

`mdbtools` is **no longer needed to run the system** (2026-07-30): stock comes
from the weekly ZM13 xlsx, not `SAP在庫抽出.accdb`. It stays installed in CI only
because `tests/test_sap_crosswalk_integration.py` still exercises the crosswalk
through the legacy Access parser; the Docker image no longer ships it.

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
