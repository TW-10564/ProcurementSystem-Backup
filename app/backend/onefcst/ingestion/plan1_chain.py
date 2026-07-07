"""Per-SKU cell + formula extraction from ①販売計画's コンシューマー/コマーシャル
sheets, for verify/parity.py's chain check (Gate B).

ingestion/planning1.py already reads the plan VALUES for these sheets, but
only the cached numbers — engine/chain.py's reproduce_chain needs the cell
FORMULAS too (to classify chain/lookup/custom/override via
classify_formula), plus the sheet's own per-column MoM-default and
manual-override rows that those formulas reference. This module reads both
value and formula views of the same cells and shapes them exactly as
reproduce_chain expects: months, mom, manual, and a per-SKU cells list.

Row layout (both sheets, verified 2026-07-07, 0-based row indices):
  row 2:  "手動補正" marker — one column per plan month (identifies the
          plan-month columns, same marker parse_plan1_sheet already uses).
  row 3:  前月比(補正値) — per-column seasonal MoM default (feeds a SKU
          formula's "AP$4" reference).
  row 5:  全体手動補正(%) — per-column manual override, blank unless a
          planner typed one in (feeds a SKU formula's "AP$6" reference).
  date row (first row with datetime values in one of the marker columns,
          scanned like parse_plan1_sheet does): the plan month headers.
  rows below the date row: one per SKU, name in column B (index 1); a
  formula like `=IF(AP$6="",AO28*AP$4,AO28*AP$6)` is the standard "chain"
  step engine/chain.py's classify_formula() recognizes via the `$6=""`
  literal — confirmed against real formulas in the file 2026-07-07.
"""
from __future__ import annotations

from datetime import datetime

from ..config import planning_file
from .excel import read_xlsx_rows

SHEETS = ["コンシューマー", "コマーシャル"]
MOM_ROW = 3
MANUAL_ROW = 5


def load_plan_chain(sheet: str) -> dict:
    """→ {"months": [...], "mom": [...], "manual": [...],
    "skus": [{"name":.., "cells": [{"value":.., "formula":..}, ...]}]}
    — the exact shape engine/chain.py's reproduce_chain expects, sourced
    directly from ① (no poc_v0 dependency)."""
    path = planning_file("①")
    values = read_xlsx_rows(path, sheet, data_only=True)
    formulas = read_xlsx_rows(path, sheet, data_only=False)

    date_cols = [i for i, v in enumerate(values[2]) if v == "手動補正"]
    if not date_cols:
        return {"sheet": sheet, "months": [], "mom": [], "manual": [], "skus": []}
    date_row_idx = next((r for r, row in enumerate(values[:30])
                        if isinstance(row[date_cols[0]], datetime)), None)
    if date_row_idx is None:
        return {"sheet": sheet, "months": [], "mom": [], "manual": [], "skus": []}

    months = [values[date_row_idx][c] for c in date_cols]
    mom = [values[MOM_ROW][c] for c in date_cols]
    manual = [values[MANUAL_ROW][c] for c in date_cols]

    skus = []
    for ri in range(date_row_idx + 1, len(values)):
        name = values[ri][1]
        if name is None or str(name).strip() in ("", "-") or "合計" in str(name):
            continue
        cells = [{"value": values[ri][c], "formula": formulas[ri][c]}
                for c in date_cols]
        # Skip the text sub-header row right after the date row (e.g.
        # "当月見込(法込)"/"6月"...) and any other non-numeric row — real SKU
        # cells are always a number or blank.
        if any(c["value"] is not None and not isinstance(c["value"], (int, float))
              for c in cells):
            continue
        if all(c["value"] is None for c in cells):
            continue
        skus.append({"name": str(name).strip(), "cells": cells})

    return {"sheet": sheet, "months": months, "mom": mom, "manual": manual,
           "skus": skus}
