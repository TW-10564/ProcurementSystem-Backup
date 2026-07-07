"""Parser for ①販売計画 — the Tier-1 model-level sales plan seed (level='planning1').

コンシューマー / コマーシャル sheets: 手動補正-labeled columns are the plan
months; SKU rows below the date row. We capture the plan VALUE per SKU per
month (not the coefficient rows) — this is the pre-attach-rate model-level
number, upstream of ④'s parts conversion.
"""
from __future__ import annotations

from datetime import datetime

from ..config import planning_file
from .excel import read_xlsx_rows, col_letter

SHEETS = ["コンシューマー", "コマーシャル"]


def parse_plan1_sheet(sheet: str) -> dict:
    path = planning_file("①")
    rows = read_xlsx_rows(path, sheet)
    date_cols = [i for i, v in enumerate(rows[2]) if v == "手動補正"]
    if not date_cols:
        return {"sheet": sheet, "rows": [], "months": []}
    date_row_idx = next((r for r, row in enumerate(rows[:30])
                        if isinstance(row[date_cols[0]], datetime)), None)
    if date_row_idx is None:
        return {"sheet": sheet, "rows": [], "months": []}
    months = [rows[date_row_idx][c] for c in date_cols]

    out = []
    for ri in range(date_row_idx + 1, len(rows)):
        row = rows[ri]
        name = row[1]
        if name is None or str(name).strip() in ("", "-") or "合計" in str(name):
            continue
        for ci, c in enumerate(date_cols):
            v = row[c]
            if isinstance(v, (int, float)):
                out.append({"raw_name": str(name).strip(),
                           "month": months[ci].date().replace(day=1),
                           "value": float(v),
                           "cell": f"{sheet}!{col_letter(c)}{ri + 1}"})
    return {"sheet": sheet, "rows": out, "months": [m.date().replace(day=1) for m in months]}


def parse_plan1() -> list[dict]:
    out = []
    for sheet in SHEETS:
        out.extend(parse_plan1_sheet(sheet)["rows"])
    return out
