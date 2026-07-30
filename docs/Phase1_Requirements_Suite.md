# Requirements Suite — Phase 1
## AI-Assisted Procurement Decision Support: ONE System Excel Automation

> **Document status:** Draft for submission
> **Date:** 2026-06-29
> **Author:** 【your name / 社員番号】
> **Companion to:** docs/Phase1_Project_Charter.md
> _Items in 【 】 are placeholders. Requirement IDs are stable for traceability._

---

## 1. Overview

This document specifies the functional, non-functional, data, and acceptance
requirements for Phase 1. Phase 1 delivers an **assistive** system: it
automatically reads the existing ONE Excel files, consolidates and computes,
and presents the results — **without replacing the authoritative master or
making any purchasing decisions automatically.**

Priority key: **M** = Must-have (Phase 1), **S** = Should-have, **C** = Could-have (if time permits), **F** = Future phase.

---

## 2. Functional Requirements

### 2.1 Data Ingestion
| ID | Requirement | Priority |
|---|---|---|
| FR-1.1 | The system shall read the **17 product sub-files** and **2 master files** from the NAS shared folder. | M |
| FR-1.2 | Ingestion shall be **read-only** — the system shall never modify source files. | M |
| FR-1.3 | The system shall run ingestion **automatically on a daily schedule** and on-demand. | M |
| FR-1.4 | Each ingestion shall **snapshot** the pulled data (timestamped) for history/audit. | S |
| FR-1.5 | The source folder location shall be **configurable** (one setting) to switch between local copy and NAS. | M |
| FR-1.6 | If a source file is missing/locked, the system shall **skip it gracefully**, flag it, and continue. | M |

### 2.2 Parsing & Normalization
| ID | Requirement | Priority |
|---|---|---|
| FR-2.1 | The system shall parse the common sheets (`部品詳細`, `生産予測`, `購買計画`) across all 17 files. | M |
| FR-2.2 | The system shall handle **heterogeneous layouts**, including the non-standard files (FAN, Case_梱包) and both `.xlsb`/`.xlsx` formats. | M |
| FR-2.3 | Per-file column layouts shall be held in **external configuration** (no code change to onboard a layout fix). | M |
| FR-2.4 | The system shall key all records on **綽名 (shortname)**. | M |

### 2.3 Consolidation (部品在庫)
| ID | Requirement | Priority |
|---|---|---|
| FR-3.1 | The system shall produce a **single unified table** of all SKUs across the 17 files with current stock balance and days-of-stock. | M |
| FR-3.2 | The system shall flag each SKU as **excess / shortage / negative-stock**. | M |
| FR-3.3 | Data-quality issues (e.g., negative balances) shall be **surfaced as flags**, not silently copied. | M |

### 2.4 Accuracy / Reconciliation Gate
| ID | Requirement | Priority |
|---|---|---|
| FR-4.1 | The system shall **reconcile** parsed records against expected counts per file and report **PASS / WARN / FAIL**. | M |
| FR-4.2 | The reconciliation result shall be **visible to the user on every run** (not hidden). | M |
| FR-4.3 | A layout change in a source file shall surface as WARN/FAIL (drift detection). | M |

### 2.5 Master Computation Replication (assistive)
| ID | Requirement | Priority |
|---|---|---|
| FR-5.1 | The system shall aggregate the sub-files' `生産予測` into the master's `部品予測` format (history months). | M |
| FR-5.2 | The system shall **validate** the reproduced history against the real master and report the match. | M |
| FR-5.3 | The **forecast portion** (current + future months) shall be an **isolated, pluggable component** that reproduces the current method (先月比) for parity. | M |
| FR-5.4 | The system shall derive `部品計画` from the forecast per the documented rules (base × factor, custom from build-actuals). | S |
| FR-5.5 | The system shall **not** write to or replace the authoritative master file. | M |

### 2.6 Dashboard / Portal
| ID | Requirement | Priority |
|---|---|---|
| FR-6.1 | The system shall provide a **web dashboard** showing KPIs (total SKUs, excess, shortage, negative, accuracy gate). | M |
| FR-6.2 | The dashboard shall provide a **category roll-up summary**. | M |
| FR-6.3 | The dashboard shall provide a **filterable & searchable** full SKU list. | M |
| FR-6.4 | The dashboard shall show the **reconciliation report** and the **master-automation view**. | M |
| FR-6.5 | The UI shall be **bilingual (日本語 / English)**, switchable instantly. | M |
| FR-6.6 | The dashboard shall provide a **"Re-run"** action to refresh from source. | S |

### 2.7 Export
| ID | Requirement | Priority |
|---|---|---|
| FR-7.1 | The system shall export results as **Excel (.xlsx)** in a familiar master/consolidated format. | M |
| FR-7.2 | Exports shall include the **reconciliation/accuracy** sheet (self-evidencing). | S |
| FR-7.3 | Exports shall be **timestamped**. | S |

### 2.8 Human Judgment / Overrides
| ID | Requirement | Priority |
|---|---|---|
| FR-8.1 | The system shall allow **manual override inputs** (campaign push, large corporate deals, seasonal correction) applied on top of computed values. | S |
| FR-8.2 | Overrides shall recompute downstream values automatically. | S |

---

## 3. Non-Functional Requirements

| ID | Category | Requirement | Priority |
|---|---|---|---|
| NFR-1 | **Accuracy** | Reproduced recent/active data shall match the source within tolerance (achieved: **100%** on recent/active; consolidation gate **100%**). | M |
| NFR-2 | **Transparency** | The system shall display an accuracy metric each run; no silent truncation/caps. | M |
| NFR-3 | **Performance** | Daily refresh shall complete within **【target, e.g. 5 min】**; dashboard views shall load responsively. | S |
| NFR-4 | **Reliability** | A locked/missing/corrupt source file shall not crash the run; it is flagged and skipped. | M |
| NFR-5 | **Usability** | Non-technical, web-based, bilingual; procurement owners require **no new manual work**. | M |
| NFR-6 | **Security / Access** | NAS access is **read-only**; portal access is controlled to authorized staff. | M |
| NFR-7 | **Maintainability** | Layout/source config externalized; a **data-abstraction layer** isolates Excel so a future Oracle DB swap does not touch business logic. | M |
| NFR-8 | **Auditability** | Snapshots + reconciliation results retained per run. | S |
| NFR-9 | **Portability** | Runs on 【Windows machine with Excel / server】 with the existing toolchain. | S |

---

## 4. Data Requirements

| ID | Requirement |
|---|---|
| DR-1 | **Sources:** 17 product sub-files + 2 masters (`ONE計画`, `NoteONE計画`) on NAS: `\\172.25.20.11\…\【通常業務】\ONE`. |
| DR-2 | **Formats:** mix of `.xlsb` and `.xlsx`; masters are `.xlsm` (macro/formula). |
| DR-3 | **Key:** `綽名 (shortname)` joins sub-files ↔ master ↔ part master. |
| DR-4 | **Master sheets:** `仕様定義` (legend), `部品予測` (forecast, monthly, 2017→), `部品マスター` (reorder params: 最小在庫/LT/MOQ/仕入先), + notebook `販売計画`. |
| DR-5 | **Refresh cadence:** daily (source files updated near-daily). |
| DR-6 | **Scale:** 534 active SKUs across 17 files; master forecast ~1,386 parts; part master ~4,958 records. |
| DR-7 | **Snapshot/versioning:** each pull retained for audit and history. |

---

## 5. Acceptance Criteria

| ID | Criterion |
|---|---|
| AC-1 | All 17 sub-files consolidate with a **100% reconciliation pass** (or any failure clearly flagged). |
| AC-2 | Master history reproduction **validated on anchor categories**, matching the real master on recent/active data (**100%** achieved). |
| AC-3 | Dashboard delivers KPIs, summary, searchable list, reconciliation, and master view — **bilingual**. |
| AC-4 | Excel export produces a correct, timestamped file in the expected format. |
| AC-5 | The **authoritative master file is never modified** by the system. |
| AC-6 | The system runs against the **live NAS source** once access is granted (config switch only). |

---

## 6. Dependencies & Open Questions

| ID | Item | Owner | Status |
|---|---|---|---|
| OQ-1 | Long-term direction: assistive vs. system-as-master | Sponsor / 内田 | Phase 1 assumes **assistive** |
| OQ-2 | How desktop master forward-forecast values are produced | 内田 | Pending |
| OQ-3 | Is `ONE計画.xlsm` the complete master or a subset | 内田 | Pending |
| OQ-4 | Which cells are typed manually each month (true inputs) | 内田 | Pending |
| OQ-5 | How manual judgment (campaign/deal/seasonal) is applied | 内田 / 企画 | Pending |
| OQ-6 | NAS read access (employee number + name) | 内田 / IT | Submitted |
| OQ-7 | Performance targets, monthly-hours baseline for ROI | Sponsor | 【fill】 |

---

## 7. Phasing Note

Phase 1 deliberately **excludes** improved forecasting and any replacement of the
master, to keep scope safe and approvable. Both are designed to be **additive
later** (the forecast component is already pluggable; the logic already lives in
the system rather than the spreadsheet), enabling a clean future path to better
forecasting and the planned Oracle DB migration **without rework**.
