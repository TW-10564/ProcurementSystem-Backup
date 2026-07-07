"""Sub-file 部品詳細 parser — full monthly 生産計画 series per SKU (level='one_row').

Reuses the PROVEN Phase-0 layouts from consolidation/config_all.py
(per-file label_col / name_col / first_date_col; 4-row blocks
生産計画/購買計画/在庫残高/在庫日数). Phase-0 extracted one snapshot column;
here we capture the whole monthly series for the three-level plan diff.
Also captures the current 在庫残高/在庫日数 snapshot per SKU.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

_CONSOLIDATION = Path(__file__).resolve().parents[4] / "consolidation"
if str(_CONSOLIDATION) not in sys.path:
    sys.path.insert(0, str(_CONSOLIDATION))

from config_all import FILES, BLOCK_LABELS, BASE          # noqa: E402
from consolidate import read_sheet, to_date, to_float     # noqa: E402

from .excel import col_letter                              # noqa: E402


def parse_subfile_series(cat: str, snapshot_date: date | None = None) -> dict:
    """One category file → {'series': [...], 'stock': [...], 'stats': {...}}.

    series: one record per SKU × month from the 生産計画 row (monthly cols only —
            部品詳細 headers mix weekly/monthly; we keep day==1 columns).
    """
    cfg = FILES[cat]
    snapshot_date = snapshot_date or date.today()
    path = Path(cfg.get("_abspath") or (BASE / cfg["file"]))
    label_col, name_col, fdc = cfg["label_col"], cfg["name_col"], cfg["first_date_col"]
    stats = {"category": cat, "file": cfg["file"], "skus": 0, "cells": 0,
             "expected_skus": cfg["expected_skus"], "note": ""}

    try:
        rows, present, names = read_sheet(path, cfg["fmt"])
    except Exception as e:                                  # pragma: no cover
        stats["note"] = f"READ ERROR: {e}"
        return {"series": [], "stock": [], "stats": stats}
    if not present or not rows:
        stats["note"] = "no 部品詳細"
        return {"series": [], "stock": [], "stats": stats}

    date_cols = {}
    for c, v in rows[0].items():
        if c < fdc:
            continue
        d = to_date(v)
        if d:
            date_cols[c] = d
    month_cols = {c: d for c, d in date_cols.items() if d.day == 1}
    sorted_dates = sorted(date_cols.items(), key=lambda x: x[1])
    snap_col = next((c for c, d in sorted_dates if d >= snapshot_date),
                    sorted_dates[-1][0] if sorted_dates else None)

    series, stock = [], []
    i = 0
    while i + 3 < len(rows):
        r0, r1, r2, r3 = rows[i], rows[i + 1], rows[i + 2], rows[i + 3]
        if (str(r0.get(label_col, "")).strip() != BLOCK_LABELS[0]
                or str(r1.get(label_col, "")).strip() != BLOCK_LABELS[1]
                or str(r2.get(label_col, "")).strip() != BLOCK_LABELS[2]
                or str(r3.get(label_col, "")).strip() != BLOCK_LABELS[3]):
            i += 1
            continue
        sku = str(r0.get(name_col, "")).strip()
        if not sku:
            i += 4
            continue
        stats["skus"] += 1
        for c, d in month_cols.items():
            v = to_float(r0.get(c))
            if v is not None:
                series.append({"adana": sku, "month": d.replace(day=1), "value": v,
                               "cell": f"部品詳細!{col_letter(c)}{i + 1}"})
                stats["cells"] += 1
        if snap_col is not None:
            stock.append({"adana": sku,
                          "balance": to_float(r2.get(snap_col)),
                          "days": to_float(r3.get(snap_col)),
                          "safety_days": to_float(r1.get(name_col)),
                          "snapshot": date_cols[snap_col]})
        i += 4
    return {"series": series, "stock": stock, "stats": stats}


def all_categories() -> list[str]:
    return list(FILES.keys())
