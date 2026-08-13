# Project Overview — ONE Consolidation / Forecasting Portal

A map of what this system is, how data flows through it end to end, and
where each piece of logic lives in the codebase. For day-to-day dev
commands, deployment gotchas, and hard constraints, see `CLAUDE.md` at the
repo root — this file is the narrative walkthrough version.

## What it is

An AI-assisted procurement decision-support system for PC-parts buying at
サードウェーブ. It reads read-only data from three places on the NAS — ONE's
部品予測/部品マスター Excel system, SAP stock (weekly ZM13 extract), and
企画部's sales-plan files — and produces per-SKU demand forecasts,
order-quantity suggestions, a CPU/MB/VGA/Case compatibility gate, and
human-adjustment/accuracy tracking. It never writes back to SAP, uses no ML
(statistical baseline + backtest only), and every suggestion requires human
confirmation before it means anything.

There was an earlier Phase-1 Excel-automation POC (`consolidation/`) —
removed from this repo 2026-07-21, no longer part of this phase.

## End-to-end data flow

```
NAS (ONE Excel files, SAP ZM13, 企画部 sales-plan files)
        │  nas_sync.py mirrors → data/ (or ONEFCST_NAS_MODE=mount)
        ▼
ingestion/  (per-source parsers: one_master, sap, planning1/4, plan1_chain,
             note_plan, notepc_forecast/seasonality, keisuu, mb_procurement,
             name_crosswalk, subfiles)
        ▼
registry/build.py  — alias resolution across naming systems (join key: 綽名),
                      conflict detection
        ▼
build.py (M1)  — ingest everything into a unified item registry, writes
                  Gate A reconciliation report
        ▼
onefcst.db  (SQLite dev / Postgres prod — db.py picks backend from DATABASE_URL)
        ▼
forecasting.py  — per-SKU forecast generation:
   engine/baseline.py   5 candidates (seasonal / damped-trend / flat3 /
                         flat6 / median6) + combo (equal-weight average)
   engine/seasonality.py, chain.py, transition.py, weekly.py
   → each SKU's own 6-month walk-forward backtest picks a winner
   → winner forecasts 12 months forward
   tier2_forecasting.py + engine/tier2.py  — attach-rate derived forecasts
                         for everything NOT planned by 企画部 (RAM, PSU,
                         cables, FAN, SSD/HDD, OS, 梱包, etc.)
   engine/compat.py     — BOM/compatibility gate keeping Tier-2 quantities
                         tracking Tier-1 (CPU/MB/VGA/Case) plans
        ▼
advisory/  — the ONLY writable layer: adjustments.py (human overrides),
             rules.py + llm_rules.py (AI-drafted compat rules, human-confirm
             flow), notifications.py
        ▼
forecast_views.py  — read-side API assembly: joins forecast + stock +
                      adjustments into summary/list/detail/order-suggestions
engine/stock_projection.py  — projects stock forward against demand, times
                      an order to MOQ + lead time (no incoming-PO modeling)
        ▼
verify/  — QA gates: reconciliation.py (Gate A), parity.py (Gate B,
           cell-level), backtest.py (Gate C), fva.py (Gate D, forecast-
           value-added scoring), mismatch.py (take-or-pay mismatch gate)
        ▼
api/main.py (FastAPI)  →  frontend (React/Vite)
```

Two-tier architecture is the core mental model: **企画部 plans** CPU/MB/VGA/Case
directly (demand-driving categories) → everything else is **derived** via
attach-rate/compat rules for purchasing.

## Backend code structure (`app/backend/onefcst/`)

| Path | Role |
|---|---|
| `config.py` | Source paths (env-overridable), `TIER1_CATEGORIES`, `find_planning_file()` |
| `models.py` | Full SQLAlchemy schema — items, aliases, plan values, stock state, forecasts, adjustments, compat rules, accuracy scores |
| `db.py` | Engine/session, SQLite or Postgres depending on `DATABASE_URL`, `pool_pre_ping` |
| `build.py` | M1 ingestion pass → unified registry + Gate A report |
| `rebuild_preserving.py` | Preserving wrapper around `build` — keeps human data (adjustments/compat rules/audit log) across a rebuild; backs up via `pg_dump -Fc` on Postgres |
| `scheduler.py` | Background poll loop triggering `forecasting.generate()` |
| `forecasting.py` | Per-SKU forecast generation, 5-candidate + combo competition |
| `forecast_views.py` | Read-side API assembly — summary/list/detail, adjustment application, order suggestions |
| `tier2_forecasting.py` | Tier-2 (derived/attach-rate) forecasting |
| `planner_accuracy.py` | Snapshots planner forecasts, tracks historical planner accuracy |
| `ingestion/` | One parser module per NAS source |
| `registry/build.py` | Alias resolution, conflict detection |
| `engine/` | Pure functions — no I/O — baseline, seasonality, chain, transition, weekly, tier2, compat, stock_projection, fva |
| `advisory/` | Writable layer — adjustments, rules, llm_rules, notifications |
| `verify/` | Accuracy/QA gates (see pipeline above) |
| `export/excel.py` | Formatted Excel export per view |
| `api/main.py` | FastAPI routes |
| `api/auth.py` | Shared `X-API-Key` dependency |
| `api/entra.py` | Entra ID JWT verification (RS256 against tenant JWKS) |
| `api/schemas.py` | Pydantic request/response models |
| `tests/` | One file per subsystem/fix, run via pytest |

Known cross-cutting gotcha baked into the code: `verify/order_suggestions.py`'s
`_future_demand()` is a **separate code path** from
`forecast_views.order_suggestions()` — a fix to demand-calc logic in one
doesn't automatically apply to the other (confirmed divergence: one sums raw
`PlanValue` over 4 months, the other consumes the full 12-month pipeline
output).

## Frontend code structure (`app/frontend/src/`)

| Path | Role |
|---|---|
| `pages/` | `Dashboard`, `Forecast`, `Mismatch`, `Accuracy`, `Adjustments`, `CompatReview`, `Notifications`, `Landing` |
| `components/Layout.tsx` | App shell — header, sidebar nav, the `header-logo-text` wordmark |
| `api/client.ts` | Axios instance, `BASE_URL` from `VITE_API_BASE`, `X-API-Key` header |
| `api/types.ts` | TS types mirroring backend Pydantic schemas |
| `i18n/translations.ts` | ja/en string table (`brand_name`, `dash_api_error`, etc.) |
| `auth/msal.ts`, `auth/AuthGate.tsx` | MSAL PKCE flow for Entra SSO (public client, no secret) |

## Auth

Two modes, switched by whether `ONEFCST_ENTRA_TENANT_ID`/`ONEFCST_ENTRA_CLIENT_ID`
are set:
- **Unset** (dev default): shared `X-API-Key` header only.
- **Set**: every request must carry a Microsoft token. SPA is a public
  client doing PKCE in-browser; backend only verifies (RS256 + aud/iss/tid/exp
  against tenant JWKS). Who may *do what* isn't implemented yet —
  `Identity.groups`/`.roles` are populated but unread.

## Database

`db.py` picks backend from `DATABASE_URL`. Unset → SQLite at
`app/data/onefcst.db`. Set (`postgresql+psycopg://…`) → Postgres 17 with an
explicit pool. Both run the same test suite (`ONEFCST_TEST_DATABASE_URL`).
Portability rules that matter for any new query code: no SQLite-only
functions (`julianday`/`strftime`), always pin NULL ordering explicitly
(`.nulls_first()`/`.nulls_last()` — SQLite and Postgres default oppositely).

## Deployment

One multi-stage Docker image serves API + built SPA on 8600, Postgres
alongside, nginx on the host (not in the container). One uvicorn worker
only (two would run two schedulers). Code changes need a container restart.
`ONEFCST_API_KEY` rotation needs an image rebuild (compiled into the JS
bundle). CI (`.github/workflows/ci.yml`) runs the test suite on both DB
dialects + `npm run build` + `docker build` on every push; `deploy.yml`
deploys to the DGX from a self-hosted runner only after CI is green on
`main`.

Maintenance mode is deliberately **not** touched by NAS rebuilds — only a
human toggles it for actual deployments (`onefcst.maintenance on/off`).
Rebuilds instead show a non-blocking 「再構築中」 strip driven by
`/build/rebuild/status`.
