"""Parsers for ④部材利用数 — the planning→ONE bridge.

ﾏｽﾀ sheet          → alias mapping 企画名前 → ONE名前(綽名) → 商品コード.
ONE計画貼り付け     → the planning dept's parts plan per 綽名 (level='planning4').
その他ONE貼り付け   → same for misc parts, with 大分類.
"""
from __future__ import annotations

from datetime import datetime

from ..config import planning_file
from .excel import read_xlsx_rows, col_letter


def _f4():
    return planning_file("④")


def parse_masta_aliases() -> list[dict]:
    rows = read_xlsx_rows(_f4(), "ﾏｽﾀ")
    hdr_idx = None
    for i, r in enumerate(rows[:5]):
        cells = [str(c or "") for c in r]
        if any("ONE" in c or "綽名" in c for c in cells):
            hdr_idx = i
            break
    if hdr_idx is None:
        hdr_idx = 0
    out = []
    for r in rows[hdr_idx + 1:]:
        vals = [str(c).strip() if c not in (None, "") else None for c in r[:4]]
        if not vals or not any(vals):
            continue
        plan_name, one_name = vals[0], vals[1] if len(vals) > 1 else None
        code = vals[2] if len(vals) > 2 else None
        if plan_name and one_name:
            out.append({"plan_name": plan_name, "one_name": one_name, "code": code})
    return out


def parse_one_paste() -> list[dict]:
    """その他ONE貼り付け: 汎用コード | 綽名 | <month value> | 大分類 (all parts)."""
    rows = read_xlsx_rows(_f4(), "その他ONE貼り付け")
    hdr = rows[0]
    month = next((v for v in hdr if isinstance(v, datetime)), None)
    out = []
    for ri, r in enumerate(rows[1:], 2):
        adana = r[1]
        if adana in (None, ""):
            continue
        out.append({
            "adana": str(adana).strip(),
            "generic_code": str(r[0]).strip() if r[0] not in (None, "") else None,
            "month": month.date().replace(day=1) if month else None,
            "value": float(r[2]) if isinstance(r[2], (int, float)) else None,
            "category": str(r[3] or "").strip() or None,
            "cell": f"その他ONE貼り付け!{col_letter(2)}{ri}",
        })
    return out
