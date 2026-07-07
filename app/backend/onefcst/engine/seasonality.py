"""Tier-1 seasonality engine — pure functions, no I/O.

Reproduces ①係数計算's proven math (validated 48/48 cells in poc_v0.py):
5-期 average → 年間構成比 (annual share) → 先月比 (MoM ratio) → 季節指数.
Kept as pure functions here so the SAME code both computes forecasts and is
checked cell-by-cell against the Excel in verify/parity.py (Gate B).
"""
from __future__ import annotations

FISCAL_MONTHS = ["8月", "9月", "10月", "11月", "12月", "1月",
                 "2月", "3月", "4月", "5月", "6月", "7月"]


def compute_seasonality(history: dict[int, list[float]], kis: list[int]) -> dict:
    """history: {期number: [12 monthly values, Aug..Jul]}. kis: which 期 to average."""
    rows = [history[k] for k in kis]
    avg = [sum(r[m] for r in rows) / len(rows) for m in range(12)]
    total = sum(avg)
    share = [v / total for v in avg]
    grand_avg = total / 12
    mom = [None] * 12
    for m in range(12):
        prev = avg[11] if m == 0 else avg[m - 1]
        mom[m] = avg[m] / prev if prev else None
    season = [v / grand_avg for v in avg]
    return {"avg": avg, "share": share, "mom": mom, "season": season}


def compute_landing(history: dict[int, list[float]], share: list[float],
                    current_ki: int) -> dict:
    """着地予測: actuals for completed months + YTD-share extrapolation for the rest.

    Returns BOTH variants:
      as_in_file — reproduces ①'s formula exactly (PRIOR-期 YTD reference +
                   frozen 6-month window in the last 3 columns — both are
                   defects confirmed with planning, kept for parity checking)
      corrected  — current-期 YTD, consistent growing window (used for the
                   actual forecast the system surfaces)
    """
    cur = history[current_ki]
    prior = history[current_ki - 1]
    done = [i for i, v in enumerate(cur) if v is not None]
    n_done = max(done) + 1 if done else 0

    def build(ytd_src, growing):
        out = []
        for m in range(12):
            if m < n_done:
                out.append(cur[m])
                continue
            w = (m if m <= 8 else 6) if growing else n_done
            ytd = sum(v for v in ytd_src[:w] if v is not None)
            out.append(ytd / sum(share[:w]) * share[m] if sum(share[:w]) else None)
        return out

    return {"as_in_file": build(prior, True), "corrected": build(cur, False),
            "n_done": n_done}
