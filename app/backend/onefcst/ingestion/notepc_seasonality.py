"""Parser for NOTE_STOCK_PLAN's own 参考資料(係数 通販+店舗　台数実績) sheet —
the NotePC-side equivalent of desktop's ①係数計算 (poc_v0.load_keisuu).

Same underlying methodology as desktop: per-brand monthly unit actuals by
fiscal 期 (Aug-start, 12 months), from which engine/seasonality.py's generic
compute_seasonality/compute_landing derive a 5-期 seasonal share — no new
math needed, this only supplies NotePC's own history in the same shape
({期: [12 monthly values, Aug..Jul]}) that those functions already expect.

Brands: GG (Gaming), GC (Creator), TW (THIRDWAVE) — the three lines this
sheet tracks under "通販+店舗" (mail-order + store).

Row layout (verified 2026-07-07, one block per brand, 1-based Excel rows):
  GG  rows 14-24   (期34 16-17 .. 期44 26-27)
  GC  rows 47-57
  TW  rows 82-92
col0=通販+店舗 (first row only), col1=brand/alias, col2=期 number, col3=年,
col4..15=12 months Aug..Jul.

期43 (25-26) is EXCLUDED: confirmed by direct cell inspection that all three
brands carry the byte-identical row (2771, 2510, 2554, 1992, 2843, 1773,
1634, 2461, 2049, 2194, 2347.5, None) — a copy/paste or broken cross-sheet
formula in the source file, not real per-brand data. 期44 is future/empty.
Only 期34-42 (9 complete fiscal years) are trustworthy history.
"""
from __future__ import annotations

from ..config import NOTE_STOCK_PLAN
from .excel import read_xlsx_rows

SHEET = "参考資料(係数 通販+店舗　台数実績)"
BRAND_HEADER_ROWS = {"GG": 14, "GC": 47, "TW": 82}   # 1-based first-data row
CORRUPTED_KI = {43}                                   # duplicate-row bug, see module docstring


def _read_brand(rows: list, start_row: int) -> dict[int, list[float | None]]:
    history: dict[int, list] = {}
    ri = start_row - 1
    while ri < len(rows):
        r = rows[ri]
        ki = r[2]
        if not isinstance(ki, (int, float)):
            break
        ki = int(ki)
        if ki not in CORRUPTED_KI:
            history[ki] = list(r[4:16])
        ri += 1
    return history


def load_notepc_history() -> dict[str, dict[int, list]]:
    """→ {brand: {期: [12 monthly unit values, Aug..Jul]}} for GG/GC/TW,
    each ready to hand straight to engine.seasonality.compute_seasonality."""
    rows = read_xlsx_rows(NOTE_STOCK_PLAN, SHEET, max_row=100, max_col=16)
    return {brand: _read_brand(rows, start_row)
            for brand, start_row in BRAND_HEADER_ROWS.items()}
