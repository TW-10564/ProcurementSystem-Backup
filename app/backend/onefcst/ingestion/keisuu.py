"""Parser for desktop's ①係数計算 sheet — the desktop-side equivalent of
NotePC's 参考資料(係数 通販+店舗　台数実績) (see notepc_seasonality.py).

Supplies two things, both sourced straight from ①'s own cells (no dependency
on the deleted forecasting/poc_v0.py):

  load_keisuu_history() — {期: [12 monthly PC-unit values, Aug..Jul]} for
  33期-42期, in the exact shape engine/seasonality.py's compute_seasonality
  and verify/backtest.py's walk-forward loop expect.

  load_keisuu_reference() — the sheet's OWN cached formula results for its
  "過去5期(41-37)" block (平均/年間構成比/先月比/季節指数, rows 22-25) and its
  "42期着地予測" row (13). These are Excel's own last-computed values, not a
  script's snapshot — used by verify/parity.py (Gate B) as the ground truth
  that engine/seasonality.py's compute_seasonality/compute_landing must
  reproduce.

Row layout (係数計算 sheet, verified 2026-07-07, 1-based Excel rows):
  rows 3-12:  <期label>期(<years>), col B=期 label, cols C..N = 12 monthly
              values Aug..Jul, col O = 合計. 42期(row 12) is still in
              progress (partial actuals, rest blank) — expected, not an error.
  row 13:     42期着地予測 — the sheet's own landing forecast, cols C..N.
  row 22:     平均 (過去5期(41-37)), cols C..N.
  row 23:     年間構成比, cols C..N.
  row 24:     先月比(平均), cols C..N.
  row 25:     季節指数(平均), cols C..N.
"""
from __future__ import annotations

import re

from ..config import planning_file
from .excel import read_xlsx_rows

SHEET = "係数計算"
KI_RE = re.compile(r"(\d+)期")


def load_keisuu_history() -> dict[int, list]:
    """→ {期number: [12 monthly unit values, Aug..Jul]} for 33期-42期."""
    path = planning_file("①")
    rows = read_xlsx_rows(path, SHEET, max_row=15, max_col=14)
    history: dict[int, list] = {}
    for row in rows[2:12]:                      # 1-based rows 3-12
        label = row[0]
        if not isinstance(label, str):
            continue
        m = KI_RE.match(label)
        if not m:
            continue
        history[int(m.group(1))] = list(row[1:13])
    return history


def load_keisuu_reference() -> dict:
    """→ the sheet's own cached 過去5期(41-37) seasonality figures and 42期
    着地予測, straight from Excel's last-computed cell values (Gate B ground
    truth)."""
    path = planning_file("①")
    rows = read_xlsx_rows(path, SHEET, max_row=30, max_col=14)
    return {
        "landing": list(rows[12][1:13]),   # row 13: 42期着地予測
        "avg": list(rows[21][1:13]),       # row 22: 平均
        "share": list(rows[22][1:13]),     # row 23: 年間構成比
        "mom": list(rows[23][1:13]),       # row 24: 先月比(平均)
        "season": list(rows[24][1:13]),    # row 25: 季節指数(平均)
    }
