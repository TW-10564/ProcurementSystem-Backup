"""Parser for NoteONE計画.xlsm's own 部品予測 sheet — the NotePC-side
equivalent of ONE計画.xlsm's desktop 部品予測 (one_master.parse_parts_forecast).

Found 2026-07-07: this sheet has real forecast data (2020-02 → 2028-12, 250
parts) but the ingestion pipeline never read it — every NotePC part got
zero forecast coverage, not because the data doesn't exist, but because
nobody pointed a parser at it.

Layout is almost, but not quite, identical to the desktop file: NotePC adds
a 製品型番 (model number) column between 綽名 and 汎用コード, shifting every
column after 綽名 by one versus one_master.parse_parts_forecast — a
separate parser, not a shared one, avoids silently misreading either file
if one of them changes shape independently.

Header layout (verified 2026-07-07): row 3 = 番号|大分類|中分類|綽名|製品型番|
汎用コード|BC|EOL|<monthly datetime columns...>. Data from row 4.
"""
from __future__ import annotations

from datetime import datetime

from ..config import NOTE_ONE_MASTER
from .excel import read_xlsx_rows, col_letter

SHEET_FORECAST = "部品予測"
FORECAST_MAX_COL = 400
FORECAST_MAX_ROW = 1500


def parse_notepc_forecast() -> dict:
    """→ {'items': [...], 'plan': [...], 'months': [...]}, same shape as
    one_master.parse_parts_forecast() so it can feed the same registry/
    forecasting code paths."""
    rows = read_xlsx_rows(NOTE_ONE_MASTER, SHEET_FORECAST,
                          max_row=FORECAST_MAX_ROW, max_col=FORECAST_MAX_COL)
    hdr = rows[2]
    date_cols = [(ci, v) for ci, v in enumerate(hdr) if isinstance(v, datetime)]
    items, plan = [], []
    for ri, r in enumerate(rows[3:], 4):
        adana = r[3]
        if adana in (None, ""):
            continue
        adana = str(adana).strip()
        items.append({
            "adana": adana,
            "category": str(r[1] or "").strip().lstrip("\t") or None,
            "subcategory": str(r[2] or "").strip() or None,
            "model_no": str(r[4]).strip() if r[4] not in (None, "") else None,
            "generic_code": str(r[5]).strip() if r[5] not in (None, "") else None,
            "eol": bool(r[7]) if r[7] is not None else False,
        })
        for ci, month in date_cols:
            v = r[ci] if ci < len(r) else None
            if isinstance(v, (int, float)):
                plan.append({
                    "adana": adana,
                    "month": month.date().replace(day=1),
                    "value": float(v),
                    "cell": f"{SHEET_FORECAST}!{col_letter(ci)}{ri}",
                })
    return {"items": items, "plan": plan,
           "months": [d.date().replace(day=1) for _, d in date_cols]}
