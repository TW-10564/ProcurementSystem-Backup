# Session Changelog — 2026-07-09

Summary of all code changes made this session, in the order they were done.
Companion to `PILOT_DEPLOY_PLAN.md` (what's still planned but not yet done)
and `IMPLEMENTATION_PLAN.md` (overall architecture). Nothing in this file
has been committed to git yet — all changes are currently in the working
tree.

---

## 1. SAP stock matching — SYOCODE as primary join key

**Files:** `app/backend/onefcst/ingestion/sap.py`, `app/backend/onefcst/build.py`,
`app/backend/tests/test_sap_crosswalk_integration.py`

**Problem:** SAP stock rows were matched to items by あだ名 (name) only,
with a human-curated name-crosswalk as fallback for blank names. SYOCODE
(SAP's own part code) was present in the source file but never read.

**Fix:** Verified SYOCODE lines up exactly with `商品コード` — already
stored as an `ItemAlias` by the registry — so `sap.py` now parses SYOCODE
per row and returns raw per-row records (not pre-aggregated by name);
`build.py` resolves each row by SYOCODE first, あだ名 second, and
aggregates quantity by the resolved item.

**Verified impact (real data, `260629在庫.xlsx`):** 566/721 rows resolved
via SYOCODE directly (vs ~41 via the old name-crosswalk); unresolved rows
dropped from 368 (blank あだ名) to 150 (true floor — no あだ名, no
SYOCODE hit, no crosswalk hit).

---

## 2. Forecast accuracy — seasonal method decline gate

**Files:** `app/backend/onefcst/engine/baseline.py`, `app/backend/tests/test_baseline.py`

**Problem:** Found via per-category WAPE audit (SSD, HDD, 電源S, ファンN,
グラフィックカード were all worse than the naive trailing-mean baseline).
Root cause traced to a specific SKU (TFX300, a PSU): the `seasonal`
candidate won its own out-of-sample method-selection hold-out (252.5%
WAPE) then scored far worse on the next hold-out window (1171.3% WAPE) —
worse than every other candidate, including naive. Deseasonalizing an
already-collapsed recent level by a calendar-month index derived from the
SKU's OLD, higher-volume months pulls the forecast back toward
pre-decline volume instead of tracking the real drop.

**Fix:** Added `_is_seasonal_eligible()` — withholds `seasonal` as a
candidate when a SKU's recent 3-month level is under 50% of the *same
3-month period one year earlier* (year-over-year, not vs. the window's
all-time peak — an all-time-peak version was tried first and rejected
because it false-positived on genuinely cyclical series, caught by a new
regression test).

**Verified impact (real data, full 273-item portfolio backtest):**
overall WAPE 99.44% → 98.37% (naive stays 108.06%); 電源S 138.78% →
130.87% (now beats naive); confirmed identical in a live `onefcst.forecasting`
run, not just the offline backtest.

**Two alternative fixes were tested and rejected** (found not to help,
backtested before touching code): a tie-aware `WINNER_MARGIN` (made
things slightly worse overall) and a `combo_stable` candidate excluding
seasonal/trend (net zero effect — never won the earlier selection window
in practice).

---

## 3. Registry — "B650" cross-system alias collision

**Files:** `app/backend/onefcst/registry/build.py` (`resolve_via_source`),
`app/backend/onefcst/build.py` (`_resolve_planning1`)

**Problem:** Found via full-system audit. The string "B650" was an alias
for two different real items: a PSU's own 綽名 (`ONE綽名`, added early →
lower `ItemAlias.id`) and a motherboard's officially-curated planning
nickname (`④企画名前`, added later → higher id). `resolve()`'s
lowest-id tie-break silently routed every `①販売計画` row named
"ASR B650" (ASR-prefix-stripped to "B650") to the unrelated PSU instead
of the Tier-1 motherboard — a real financial-downside bug, since Tier-1
categories are planning-owned demand signals.

**Fix:** Added `resolve_via_source(session, name, source_system)` —
resolves only against one specific source system. Wired into
`_resolve_planning1`'s variant-matching loop (where the actual "B650"
match happens, after ASR-prefix stripping) so a planning-origin name
checks the curated `④企画名前` bridge first, before falling through to
`resolve()`'s generic (source-agnostic) pick. Scoped narrowly to
planning1 resolution only — does not change `resolve()`'s behavior for
other ingestion paths (e.g. SAP), where this preference wouldn't be safe.

**Verified:** rebuilt the DB; `"ASR B650"` now resolves to item 575
("B650 TW", ﾏｻﾞｰﾎﾞｰﾄﾞ) instead of item 899 (PSU).

---

## 4. Registry — ④ﾏｽﾀ plan-name alias resolution normalization

**Files:** `app/backend/onefcst/registry/build.py` (`build_registry` step 4)

**Problem:** Found via full-system audit. This one call site used a bare
case-sensitive dict lookup (`by_adana.get(...)`) instead of the
normalization fallback (`resolve_any`) every other ingestion path uses.
7 of 11 unresolved rows were pure case/spacing variants of a real 綽名
(e.g. `"Ry75700G"` vs canonical `"ry75700G"`) that would already resolve
with the standard fallback.

**Fix:** Added a normalized lookup (`_norm_for_crosswalk`) as a fallback
after the exact-match dict lookup, scoped to this one step.

**Verified:** rebuilt the DB; `④aliases=329 (unresolved 2)` — down from
`320 (unresolved 11)`.

---

## 5. API auth — constant-time key comparison

**File:** `app/backend/onefcst/api/auth.py`

**Problem:** Found via backend security review. `x_api_key != API_KEY`
is a content-dependent-timing string comparison — a theoretical timing
side-channel against the shared secret (low real-world severity for a
LAN-only key, but a one-line fix).

**Fix:** Replaced with `hmac.compare_digest`.

---

## 6. Frontend — export/download buttons bypassed the API auth header

**Files:** `app/frontend/src/api/client.ts` (`downloadReport`),
`app/frontend/src/pages/Accuracy.tsx`, `Adjustments.tsx`, `CompatReview.tsx`,
`Forecast.tsx`, `Mismatch.tsx`

**Problem:** Found via frontend security review. Every export/download
button (Accuracy, Adjustments, CompatReview, Forecast, Mismatch pages) used
a raw `<a href={downloadUrl(...)}>`, and Accuracy's FVA/adjustment-rate
report cards used raw `fetch()` — neither path goes through the axios
client that attaches `X-API-Key`. The moment `ONEFCST_API_KEY` is set
(which `CLAUDE.md` recommends before any non-localhost deployment), every
one of these would silently 401/403 while the rest of the app kept
working — a confusing partial outage.

**Fix:** Added `downloadReport(path, fallbackName)` to `client.ts` — fetches
via the authenticated `http` client with `responseType: "blob"`, then
triggers the download client-side via an object URL (reads the real
filename from `Content-Disposition` when available, falls back to a
sensible default otherwise). Replaced all 5 `<a href>` download links with
buttons calling `downloadReport`, and replaced Accuracy's two raw `fetch()`
calls with new `api.fvaReport()` / `api.adjustmentRate()` client methods.

---

## 7. Frontend — `Notifications.tsx` silent failure on API errors

**File:** `app/frontend/src/pages/Notifications.tsx`

**Problem:** Found via frontend security/quality review. The two API
calls on this page had no `.catch`, unlike every other page — a backend
error rendered as silent empty state instead of the error banner the rest
of the app shows.

**Fix:** Added `error` state and `.catch` handlers matching the pattern
used elsewhere (e.g. `Mismatch.tsx`); added the same `⚠️ {error}` banner.

---

## Verification performed

- Full backend test suite: **169/169 passing** (167 pre-existing + 2 new
  regression tests for the seasonal decline gate — one proving it fires
  on a TFX300-shaped collapse, one proving it does *not* false-positive
  on a normal cyclical trough).
- Frontend typecheck (`npx tsc --noEmit`): clean except one **pre-existing**
  `Forecast.tsx` error confirmed present before this session's changes
  (via `git stash`) — not introduced by this work, not yet fixed.
- Full DB rebuild (`onefcst.build`) + fresh forecast run
  (`onefcst.forecasting`) run end-to-end after every change; Gate A
  passing (99.0% plan-value resolution).
- Both servers started and confirmed live: backend `http://localhost:8600`
  (`/health` OK), frontend `http://localhost:5173` (200 OK).

## Not yet done

A full read-only audit (data integrity, forecast accuracy for every
category, backend security, frontend security) was also run this session;
everything found and judged worth fixing is listed above. Everything
found but **not** fixed (deliberately deferred — needs a design decision,
domain-expert review, or infrastructure work rather than a code patch) is
tracked in `PILOT_DEPLOY_PLAN.md`, most importantly: every `onefcst.build`
run currently destroys all human-entered adjustments/compat-rule
confirmations (`Base.metadata.drop_all` + no stable `Item` key across
rebuilds) — the top blocker before any pilot deployment.
