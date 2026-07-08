# Fix Plan — Known Forecasting Weak Points + Data Summary Cross-Check

*Written 2026-07-07. Two audit passes complete (file:line evidence gathered for all items below); fixes not yet implemented. Security/auth/RBAC is explicitly out of scope per user instruction — this covers only core forecasting/procurement correctness.*

## Context

The core system's foundations are verified strong — Gate A 97.9% data resolution, Gate B 100% cell-level parity against the real Excel, Gate C shows the seasonal method beats naive baselines at the category level (10.95% vs 13.6-16.3% WAPE). A first review pass found 4 weak points (per-SKU accuracy, a Tier-2 forecasting bug, stock coverage, and a missing name bridge). A second, broader sweep across the remaining core modules (stock projection, mismatch detection, adjustments, FVA scoring, the item registry, exports, and DB schema) found **5 more issues — three of them serious, silently-wrong-output bugs, not edge cases.** This document lists everything found, ordered by severity, plus the separate Data Summary cross-check task.

---

## SEVERITY: CRITICAL — silently wrong numbers/decisions, no crash to alert anyone

### C1. FVA (Forecast Value Added) scores every human adjustment as "hurt" — the metric is backwards

`onefcst/verify/fva.py:31-38` (`score_fva`) compares the adjustment's outcome against a "baseline" that is read from `PlanValue` at `level="master"` for that month. But per this codebase's own rule (`planner_accuracy.py:159-162`), once a month closes (`adj.month < today`, the exact filter at `fva.py:24`), the master cell has already been **overwritten with the actual**. So `baseline == actual`, `baseline_wape ≈ 0`, and any adjustment that isn't a perfect guess scores worse than "doing nothing" — verdict is always `adjustment_hurt`. **This means the FVA dashboard (one of the app's headline accuracy claims — Accuracy.tsx's FVA card) has been systematically wrong since it was built: it can never show a human adjustment helping, even when it did.**

**Fix:** `score_fva` must compare against the ORIGINAL statistical forecast that was overridden — either `Adjustment.old_value` (already captured at adjustment time, just unused) or the matching `Forecast.baseline` row for that item/month/run — never the current master cell.

### C2. Global and category-scope adjustments are silently accepted and then never applied anywhere

`onefcst/forecast_views.py:77` (`_latest_adjustments`) filters `Adjustment.scope == "sku"` only, and it's the only consumer of adjustments across every read path (`summary`, `list_forecasts`, `item_detail`, and by extension the FVA scoring above). `record_adjustment` happily accepts and stores `scope="global"` or `scope="category"` (`advisory/adjustments.py:22-23`) — the Adjustments UI form even has a dropdown for it — but nothing downstream ever reads a non-"sku" adjustment. **A planner can record a category-wide correction, see "Saved", and it changes zero forecast numbers anywhere in the system, with no error and no indication.**

**Fix:** `_latest_adjustments` (or a new resolver) must expand global/category adjustments to every item they should affect, the same way `scope="sku"` is applied today.

### C3. Item name resolution is non-deterministic — the same alias can silently resolve to a different item between runs

`onefcst/registry/build.py:175` (`resolve()`) does `ItemAlias.alias == name` with **no `order_by`**, so when the same alias string is claimed by two items from two different source systems, which one wins is arbitrary DB storage order — not tracked as a conflict at all, because the collision counter (`_add_alias`, lines 22-33) only catches same-alias-same-source_system collisions (matching the DB's own unique constraint, `models.py:57`: `(alias, source_system)`, which permits cross-system duplicates by design). **This can silently misattribute an entire item's plan/stock/actuals data to the WRONG item, and which item "wins" can change between rebuilds of the same data — i.e. rerunning `python3 -m onefcst.build` twice on identical input could produce two different forecasts for the affected items.**

**Fix:** Either (a) add deterministic tie-breaking to `resolve()` (e.g. prefer a fixed source-system priority order, or the most-recently-updated alias), or (b) widen the conflict detection to catch and report cross-system alias collisions too, so they route to human review like other conflicts already do (per `build.py`'s `alias_conflicts` reporting), rather than resolving silently either way.

### C4. `PlanValue` re-ingestion averages stale and corrected values together instead of using the latest snapshot

`onefcst/forecasting.py:37-41` (`_series_by_item`) uses `func.avg(PlanValue.value)` grouped by `(item_id, month)` to build each SKU's history. There is no unique constraint on `PlanValue(item_id, month, level)` (`models.py`), so when a source file is re-ingested with a corrected number for a month already ingested under an earlier snapshot, the forecast input **blends the old (wrong) and new (corrected) value via averaging** instead of using only the latest snapshot — the opposite of what `_stock_by_item` correctly does for `StockState` (latest-snapshot-wins). `forecast_views.py:232` (`item_detail`'s displayed history) has the identical issue.

**Fix:** Change `_series_by_item` and the `item_detail` history query to select the value from the most recent `snapshot_id`/`Snapshot.file_mtime` per `(item_id, month, level)`, not an average across all ingested snapshots.

### C5. No unique constraint on `Forecast(run_id, item_id, month)` — a future code path could silently double-count

`onefcst/models.py:123-131` — `Forecast` has no DB-level uniqueness guard. Today's `generate()` avoids duplicates only via an in-memory `already_forecasted` set (`forecasting.py`), so it's not currently triggered, but any future write path (a retry, a partial re-run, a bug in a new caller) could insert a second row for the same (run, item, month) and silently double `next12_total` and any downstream order suggestion, with no error raised anywhere.

**Fix:** Add `UniqueConstraint("run_id", "item_id", "month")` to `Forecast`, matching the pattern already used on `CompatRule` and `ItemAlias`.

---

## SEVERITY: HIGH — crashes / misleading test results on realistic data

### H1. Mismatch gate crashes (`TypeError`) on a NULL master value in the anchor month

`onefcst/verify/mismatch.py` — `compute_mismatch`'s `demand()` helper returns `v[0]` unguarded (used at lines ~75, 81, 87) and is `+`-accumulated into `cpu_by_socket`/`mb_by_socket` totals; if any classified CPU/MB item has a NULL value in the anchor month, this raises `TypeError` and **aborts the entire Mismatch Detection tab**, not just that one item. The identical case is already correctly guarded elsewhere in the same file (`historical_scan`, lines ~149/153, uses `(val or 0)`) — the anchor-month path was simply missed.

**Fix:** Apply the same `(val or 0)` guard in `compute_mismatch`'s demand accumulation.

### H2. The mismatch module's own "acceptance test" can only crash, never actually fail — and its PASS is unconditional

`onefcst/verify/mismatch.py:201-207` (`run`) prints `PASS` with no assertion of any pass condition, and will raise `IndexError`/`ValueError` (empty `hist`/`persistent` sequences) rather than ever printing a genuine FAIL. **This means the historical-validation "PASS" shown for the AM4 mismatch finding (39 months of 200-360% divergence, already surfaced to procurement) has no actual test behind it** — it would say PASS even if the underlying pattern silently stopped being true, and would crash rather than correctly report FAIL if the data shape changed.

**Fix:** Rewrite as a real assertion (e.g. persistent-months count and gap-% range checked against explicit thresholds, raising a clear failure message) instead of an unconditional print.

### H3. `stock_projection.py:48` — `TypeError` when average monthly demand is both omitted and contains a `None` entry

The average-demand calculation doesn't apply the same `d or 0.0` guard that `project_stock` uses one line away (line 23), so a `None` in `monthly_demand` crashes the average instead of being treated as zero, unlike the rest of the function.

**Fix:** Apply the identical `or 0.0` coercion in the average calculation for consistency with the rest of the function.

---

## SEVERITY: LOW / COSMETIC — noted, not urgent

- `onefcst/export/excel.py` — `_autosize` only covers columns A-J; forecast month columns past J in wide exports aren't auto-sized (cosmetic column width only, data itself is correct — headers/row-widths were checked and match).
- `onefcst/scheduler.py` — if `generate()` fails before writing a `ForecastRun` row, `is_due` stays true and the job retries every poll interval rather than backing off; not a correctness bug (no double-write, no silent death of future runs — exceptions are caught per-cycle and logged), just a minor noisiness/retry-storm risk worth a backoff if it ever fires in practice.

---

## SEVERITY: MEDIUM — accuracy/coverage gaps, no silent wrong-decision risk

### M1. Per-SKU WAPE ~114% (vs 10.95% at category level)

**Diagnosis — not a bug, an inherent property, but partly fixable:**
`onefcst/engine/baseline.py:186-211` (`holdout_eval`) picks one winning method among up to 6 candidates using only a 6-point test window (line 205) — high-variance selection on noisy, often-intermittent monthly SKU demand. The module's own docstring (lines 14-19) already measured that **combo (average of candidates) beats winner-take-all**: 115.7% vs 121.5% volume-weighted WAPE. Separately, `forecasting.py`'s near-term (M+1) WAPE is ~54-70% — the "114%" headline number is inflated by scoring a flat 6-month blend, not the 1-month-ahead number a buyer actually acts on given lead times.

**Fix:**
- `engine/baseline.py`: in `holdout_eval`, only crown a single winner if it beats `combo`'s WAPE by a real margin (e.g. >10 relative points); otherwise default to `combo`. Targets the documented overfitting-to-noise failure mode directly.
- `forecasting.py` / `forecast_views.py`: promote the already-computed near-term (M+1) WAPE to the primary per-item confidence signal shown in the UI; keep the 6-month blended WAPE as a secondary "full horizon" caveat rather than the number driving the confidence-dot color.
- Add a test to `tests/test_baseline.py` asserting margin-gated selection reduces volume-weighted WAPE vs pure winner-take-all on existing fixtures.

### M2. Tier-2 direct attach-rate = 0 items_forecasted

**Diagnosis — a real, fixable bug:**
`tier2_forecasting.py` gates the direct attach-rate path with `baseline.is_stale(obs, start)` (line 82), which only checks **trailing past** months before `start`. But 部品予測 is a forward-looking plan, and Tier-2 accessory parts have their nonzero values concentrated in **future** months relative to `start`, not the trailing 6 — so nearly all 626 are wrongly flagged stale. Proof by contrast: the category-fallback path (lines 159-193) never applies `is_stale` and correctly produces 153 forecasts from the same underlying data.

**Fix:**
- Remove the `is_stale()` pre-filter from the direct attach-rate path in `tier2_forecasting.py`. Eligibility should rely solely on `tier2.compute_attach_rate`'s own `MIN_OVERLAP_MONTHS` overlap check — already correctly used by the category-fallback path.
- Re-run forecasting after the fix and confirm `items_forecasted` becomes nonzero and `items_skipped_stale` drops to reflect genuinely dead SKUs only.
- Add a test to `tests/test_tier2_forecasting.py` using forward-dated nonzero months to lock in the fix.

### M3. Stock coverage ~18% (blocks order suggestions)

**Diagnosis — mostly structural, one fixable slice:**
`StockState` rows only come from the 17 ONE 部品詳細 sub-files (534 SKUs total) and SAP physical stock. Most Tier-2 items (keyboards, mice, software, consumables) are structurally outside both sources — no code fix can create stock data that doesn't exist anywhere. The one fixable slice: SAP has ~368 blank-`あだ名` rows, and the crosswalk recovers only 41/368 today because `registry/build.py`'s `resolve()` does exact string matching with no normalization.

**Fix (bounded):**
- Reuse the existing `norm()` normalization already proven in `consolidation/stock_paste.py` (fullwidth→halfwidth, case-fold, strip spaces/hyphens) in `name_crosswalk.py`/`resolve()` before matching blank-`あだ名` SAP rows — should recover a meaningful fraction of the remaining 327 unmatched rows.
- Report plainly to the team: full Tier-2 stock coverage needs a **new inventory data source** for accessory/consumable parts — a data-sourcing ask for purchasing/IT, not further engineering.

### M4. ①販売計画 37% name resolution

**Diagnosis — structural data gap, not a bug:**
`planning1.py` correctly extracts finished-PRODUCT model names, but no model-name↔綽名 (BOM/attach) bridge exists anywhere in the source data — `engine/tier2.py`'s own docstring confirms the ONE計画.xlsm's intended `製品マスター`/`製品計画` sheets don't exist in the live workbook.

**Fix (mitigation, not a full solve):**
- Add a heuristic fuzzy matcher for ① model names → 綽名, modeled on `consolidation/stock_paste.py`'s existing SYONAME substring/keyword matcher — will raise resolution for models with recognizable CPU/GPU codes embedded, but won't reach full coverage.
- Flag to the team: fully closing this needs either a real `製品マスター`/BOM table from planning, or extending NotePC's partial model→綽名 bridge to desktop models.

---

---

## Separate task: Cross-check against `Data Summary.xlsx` (teammate's file)

Located at repo root. 3 sheets: `All Products` (534 SKUs, per-SKU stock/forecast), `Category Summary` (CPU/MB/MEM/SSD/HDD/VGA/RTX — **VGA and RTX as separate rows**), `ReadMe` (sources: SAP snapshot 6/29, ONE snapshot 6/30, forecast = Jul/Aug/Sep 2026).

**Already discovered:**
- Our system merges VGA+RTX into one `Item.category = 'グラフィックカード'` with `subcategory='RTX'` for 23 items, vs. the teammate's sheet splitting them into 28 VGA + 24 RTX rows. Any comparison must group by category+subcategory or VGA/RTX will look falsely mismatched.
- **The live `forecasts` table is currently empty** — the DB was rebuilt for a schema change but forecasting hasn't been rerun since. No numbers exist yet to compare.

**Execution plan:**
1. Rebuild DB (`python3 -m onefcst.build`) and regenerate forecasts (`python3 -m onefcst.forecasting`).
2. Write a comparison script: read `Data Summary.xlsx`'s `Category Summary` sheet, query our DB for the same categories' Jul/Aug/Sep 2026 forecasts and current stock (splitting グラフィックカード by subcategory), print a side-by-side diff (their number vs ours vs % difference).
3. Spot-check a sample of `All Products` rows against our per-item numbers for item-level (not just category-level) discrepancies.
4. Report plainly: where we match (validates the system), where we diverge and why (different snapshot dates, different stock-source preference, or a real bug), and whether divergence means our system is wrong or the two sheets use different assumptions.

---

## Recommended fix order

Critical items first — they're silently producing wrong output right now, with no crash to flag them:

1. **C1** (FVA baseline) — one-line-ish fix, corrects a headline metric that's been backwards since launch
2. **C2** (global/category adjustments no-op) — planners may believe they've already made corrections that never took effect
3. **C3** (non-deterministic alias resolution) — could be silently misattributing data right now; worth an immediate one-off audit query (find all aliases claimed by >1 item across source systems) even before the code fix lands
4. **C4** (PlanValue averaging) — affects forecast input correctness whenever a source file gets re-ingested
5. **C5** (Forecast unique constraint) — cheap schema hardening, no current trigger but zero cost to add now
6. **H1-H3** (mismatch crash, fake acceptance test, stock-projection edge case) — fix alongside C-items since they're quick
7. **M1-M4** — the original 4 items; M2 (tier-2 staleness bug) is a clean win, M1/M3/M4 are partly structural
8. **Data Summary cross-check** — do this AFTER at least C1-C5 land, so the comparison reflects corrected numbers, not numbers still carrying known bugs

## Verification checklist

- [ ] One-off SQL audit for C3: find alias strings claimed by items across >1 `source_system` — quantify real-world exposure before coding the fix
- [ ] `cd app/backend && python3 -m pytest tests/ -q` stays green after each fix; add new tests for C1, C2, C3, C4, H1, H2 (none currently covered per the audit)
- [ ] Rerun `python3 -m onefcst.build && python3 -m onefcst.forecasting`; confirm tier2 `items_forecasted` > 0, SAP/crosswalk matched count rises, no crash from H1
- [ ] Manually verify C2's fix: record a category-scope adjustment, confirm it changes the forecast for every item in that category, not zero items
- [ ] Manually verify C1's fix: find/create a closed-month adjustment that clearly helped, confirm FVA now shows "adjustment_helped" instead of always "hurt"
- [ ] Run the Data Summary comparison script; review diff table before concluding anything
- [ ] Restart both servers and spot-check Forecast/Mismatch/CompatReview/Accuracy tabs after the DB rebuild
