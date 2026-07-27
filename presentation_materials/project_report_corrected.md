# Procurement Division Operations Support System

Project Report · Digital Innovation Department 3 · 24 July 2026

Team members: Malambedu Karthikeyan Balamurukan, Balachandran Ravikumar, Mahadevan Pillai Divya

---

## Background

This project originally began as a Phase-1 POC aimed at automating Excel-based work, covering four functions: consolidation and reconciliation, anomaly detection, procurement checks, and SAP inventory pasting. As we got deeper into the actual data, though, it became clear that the real bottleneck in parts procurement was not the aggregation work itself but the judgment behind it — how much of each SKU should we actually be ordering in the first place? On 3 July 2026 we froze the Excel automation POC and narrowed the scope to a demand forecasting and advisory dashboard.

The objective under the new scope has stayed consistent since: ingest the ONE parts forecast / parts master system, SAP inventory, and the sales planning sources from the NAS strictly read-only, and cover per-SKU demand forecasting, order suggestions, CPU/MB/VGA/Case compatibility checks, and human adjustment with accuracy tracking in a single system. Nothing is ever written back to SAP or to any ONE file, and we have held to the founding principle throughout: the system suggests, a person decides.

One more decision we made early and have not walked back: no machine learning in Phase 1. Every forecast has to be traceable to arithmetic a buyer can check by hand. That mattered more to us than chasing a few extra points of accuracy with a model nobody could interrogate, and it also gives us a clean evidence bar — if an ML model is ever proposed later, it has to beat the statistical baseline on the same out-of-sample test before it earns a place.

## Current Status

All planned build stages of the forecasting engine (M0–M6) are complete and verified against real data.

- The ingestion and registry layer (Gate A) passes against actual NAS data: the 17 ONE sub-files plus the master 部品予測/部品マスター sheets, the weekly SAP stock extract, and the sales-plan sources. The registry currently resolves 2,932 items (Tier-1: 746 / Tier-2: 2,186) across 9,204 recorded aliases spanning at least four naming systems (ONE nicknames, SAP shorthand, model names from source ④, and others). The latest weekly run produces forecasts for 462 SKUs.

- The forecasting engine pits five statistical methods per SKU (seasonal, damped-trend, flat3, flat6, median6) plus their equal-weight combination (combo) against each other on a hold-out of that SKU's own most recent six months; the winner forecasts the next twelve. On the company-level walk-forward backtest the seasonal method comes in at 10.95% WAPE against 16.28% for a flat baseline and 13.59% for last-year-repeated — which is the concrete reason it earned its default position, not an assumption.

- The planning division plans only four categories — CPU, MB, VGA, Case — and everything else (RAM, PSU, cables, mouse & keyboard, fans, and so on) is derived for procurement from attach rates and compatibility rules, in a two-tier structure that mirrors how the departments actually divide the work. The compatibility layer currently holds 46 rules, 43 of them human-confirmed; drafts are AI-generated from bare part names but nothing takes effect without a person confirming it.

- Order suggestions have been checked against live PO history. Of 167 SKUs with real orders in the last 90 days, 99 had enough data on both sides to compare, and 47 of those (roughly half) fell within 2× of the actually ordered quantity. That is not accurate enough to order without human review, and we say so plainly — it is a plausibility check on live data, not a true historical backtest, because the system has no monthly snapshots of its own to replay yet. That becomes possible after a few more months of operation.

- Accuracy tracking now runs in both directions. Our own forecasts are scored against realized SAP actuals every run. For the planner's side there was a structural problem: the ONE sheet overwrites each month's forecast cell with the actual once the month closes, so no record survives of what was predicted in advance. Since mid-July every run archives the planner's current future-month figures — 3,311 snapshots so far — which means that from this autumn we will have the first genuinely fair, same-lead-time comparison between the planner's numbers and ours. Until then we deliberately treat the question as open.

## Technical Challenges Encountered

- Early on we found that simply opening and re-saving a ONE workbook with openpyxl silently strips every formula's cached value — we watched a stock balance go from 6,695 to blank on a no-op save. This single discovery shaped the whole architecture: ingestion is read-only by design, and any output we produce goes into a newly created workbook, never back into a source file.

- The live SAP stock source turned out to be an Access database rather than the xlsx we had been handed initially. We read it via mdbtools with the manually placed xlsx snapshot kept as a fallback, so a missing tool or an unreachable file degrades gracefully instead of failing the build.

- The WSL + OneDrive development environment caused recurring friction with the frontend tooling: Vite's file watcher cannot reliably see changes on the OneDrive mount (we run it in polling mode, which only partly helps), and the machine's default Node.js 18 does not run our Vite version at all — it needs Node 20+, which we switch to via nvm. The team habit became "restart the dev server before suspecting your code."

- We discovered that the demand calculation for order suggestions existed along two independent code paths (the API view and the verification module), with genuinely different horizons — twelve months of pipeline output on one side, a four-month plan-value sum on the other. This is the classic trap where a fix lands on only one side, so we wrote it down as an explicit operating rule: any change to demand, snapshot, or adjustment logic gets checked in both places.

## Lessons Learned

- Report inconvenient numbers as they are. "Roughly half within 2×" on order suggestions, an FVA report with nothing gradeable yet, and one direct planner-versus-system month where the planner came out ahead (43.9% vs 61.6% WAPE) all went into the reports unretouched. On that last one we also documented why the comparison was structurally unfair — the planner's figure was captured one day before month-end, ours thirty days out — and published it with the caveat rather than either hiding it or claiming a verdict. We think this habit is what will earn the trust of planning and procurement, and it is cheaper to build in from day one than to retrofit.

- Confine writes to a single layer and make every one of them leave an audit trail. Everything outside the advisory layer (adjustments, rule confirmations, notifications, order actions) is read-only, so who changed what and when is guaranteed by structure, not by convention.

- Make the system flag its own suspicious output. The best example came from a real item: a packaging part with demand of about one unit per month and a supplier MOQ of 850, where the mathematically correct minimum order equals decades of stock. The rounding is not wrong — that is the smallest order the supplier accepts — but a buyer scanning a list has no way to tell it apart from a normal reorder, so suggestions now carry an explicit "MOQ-driven, N months of cover" flag, with the threshold configurable rather than baked in.

## Next Steps

The immediate step is a pilot deployment on an internal server for a small group of planning and procurement users. The plan is deliberately staged: a shared API key for the pilot, SSO and role-based access control once the user list is settled, and the server itself whitelisted for NAS access so ingestion runs where the data lives. A pre-deployment review this week surfaced a short list of items we are fixing first — hardening the rate limiter, adding a production serving path for the frontend, and normalizing adjustment dates so a mid-month entry cannot silently fail to apply.

In parallel: tune the demand-averaging window for order suggestions, where large-lot ordering patterns (consolidated purchases reaching up to ~25× a normal month) are currently being smoothed away; backfill around twelve months of weekly sales history once procurement confirms how far back the records go; and start modeling incoming POs, which the stock projection currently ignores — a known limitation buyers are warned about, but one worth closing.

## Summary

We narrowed the scope from automating Excel work to an advisory dashboard covering demand forecasting, order suggestions, compatibility checks, and human adjustment, and completed all planned build stages (M0–M6) verified against real data. Every source stays read-only, every AI output passes through a human, and the forecasting is statistical and fully traceable by design. The accuracy question — whether this system's numbers beat the planner's — is one we have refused to answer by assertion: the machinery to settle it fairly is now running, and the evidence accumulates with every month the system operates.
