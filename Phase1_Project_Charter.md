# Project Charter — Phase 1
## AI-Assisted Procurement Decision Support: ONE System Excel Automation

> **Document status:** Draft for submission
> **Date:** 2026-06-29
> **Author / Project Lead:** 【your name / 社員番号】
> **Sponsor:** 【Product Management Manager / 内田さん — confirm】
> _Items in 【 】 are placeholders to fill before submission._

---

## 1. Purpose

Automate the repetitive, error-prone monthly Excel work in the procurement
"ONE System" and present a single, always-current view of inventory, demand,
and procurement status — **so procurement staff spend less time rebuilding
spreadsheets and more time on decisions.**

The system is a **decision-support / assistance tool**. Humans retain all
purchasing decisions and remain the owners of the authoritative master files.

## 2. Background & Problem Statement

- Procurement planning runs across **18+ linked Excel files** (the "ONE System")
  stored on a NAS: **17 product sub-documents** (one per product owner) plus
  **2 master files** (`ONE計画` for desktops, `NoteONE計画` for notebooks),
  maintained by hand by 購買部 (内田さん).
- Every month, demand forecasting, inventory computation, and master
  consolidation are performed **manually** → slow, error-prone, and with **no
  proactive alerting**.
- Data analysis across all 17 files found **327 of 534 active SKUs (61%) flagged
  as excess inventory**, and the incumbent forecasting method systematically
  over-forecasts — a key driver of overstock.
- There is no consolidated, real-time view of inventory health across products.

## 3. Business Case / Justification

- **Reduce manual effort & human error** in the monthly Excel cycle.
- **Surface excess / shortage proactively** instead of discovering it late.
- **Improve data freshness** (daily, automated) vs. periodic manual updates.
- **Lay the foundation** for the planned future Oracle DB migration (logic moves
  into a system, not trapped in macro spreadsheets) and for improved forecasting.

## 4. Objectives & Success Criteria

| # | Objective | Measurable success criterion |
|---|---|---|
| O1 | Automate consolidation of all sub-files | All 17 files auto-consolidated daily with a **verifiable accuracy gate** (achieved: 534/534 SKUs, 100% pass) |
| O2 | Reproduce master computations from source | Recent/active data reproduced from sub-files at **100% match** vs. the real master (validated) |
| O3 | Provide a single current view | Bilingual dashboard (inventory, summary, accuracy, master view) + one-click Excel export |
| O4 | Reduce manual monthly effort | Eliminate manual re-keying of the mechanical (non-judgment) portions; target **【X%】** reduction in monthly manual hours |
| O5 | Make accuracy transparent | Every run shows a reconciliation result (pass/warn/fail per file) |

## 5. Scope

### 5.1 In Scope (Phase 1)
- **Automated daily data ingestion** (read-only) of the 17 sub-files + 2 masters
  from the NAS; a snapshot is taken on each pull.
- **Inventory consolidation** (部品在庫) — one unified table across all SKUs with
  excess / shortage / negative-stock flags.
- **Accuracy / reconciliation gate** — per-file validation against expected counts.
- **Master computation replication (assistive):** aggregate the sub-files'
  `生産予測` into the master's `部品予測` format (history), derive `部品計画`;
  the **forecast portion is an isolated, pluggable component that reproduces the
  current method (先月比) for parity**.
- **Dashboard portal** — bilingual (日本語 / English): KPIs, category roll-up,
  filterable/searchable SKU list, reconciliation, and a master-automation view.
- **Excel export** — outputs in the master / consolidated format, downloadable.
- **Manual-override inputs** — campaign, seasonal, and large-deal adjustments
  applied on top of computed values (human judgment preserved).
- Initial coverage: anchor categories first (MB, GPU, SSD, MEM, Case),
  **extensible to all categories**.

### 5.2 Out of Scope (Phase 1 — future / separate)
- ❌ **Replacing or retiring the authoritative Excel master.** The system is
  **assistive**; 内田さん keeps the official master. (System-as-master is a
  *future, optional* consideration pending stakeholder decision.)
- ❌ **Automated ordering** or writing back into the NAS source files.
- ❌ **SAP / external system integration** and external data feeds.
- ❌ **Improved / AI forecasting model.** Phase 1 reproduces the current method
  only; a better forecaster is a deliberately deferred future phase (the
  component is built to be swappable).

## 6. Key Deliverables

| # | Deliverable | Status |
|---|---|---|
| D1 | Data pipeline: NAS → normalize → database (snapshot per pull) | Built (local source); NAS pending access |
| D2 | Consolidation engine + accuracy gate | ✅ Built & verified (534/534, 100%) |
| D3 | Bilingual dashboard portal (view / filter / search / export) | ✅ Built |
| D4 | Excel exporter (consolidated + master format) | ✅ Built |
| D5 | Desktop master history-aggregation (assistive) + forecast slot | ✅ PoC built & validated |
| D6 | Project Charter + Requirements Suite | ✅ This submission |
| D7 | Stakeholder demo | Ready |

## 7. Stakeholders

| Role | Who | Responsibility |
|---|---|---|
| Sponsor | 【Product Management Manager】 | Approves scope, funding, priorities |
| Master data owner | 購買 内田さん | Owns master files; key data & process authority |
| Product owners (×17) | Each procurement member | Maintain their product sub-files (unchanged) |
| Planning input | 企画部 | Sales plan / demand inputs |
| Project lead / developer | 【your name】 | Design, build, deliver |

## 8. Milestones (high-level)

| Milestone | Target | Status |
|---|---|---|
| M1 Requirements & charter sign-off | 【date】 | This submission |
| M2 NAS access granted + live data connected | 【date】 | Pending 内田 / IT |
| M3 Pilot on anchor categories (validated) | 【date】 | PoC done |
| M4 Stakeholder demo & feedback | 【date】 | Ready |
| M5 Phase 1 acceptance | 【date】 | — |

## 9. Assumptions

- NAS access (read-only) will be granted; files are in a **stable shared
  location with fixed filenames** (confirmed by 内田さん).
- Master/sub-file **templates are structurally stable** — only the data updates
  monthly (confirmed); history runs back to 2017.
- Files are updated **frequently (≈ daily)**, so daily automated refresh is
  appropriate.
- The common key across files is **綽名 (shortname)**.

## 10. Constraints

- **Decision-support only** — no automated ordering; humans decide.
- **Excel is the data source now**; design must not block the future Oracle DB swap.
- Master files are **macro/formula-driven** — the system reads them safely and
  does not corrupt them.
- Solution must be **non-technical and bilingual** for procurement staff.

## 11. Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Org may not approve system replacing the master | Scope creep / rejection | **Phase 1 is assistive by design**; replacement is out of scope |
| Incumbent 先月比 forecast is inaccurate (over-forecasts) | Wrong recommendations | Forecast kept **pluggable & parity-only** in Phase 1; improvement deferred |
| Source layout drift (daily edits by 17 owners) | Silent wrong data | **Reconciliation gate** flags any drift (PASS/WARN/FAIL) each run |
| Source file locked/open during read | Failed pull | Per-file graceful handling; failed file flagged, run continues |
| Source data quality (e.g., negative balances) | Bad numbers | Surfaced as flags, not silently copied |
| NAS access / IT approval delay | Schedule slip | Runs on local copies until access granted |

## 12. Open Decisions & Dependencies (pending 内田さん)

1. **Direction:** assistive (Phase 1) vs. eventual system-as-master — Phase 1
   assumes **assistive**; confirm long-term direction.
2. How the desktop master's **forward forecast values** are produced today.
3. Whether `ONE計画.xlsm` is the **complete master** or part of a larger system.
4. Which cells 内田さん **types manually each month** (the true inputs).
5. How **manual judgment** (campaigns, big deals, seasonal) is applied.
6. **NAS read access** (employee number + name submitted).
