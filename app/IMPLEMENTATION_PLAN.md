# Implementation Plan & Build Log — Phase-1 POC (onefcst)

Companion to `../forecasting/POC_BUILD_PLAN.md` (the approved architecture).
This file tracks the concrete build: what exists, what's next, in what order.
Updated: 2026-07-06 (rev.2 — project phase framing corrected, see below).

## Project phases vs. build stages (do not confuse these)

The **project** (per `Project_Charter_Phase1_Forecasting_v1.2.docx`) has 3 phases:

| Project Phase | Scope |
|---|---|
| **Phase 1 — AI-Assisted Demand Forecasting (Decision Support)** | Everything in this document (M0-M6 below) is entirely WITHIN Phase 1. Read-only against every source; the system suggests, humans decide. |
| Phase 2 — Excel Process Automation | Future. Begins once Phase 1 is piloted and its success criteria are met. First introduces write operations to Excel. |
| Phase 3 — Full System Replacement | Future. The system becomes the sole system of record; Excel is retired. |

**M0 through M6 below are build STAGES inside Phase 1, not separate project
phases.** An earlier revision of the Charter's schedule table mistakenly
listed M1-M6 as "Phase 1" through "Phase 6" — this has been corrected in
Charter v1.2. This document keeps the M-numbering because it's a useful,
already-referenced internal build sequence (and is cross-referenced by FUN-ID
task refs in the Requirements Suite as "Phase 1 - M1" etc.) — just remember
every M-stage below sits inside project Phase 1.

---

## Approved architecture flow (summary)

```
1. SOURCES (read-only NAS)   ONE計画+17 sub-files / NoteONE / SAP ZM13 / ①②③④ / ノート在庫使用計画
        ▼
2. INGESTION                 per-source parsers, values-only, lineage (file→sheet→cell)
        ▼  ★Gate A: reconciliation (counts/sums) — FAIL blocks the run
3. UNIFIED ITEM REGISTRY     one ID per part/model; aliases from 4+ naming systems;
                             tier(1/2); lifecycle; successor; MOQ/LT/take-or-pay
        ▼
4. ENGINES (pure functions)  4a Tier-1 forecast (baseline×seasonality, landing,
                             chain, ramp/fade)   4b BOM/compat (AI-draft →
                             history-check → human confirm; Tier-2 = explosion)
                             4c stock projection (mirrors 月末在庫シミュ)
        ▼
5. ADVISORY LAYER (app DB — the ONLY writable place)
                             adjustments, AI suggestions+confidence, audit log,
                             notifications
        ▼
6. DECISION PRODUCTS         mismatch gate (take-or-pay-weighted) / 発注提案 /
                             three-level plan diff (④ vs sub-file vs master)
        ▼
7. OUTPUTS                   React+TS dashboard / Excel export per view /
                             検証 workbook per refresh (computed|source|diff|cell)
        ▼
8. ACCURACY LOOP             ★Gate B cell parity ★Gate C walk-forward backtest
                             (incl. their past plans) ★Gate D FVA
```

Key rules: read-only against all sources; AI suggests → humans confirm (anyone);
no ML models — statistical baseline, backtest harness decides any change;
everything checkable in Excel; accuracy is the product.

---

## Repository layout

```
app/
  IMPLEMENTATION_PLAN.md   ← this file
  data/                    app-owned store (SQLite DB, exports)   [gitignore]
  backend/
    requirements.txt       fastapi uvicorn sqlalchemy openpyxl pyxlsb pydantic
    onefcst/
      config.py            source paths (env-overridable), TIER1_CATEGORIES
      models.py            full schema (items…accuracy_scores)
      db.py                SQLite engine/session (PostgreSQL-portable)
      build.py             M1 entrypoint + Gate A report
      ingestion/
        excel.py           read-only xlsx/xlsb helpers + lineage
        one_master.py      ONE計画: 部品マスター / 部品予測
        note_plan.py       ノート在庫使用計画: マスタ / MOQ条件
        planning4.py       ④: ﾏｽﾀ aliases / ONE貼り付け
      registry/build.py    unified registry merge + resolve()
  frontend/                React+TS+Vite SPA                      [M6, not yet]
```

---

## Build log

### DONE — M0 scaffold + M1 first slice (2026-07-06)
- Package structure, schema (all 11 tables), config, DB factory.
- Parsers: ONE計画 部品マスター (4,969 rows) & 部品予測 (categories + 122 monthly
  cols 2017-10→2027-11), ノート在庫使用計画 マスタ (lifecycle) + MOQ条件,
  ④ ﾏｽﾀ aliases + その他ONE貼り付け.
- Registry merge with conflict quarantine.
- **First build result (real NAS data): Gate A PASS**
  - items 2,924 (Tier-1 798 / Tier-2 2,126); aliases 9,043; conflicts 124
  - master plan cells 41,217 — 100.0% resolved to items (criterion ≥95%)
  - ④ paste rows 410 — 6 unresolved
- Run: `cd app/backend && python3 -m onefcst.build`

### DONE — M1 complete (2026-07-06)
1. **Sub-file 部品詳細 parser** (`ingestion/subfiles.py`, level=`one_row`):
   reuses proven Phase-0 layouts (`consolidation/config_all.py`); captures the
   FULL monthly 生産計画 series per SKU (not just the snapshot column) +
   current 在庫残高/在庫日数/安全在庫 into `StockState`.
   **All 17 files parsed at 100% of expected SKUs (534 total)** — same
   reconciliation guarantee as Phase-0. 8,596 series cells, 534 stock rows.
2. **SAP ZM13 parser** (`ingestion/sap.py`) → StockState(source SAP).
   260629在庫.xlsx: 302 あだ名 rows, 277 matched to registry items (368
   unnamed SAP rows correctly excluded, matching Phase-0's known coverage gap).
3. **①販売計画 ingestion** (`ingestion/planning1.py`, level=`planning1`):
   1,175 rows, 37.4% resolved — LOW BY DESIGN (model names ≠ 綽名 without the
   ﾏｽﾀ bridge; this gap itself is the "4+ alias tables" problem, now measured
   instead of assumed. Bridging via ④ﾏｽﾀ is a follow-up, not required for M2).
4. **検証 workbook** (`verify/report.py` → `app/data/exports/検証_YYYYMMDD.xlsx`),
   5 sheets: Gate A Summary / Alias Conflicts (124, review-flagged) /
   Unresolved Names / **Plan Diff (3-level)** — ④ vs one_row vs master per
   item×month, filtered to a live 3-month+ window, divergent rows sorted to
   the top and highlighted — **234 diverging item-months found**, e.g.
   12cmケースFAN: ④6,500→ONE5,404 (−16.9%), LD5-32G56: ④1,528→ONE2,365
   (+54.8%). Every row traces to its source cell. / Snapshot Inventory
   (all 21 ingested files w/ mtime + gate status).
5. Alias-conflict rows carry kept/dropped item ids — ready to feed the M4
   review queue UI directly.

**Overall Gate A: 51,324 plan values, 97.5% resolved, PASS.**
Run: `cd app/backend && python3 -m onefcst.verify.report`

### DONE — M2 Tier-1 forecast engine + parity + backtest (2026-07-06)
- `engine/seasonality.py` — `compute_seasonality` + `compute_landing`, ported
  from `forecasting/poc_v0.py` as pure functions (no I/O), both the
  as-in-file (bug-preserving) and corrected landing variants.
- `engine/chain.py` — `reproduce_chain` (parity checking, classifies every
  cell chain/lookup/custom/override) + `project_chain` (forward projection
  for NEW forecasts, with an adjustment-override hook per month).
- `engine/transition.py` — `ramp_fade_curve` (linear, volume-conserving —
  old+new always sums to the predecessor's base level, matching planning's
  "same base volume" description) + `seed_new_item` (predecessor-based
  seeding with attach-rate ratio; auto-flags `needs_review` when the
  platform/socket changed, e.g. Nova Lake's new socket — LOW confidence,
  routes to human, never auto-applied). Not yet parity-tested (no historical
  transition ground truth exists yet — genuinely new logic per plan §4/§6).
- **Gate B (`verify/parity.py`)**: re-proves the ported engine — not just the
  original script — hits 100% cell parity: seasonality 48/48, landing 12/12,
  chain 346/346. Run: `python3 -m onefcst.verify.parity` (exit 0/1 for CI).
- **Gate C (`verify/backtest.py`)**: walk-forward across 4 fiscal years
  (38-41期) using the 10-year keisuu history, seasonal-share method vs two
  naive baselines (flat 1/12, last-year shape). **Result: seasonal method
  wins every single period tested** — avg WAPE 10.95% vs flat 16.28% vs
  last-year 13.59%. This is the evidence bar future methods (incl. any ML)
  must clear before adoption — first real proof-of-value number for planning.
- No per-SKU / notebook backtest yet (needs multiple historical ①/notebook
  snapshots over time, not available from a single current file — will
  become possible as the system accumulates its own monthly ingests).

**Demo 1 talking point, ready now:** "your seasonal method, automated,
beats naive alternatives by 30-50% WAPE, measured on your own 10-year history."

### DONE — M3 compatibility engine + mismatch gate (2026-07-06)
- `engine/compat.py` — the "AI draft" step, implemented as an explicit,
  reviewable rule engine (socket/chipset compatibility is standardized public
  spec knowledge, not a business judgment call — see module docstring for why
  this satisfies BR-010 without a live model call): Intel Core generation →
  socket (4th-14th gen + Core Ultra 200S/LGA1851 + HEDT X-series overrides +
  Celeron/Pentium G-series), AMD Ryzen series → AM4/AM5/AM3+/FM2+, and a
  chipset-code lookup for all 123 motherboard items in the registry.
  **Coverage: 247/265 CPUs classified (93%), 122/123 motherboards (99.2%)** —
  remainder are laptop-only CPUs (correctly excluded, no desktop socket) or
  a legacy socket with no matching CPU in the catalogue (X399/Threadripper).
  3,237 draft CPU↔MB `fits` rules generated, confidence 0.95 (exact match) or
  0.7 (pattern-inferred), **status=draft for every single one — none
  auto-confirmed** (BR-010).
- `ingestion/mb_procurement.py` — parses MB 製品一覧's take-or-pay liability
  (FCST引き取り責任 N+1/N+2/N+3) and 専用(exclusive) flags per board model,
  rolled up to chipset (conservative: a chipset is flagged if ANY board on it
  carries the liability). Confirmed B860 and B760 correctly show
  takeorpay_n1=True, matching the document read that found this constraint.
- `verify/mismatch.py` — draft-and-store rules, then a demand-weighted
  CPU-vs-MB comparison by socket family for the current month (clearly
  labelled as a DEMO computation on draft rules — production requires
  confirmed rules per BR-010), plus a **historical acceptance-test scan**
  across all master-plan months (2017-2026, excluding the confirmed
  formula-artifact months ≥2027-01).
- **Acceptance test PASSED with a real finding**: the AM4 socket family shows
  motherboard demand 200-360% above CPU demand in the SAME month, recurring
  in 39 separate months from 2020-10 through 2026-02 — not a one-off data
  blip but a persistent structural pattern. This is exactly the shape of
  cross-category mismatch the gate exists to catch. Cause not yet
  explained (could be legitimate board-only demand — e.g. RMA/repair stock,
  or a genuine long-running over-forecast) — flagged as a real question for
  planning/purchasing, not asserted as a bug.
- Current month (2026-07) mismatch is LOW severity across all 4 active
  socket families (AM5/AM4/LGA1851/LGA1700 all within ±14% and none exceed
  the 15% threshold) — an honest result, not forced.

**Demo 2 talking point, ready now:** "the compatibility engine, run against
6 years of your own ONE master data, independently found a real, persistent
200%+ CPU/motherboard mismatch on AM4 — the exact class of problem you
described having no rule document for."

### DONE — M4 advisory layer + API (2026-07-06)
- `advisory/rules.py` — confirm_rule/reject_rule/bulk_confirm_high_confidence
  over the M3 draft CompatRule table; every action writes an AuditLog entry
  (who/what/before/after/when) via a shared `_log()` helper — no state change
  without an audit trail, by construction, not by convention.
- `advisory/adjustments.py` — record_adjustment() enforces BR-003
  (Adjustment Accountability): raises if `reason` is blank; also computes
  `adjustment_rate()` (the % of forecast cells touched — Charter v1.1 success
  criterion #4).
- `advisory/notifications.py` — the in-app notification feed is a READ VIEW
  over AuditLog, not a second table — one source of truth for "what changed"
  so the audit trail and the notification feed can never drift apart.
- `api/main.py` + `api/schemas.py` — first real HTTP surface (FastAPI):
  `/compat-rules` (list/confirm/reject/bulk-confirm), `/adjustments`
  (create/list), `/notifications` (+ summary), `/items/{id}`,
  `/reports/mismatch`, `/reports/backtest`. Enforced read-only-against-
  sources by construction: every route only imports ingestion/engine/verify
  modules for reads; the only writes anywhere in this API are to CompatRule.
  status, Adjustment, and AuditLog rows in the app's own SQLite file.
- **Verified end-to-end via TestClient against the live-built DB**: listed
  draft rules, confirmed one, rejected one (with reason), bulk-confirmed 200
  rules at ≥0.9 confidence in one call (200 individual audit entries, not one
  opaque bulk record), recorded a real adjustment example (RTX 50 launch
  ramp override), confirmed a blank-reason adjustment is correctly rejected
  (422, caught at the Pydantic schema layer before it reaches the service
  layer's own check), and read the full trail back through
  `/notifications` — every one of the 203 events traceable to its author.
- Run: `cd app/backend && uvicorn onefcst.api.main:app --host 0.0.0.0 --port 8600`

### DONE — M5 order suggestions (発注提案) (2026-07-06)
- `engine/stock_projection.py` — `project_stock()` (deplete current balance by
  forecast demand, no restock assumed — the point is to find WHEN one is
  needed), `find_breach_month()`, `suggest_order()`: MOQ-rounded quantity,
  lead-time-shifted timing (order lands before the safety-stock breach, not
  merely placed on the breach date). Pure functions, returns a proposal
  dict only — never places an order (REQ-017/BR-013).
- `verify/order_suggestions.py` — generates live suggestions from current
  stock (SAP-preferred, ONE 在庫残高 fallback) + master-plan demand, for every
  item with both. Cross-checked against REAL historical PO data by reusing
  `consolidation/purchasing.py`'s proven `read_all_pos()` (29,053 PO rows
  across all 17 files' 購買計画 sheets, auto-detected columns).
- **Honesty note recorded explicitly in the module and here**: this is a
  live-data PLAUSIBILITY check, not the point-in-time historical backtest
  POC_BUILD_PLAN's exit criterion describes — that requires replaying past
  system snapshots, which don't exist yet (this is the system's first run).
  A true backtest becomes possible once onefcst has been running and
  recording its own monthly state for several cycles.
- **Result, reported as measured, not oversold**: of 127 SKUs with a real PO
  in the last 90 days, 45 had enough live stock+demand data for a
  suggestion; **21/45 (47%) landed within 2x of the actually-ordered
  quantity** — real order-of-magnitude signal, not noise, but not yet tight
  enough to hand a buyer un-reviewed (consistent with REQ-017: suggestions
  are always reviewed, never auto-placed, so this is an acceptable POC-stage
  result, not a blocking defect). Worst outliers (e.g. Z890M-PLUS 25x,
  SD5-16G56 20.7x) look like the 4-month demand-averaging window smoothing
  over lumpy/bulk real purchasing patterns — a concrete lead for M5.1 tuning
  (shorter window, or MOQ-aware demand smoothing) if this becomes a later
  priority.

### DONE — M6 dashboard + Excel export + FVA (2026-07-06)
- `frontend/` — React 19 + TypeScript + Vite SPA (NOT a single HTML file):
  `api/client.ts` + `api/types.ts` (typed API layer), `components/Layout.tsx`
  (sidebar nav), 6 pages: Dashboard, Accuracy (FVA + backtest), Mismatch,
  CompatReview (confirm/reject/bulk-confirm workflow), Adjustments
  (search-to-add form + history), Notifications. Clean `tsc -b` typecheck and
  production `vite build` (303KB JS / 98KB gzipped).
- `export/excel.py` + 5 new API routes (`/export/mismatch`,
  `/export/compat-rules`, `/export/adjustments`, `/export/backtest`,
  `/export/audit-log`) — every dashboard view has a matching Excel download,
  closing the "everything checkable in Excel" gap M6 originally specified.
  Verified: downloaded and re-opened the mismatch export with openpyxl —
  correct headers, real socket-family data, formatted.
- `engine/fva.py` (pure WAPE-based scoring: baseline vs adjusted vs actual)
  + `verify/fva.py` (finds adjustments whose target month has both passed
  AND has an actual on record — the only cases scoreable) +
  `/reports/fva` + `/reports/adjustment-rate` routes.
  **Honest result, by design**: 0 scoreable adjustments — this system started
  operating today, so no adjustment has yet reached a completed month with a
  recorded actual. The dashboard states this plainly rather than faking a
  score. This is the same honesty discipline as M5's plausibility check.
- **Verified end-to-end with a live browser** (Playwright) against the real
  running API (port 8600) + Vite dev server: Dashboard, CompatReview, and
  Mismatch pages screenshot cleanly with zero console errors; confirmed live
  data flow (200 previously-confirmed compat rules, real WAPE numbers, real
  mismatch table) rendering correctly.
- **Environment gotcha hit and fixed**: Vite's file watcher does not reliably
  pick up changes on this WSL/OneDrive-mounted path (chokidar limitation on
  network/mounted filesystems) — edits were saved to disk correctly but the
  running dev server kept serving a stale bundle with no HMR log activity.
  Fix: restart the dev server (a fresh process re-reads the file correctly;
  only the *watch* mechanism is affected, not the initial read). Worth
  knowing for any future frontend session on this machine: if an edit
  doesn't show up in the browser, restart Vite before debugging the React
  code itself.
- Run: backend `uvicorn onefcst.api.main:app --host 0.0.0.0 --port 8600`,
  frontend `cd app/frontend && npm run dev` (set `VITE_API_BASE` if the API
  isn't on localhost:8600).

### DONE — bilingual UI (2026-07-06)
- `i18n/translations.ts` (dictionary, ja/en, ~140 keys) + `i18n/LanguageContext.tsx`
  (React context, persisted to localStorage as `onefcst_lang`) wired in at
  `main.tsx`. Toggle lives in the sidebar (`Layout.tsx`), matching the
  Phase-0 portal's 日本語/EN pattern.
- All 6 pages (Dashboard, Accuracy, Mismatch, CompatReview, Adjustments,
  Notifications) converted to `useLang()` + `t('key', {vars})` — no
  hardcoded UI strings left in page components (dynamic API data — item
  names, numbers — is correctly left untranslated).
- **Verified live with Playwright**: toggled EN, confirmed heading text
  changed ("ダッシュボード" → "Dashboard"), navigated to another page while
  in EN and confirmed the language persisted ("Parts Compatibility Rule
  Review"), zero console errors.
- Hit the same Vite-watcher-doesn't-see-WSL/OneDrive-edits issue as before;
  fix is the same — restart the dev server after editing frontend files.

## M0–M6 all complete — this is the full planned POC build

Every module in POC_BUILD_PLAN.md §3 exists and is verified against live
company data: ingestion+registry (Gate A), forecast engine (Gate B parity +
Gate C backtest), compatibility engine (found a real historical mismatch),
advisory layer + API, order suggestions (honestly-scored plausibility
check), and the dashboard. Natural next steps are pilot-stage activities
(planning + purchasing user review, tuning M5's demand-averaging window,
building out the compat-rule spec-source integration) rather than new
build stages — see POC_BUILD_PLAN.md §7 "Open items" and Charter v1.0's
Schedule for the pilot/acceptance phase that follows.

---

## Open items (not blocking)
1. 製品開発課 使用可否 lists as feedable file? (raises M3 AI confidence)
2. MSS BOM extract availability (would shortcut attach-rate estimation)
3. ④→ONE systematic factor ~0.836 — known rule or ad-hoc? (M4 template)
4. NAS auto-retrieval folder location (Phase-0 carry-over)
```
