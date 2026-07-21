"""Sub-file 部品詳細 parser — full monthly 生産計画 series per SKU (level='one_row').

Reuses the PROVEN Phase-0 layouts, now copied in-house into
ingestion/subfile_config.py (per-file label_col / name_col / first_date_col;
4-row blocks 生産計画/購買計画/在庫残高/在庫日数) — see that module's
docstring for why this isn't a live import from consolidation/ anymore.
Phase-0 extracted one snapshot column; here we capture the whole monthly
series for the three-level plan diff. Also captures the current
在庫残高/在庫日数 snapshot per SKU.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from .subfile_config import FILES, BLOCK_LABELS, BASE
from .excel import col_letter, read_dict_rows, to_date, to_float

MAX_COVERAGE_GAP_DAYS = 10   # a month's own daily columns must start within
                              # this many days of day 1 AND end within this
                              # many days of month-end, or the file's column
                              # range doesn't actually cover the whole month —
                              # same completeness guard verify/sales_reconciliation.py
                              # already applies to SAP's rolling shipment window.
                              # Found 2026-07-21: ONE_08_MB_社外秘.xlsb's own daily
                              # range starts 2026-03-29, so summing "every column
                              # in March" only summed 3 real days — a total ~10x
                              # (31/3) lower than the real month, which then read
                              # as a false master/one_row "disagreement" for 31
                              # unrelated items in verify/reconciliation.py. Every
                              # sub-file's rolling column window has a start and
                              # end edge; whichever calendar month that edge falls
                              # inside is at risk of the same silent undercount.


def parse_subfile_series(cat: str, snapshot_date: date | None = None) -> dict:
    """One category file → {'series': [...], 'stock': [...], 'stats': {...}}.

    series: one record per SKU × month, SUMMED from the 生産計画 row's DAILY
    columns for that calendar month.

    Found 2026-07-08 during a Data Summary cross-check: every one of the 17
    sub-files has purely DAILY date columns (confirmed: 1-day gaps, ~730-800
    columns spanning ~2 years) — there is no separate "monthly" column. The
    previous version picked out only the column whose date happened to land
    on day==1 and used THAT SINGLE DAY's value as if it were the whole
    month's total, undercounting by roughly the number of days in the month
    (~30x). This went unnoticed because master-level 部品予測 data (a
    different, genuinely-monthly file) wins over this one_row series for
    every item that has both — only items with ZERO master history (11 OS
    licenses + a few others) ever actually depend on this series, which is
    exactly where the ~30x undercount surfaced as a near-total forecast
    miss."""
    cfg = FILES[cat]
    snapshot_date = snapshot_date or date.today()
    path = Path(cfg.get("_abspath") or (BASE / cfg["file"]))
    label_col, name_col, fdc = cfg["label_col"], cfg["name_col"], cfg["first_date_col"]
    stats = {"category": cat, "file": cfg["file"], "skus": 0, "cells": 0,
             "expected_skus": cfg["expected_skus"], "note": ""}

    try:
        rows, present, names = read_dict_rows(path, cfg["fmt"], "部品詳細")
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
    cols_by_month: dict[date, list[int]] = defaultdict(list)
    for c, d in date_cols.items():
        cols_by_month[d.replace(day=1)].append(c)
    sorted_dates = sorted(date_cols.items(), key=lambda x: x[1])
    snap_col = next((c for c, d in sorted_dates if d >= snapshot_date),
                    sorted_dates[-1][0] if sorted_dates else None)

    # a month only counts as fully covered if its earliest present column is
    # near day 1 AND its latest present column is near month-end — otherwise
    # the file's own column range clips into that month (see MAX_COVERAGE_
    # GAP_DAYS docstring above) and summing "every column found" would
    # silently produce a partial-month total instead of a real one.
    incomplete_months: set[date] = set()
    for month, cols in cols_by_month.items():
        month_dates = [date_cols[c] for c in cols]
        next_month = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
        month_end = next_month - timedelta(days=1)
        if ((min(month_dates) - month).days > MAX_COVERAGE_GAP_DAYS
                or (month_end - max(month_dates)).days > MAX_COVERAGE_GAP_DAYS):
            incomplete_months.add(month)
    if incomplete_months:
        stats["excluded_incomplete_months"] = sorted(m.isoformat() for m in incomplete_months)

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
        for month, cols in cols_by_month.items():
            if month in incomplete_months:
                continue
            values = [v for c in cols if (v := to_float(r0.get(c))) is not None]
            if values:
                series.append({"adana": sku, "month": month, "value": sum(values),
                               "cell": f"部品詳細!{col_letter(cols[0])}{i + 1}"
                                       f"..{col_letter(cols[-1])}{i + 1}"})
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
