"""Parsers for ONE計画.xlsm — the hub: 部品マスター, 部品予測.

部品マスター → commercial attributes (MOQ/LT/min-stock/suppliers) per 綽名.
部品予測    → category (大分類/中分類), EOL flag, and the master plan values
              (monthly columns, level='master').
"""
from __future__ import annotations

from datetime import datetime

from ..config import ONE_MASTER
from .excel import read_xlsx_rows, col_letter

SHEET_MASTER = "部品マスター"
SHEET_FORECAST = "部品予測"
# 部品予測 claims 16k+ columns; real date headers end well before this.
FORECAST_MAX_COL = 400
FORECAST_MAX_ROW = 1500


def parse_parts_master() -> list[dict]:
    """部品マスター rows → dicts (one per 商品コード; 綽名 repeats across codes)."""
    rows = read_xlsx_rows(ONE_MASTER, SHEET_MASTER)
    out = []
    for r in rows[1:]:
        if not r or r[0] is None or r[2] in (None, ""):
            continue
        out.append({
            "code": str(r[0]).strip(),
            "product_name": str(r[1] or "").strip(),
            "adana": str(r[2]).strip(),
            "bunrui": str(r[3] or "").strip(),
            "min_stock_days": r[4] if isinstance(r[4], (int, float)) else None,
            "moq": r[7] if isinstance(r[7], (int, float)) else None,
            "lead_time_days": r[8] if isinstance(r[8], (int, float)) else None,
            "supplier_a": (str(r[9]).strip() or None) if r[9] not in (None, 0) else None,
            "supplier_b": (str(r[10]).strip() or None) if r[10] not in (None, 0) else None,
            "supplier_c": (str(r[11]).strip() or None) if r[11] not in (None, 0) else None,
        })
    return out


def parse_parts_forecast() -> dict:
    """部品予測 → {'items': [...], 'plan': [...], 'months': [...]}.

    Header layout (verified 2026-07-06): row 3 = 番号|大分類|中分類|綽名|汎用コード|
    BC|EOL|<monthly datetime columns...>. Data from row 4.
    """
    rows = read_xlsx_rows(ONE_MASTER, SHEET_FORECAST,
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
            "generic_code": str(r[4]).strip() if r[4] not in (None, "") else None,
            "eol": bool(r[6]) if r[6] is not None else False,
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
