"""
Desktop master — history-aggregation PoC.

Demonstrates "we can automate the desktop master" SAFELY:
  • The desktop master 部品予測 = copied-from-sub-files ACTUALS (past) + a
    先月比 FORECAST (forward). We proved (value comparison) the past months are
    a 94-100% direct copy of the 17 sub-files' 生産予測.
  • This module reproduces that history mechanically: read each sub-file's
    生産予測 → aggregate into the master's 部品予測 layout → and RECONCILE
    against the real master to show the match.
  • The forward months (current month onward) are left as a clearly-marked
    ★ FORECAST SLOT — the one pluggable component (先月比 now, better model later).

This is the safe ~80%: no forecasting, just aggregation + a match-rate gate.

Run:  python3 master_desktop.py
Outputs: reconciliation report (stdout) + 部品予測_自動生成_YYYYMMDD.xlsx
"""
import warnings
from datetime import datetime, date, timedelta
from pathlib import Path

import openpyxl
import pyxlsb
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

from config_all import FILES, BASE

warnings.filterwarnings("ignore")
HERE = Path(__file__).parent

# Master 大分類 → which sub-file category(ies) feed it.
# PoC scope = the monthly anchor categories we verified at 94-100% match.
# (CPU is weekly → needs weekly→monthly aggregation; added later.)
CATEGORY_MAP = {
    "ﾏｻﾞｰﾎﾞｰﾄﾞ":        ["MB"],
    "グラフィックカード":  ["VGA", "RTX"],
    "SSD":              ["SSD"],
    "メモリ":            ["MEM"],
    "PCｹｰｽ":           ["Case"],
}

MASTER_PATH = BASE / "ONE計画.xlsm"


# ── date helpers ──────────────────────────────────────────────────────────────

def to_ym(v):
    """Return (year, month) for a datetime/date/Excel-serial, else None."""
    if isinstance(v, (datetime, date)):
        return (v.year, v.month)
    if isinstance(v, (int, float)) and 40000 < v < 60000:
        d = datetime(1899, 12, 30) + timedelta(days=float(v))
        return (d.year, d.month)
    return None


def snapshot_actual_through(today=None):
    """Last fully-actual month = the month BEFORE the current one."""
    today = today or date.today()
    y, m = today.year, today.month
    return (y - 1, 12) if m == 1 else (y, m - 1)


# ── sub-file 生産予測 reader ──────────────────────────────────────────────────

def read_seisan(cat_key):
    """Read a sub-file's 生産予測 → {綽名: {(y,m): value}}. Auto-detects the
    date-header row and the 綽名 column (handles both .xlsb and .xlsx)."""
    cfg = FILES[cat_key]
    path = BASE / cfg["file"]
    rows = []
    if cfg["fmt"] == "xlsb":
        with pyxlsb.open_workbook(str(path)) as wb:
            if "生産予測" not in wb.sheets:
                return {}
            with wb.get_sheet("生産予測") as ws:
                for row in ws.rows():
                    rows.append({c.c: c.v for c in row})
    else:
        wb = openpyxl.load_workbook(str(path), data_only=True)
        if "生産予測" not in wb.sheetnames:
            return {}
        ws = wb["生産予測"]
        for row in ws.iter_rows(values_only=False):
            rows.append({c.column - 1: c.value for c in row})

    # find the date-header row (>=6 parseable dates)
    hdr = None
    for ri, row in enumerate(rows[:6]):
        dc = {c: to_ym(v) for c, v in row.items() if to_ym(v)}
        if len(dc) >= 6:
            hdr = (ri, dc)
            break
    if not hdr:
        return {}
    ri, date_cols = hdr

    out = {}
    for row in rows[ri + 1:]:
        for namecol in (1, 0):  # 綽名 is usually col 1, sometimes col 0
            nm = row.get(namecol)
            if isinstance(nm, str) and nm.strip() and nm not in ("実績", "予算", "月次予測"):
                vals = {ym: float(row.get(c)) for c, ym in date_cols.items()
                        if isinstance(row.get(c), (int, float))}
                if vals:
                    out[nm.strip()] = vals
                break
    return out


# ── master 部品予測 reader (for reconciliation) ───────────────────────────────

def read_master_buhin_yosoku():
    """Read the real master 部品予測 → {(大分類, 綽名): {(y,m): value}}."""
    ws = openpyxl.load_workbook(str(MASTER_PATH), data_only=True)["部品予測"]
    hdr = [ws.cell(row=3, column=c).value for c in range(1, 131)]
    date_cols = {c + 1: to_ym(v) for c, v in enumerate(hdr) if to_ym(v)}
    out = {}
    for r in range(4, ws.max_row + 1):
        cat = ws.cell(row=r, column=2).value
        nm = ws.cell(row=r, column=4).value
        if not nm:
            continue
        out[(str(cat).strip(), str(nm).strip())] = {
            ym: float(ws.cell(row=r, column=c).value)
            for c, ym in date_cols.items()
            if isinstance(ws.cell(row=r, column=c).value, (int, float))}
    return out, sorted(set(date_cols.values()))


# ── build aggregated history ──────────────────────────────────────────────────

def build_aggregated(actual_through):
    """For each master category in scope, aggregate sub-file 生産予測 history
    (months <= actual_through). Returns {(大分類, 綽名): {(y,m): value}}."""
    agg = {}
    for master_cat, sub_keys in CATEGORY_MAP.items():
        merged = {}
        for k in sub_keys:
            merged.update(read_seisan(k))
        for nm, months in merged.items():
            hist = {ym: v for ym, v in months.items() if ym <= actual_through}
            if hist:
                agg[(master_cat, nm)] = hist
    return agg


# ── forecast helpers — NOT USED in Phase 1 ────────────────────────────────────
# Phase 1 scope = automate the Excel, do NOT compute forecasts. The forward
# months DISPLAY the master's existing (human-produced) values instead. These
# functions are retained only for a future phase if/when forecasting is added.

def forecast_history(hist, slot_months, lookback=5):
    """Fill the forward 'slot' months with a forecast value.

    Method = 先月比 level (parity with the master's current approach), made
    STABLE: take the recent level (avg of last 3 actuals) and nudge it once by
    the average recent month-over-month ratio, then hold flat. The ratio is
    clamped to avoid the compounding explosion that the raw 先月比 suffers.
    This is the swappable component — a better model drops in here later.
    """
    if not hist:
        return {ym: None for ym in slot_months}
    ms = sorted(hist)
    vals = [hist[m] for m in ms]
    recent = [v for v in vals[-3:] if v is not None]
    base = sum(recent) / len(recent) if recent else 0.0
    ratios = []
    for i in range(max(1, len(vals) - lookback), len(vals)):
        p = vals[i - 1]
        if p and p > 0 and vals[i] is not None:
            ratios.append(vals[i] / p)
    ratio = sum(ratios) / len(ratios) if ratios else 1.0
    ratio = min(max(ratio, 0.5), 1.5)
    level = round(base * ratio, 1)
    return {ym: level for ym in slot_months}


def compute_forecast(agg, slot_months, lookback=5):
    """Forecast every aggregated part across the forward slot months."""
    return {key: forecast_history(hist, slot_months, lookback)
            for key, hist in agg.items()}


# ── reconcile aggregated vs real master ───────────────────────────────────────

def reconcile(agg, master, actual_through):
    """Per-category match rate of aggregated history vs master history."""
    from collections import defaultdict
    stats = defaultdict(lambda: {"match": 0, "total": 0, "parts": 0})
    for (cat, nm), months in agg.items():
        if (cat, nm) not in master:
            continue
        stats[cat]["parts"] += 1
        for ym, v in months.items():
            mv = master[(cat, nm)].get(ym)
            if mv is None:
                continue
            stats[cat]["total"] += 1
            if abs(mv - v) < 0.5:
                stats[cat]["match"] += 1
    return stats


# ── Excel output ──────────────────────────────────────────────────────────────

HDR_FILL = PatternFill("solid", fgColor="1F4E78")
HDR_FONT = Font(color="FFFFFF", bold=True, size=9)
SLOT_FILL = PatternFill("solid", fgColor="FFF2CC")   # forecast slot = amber
HIST_FILL = PatternFill("solid", fgColor="E2EFDA")   # history = green
TITLE = Font(bold=True, size=13, color="1F4E78")
THIN = Side(style="thin", color="D9D9D9")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def write_excel(agg, master, all_months, actual_through, stats, out_path):
    wb = openpyxl.Workbook()

    # ── Sheet 1: aggregated 部品予測 (history + forecast slot) ──
    ws = wb.active
    ws.title = "部品予測(自動生成)"
    ws["A1"] = "部品予測 — 自動生成（履歴=サブ文書から集計／予測=スロット）"
    ws["A1"].font = TITLE
    ws["A2"] = (f"緑=履歴(サブ文書の生産予測を自動集計, 〜{actual_through[0]}-{actual_through[1]:02d})　／　"
                f"黄=予測(現行マスターの値を表示・人手, システムは予測計算をしません)")
    ws["A2"].font = Font(italic=True, size=9, color="808080")

    months = [m for m in all_months if m <= (actual_through[0] + 2, 12)]  # show history + a few slot cols
    hrow = 4
    headers = ["大分類", "綽名"] + [f"{y}-{m:02d}" for (y, m) in months]
    for j, h in enumerate(headers, 1):
        c = ws.cell(row=hrow, column=j, value=h)
        c.fill = HDR_FILL; c.font = HDR_FONT; c.alignment = Alignment(horizontal="center")
        c.border = BORDER
    # mark forecast-slot header columns amber
    for j, ym in enumerate(months, 3):
        if ym > actual_through:
            ws.cell(row=hrow, column=j).fill = SLOT_FILL
            ws.cell(row=hrow, column=j).font = Font(bold=True, size=9, color="7F6000")

    r = hrow + 1
    for (cat, nm) in sorted(agg.keys()):
        ws.cell(row=r, column=1, value=cat).border = BORDER
        ws.cell(row=r, column=2, value=nm).border = BORDER
        for j, ym in enumerate(months, 3):
            cell = ws.cell(row=r, column=j)
            cell.border = BORDER
            if ym <= actual_through:
                v = agg[(cat, nm)].get(ym)
                if v is not None:
                    cell.value = round(v, 1)
            else:
                # forecast region = DISPLAY the master's existing forecast value
                # as-is (human-produced). The system does NOT compute forecasts.
                v = master.get((cat, nm), {}).get(ym)
                if v is not None:
                    cell.value = round(v, 1)
                cell.fill = SLOT_FILL       # amber = forecast region (existing master value)
                cell.font = Font(italic=True, size=8, color="BF9000")
        r += 1
    ws.freeze_panes = "C5"
    ws.column_dimensions["A"].width = 16
    ws.column_dimensions["B"].width = 22

    # ── Sheet 2: reconciliation (proof) ──
    ws2 = wb.create_sheet("照合レポート")
    ws2["A1"] = "照合レポート — 自動集計 vs 実マスター部品予測（履歴）"
    ws2["A1"].font = TITLE
    head = ["大分類", "対象部品数", "照合セル数", "一致", "一致率"]
    for j, h in enumerate(head, 1):
        c = ws2.cell(row=3, column=j, value=h)
        c.fill = HDR_FILL; c.font = HDR_FONT; c.border = BORDER
    r = 4
    tot_m = tot_t = 0
    for cat, s in stats.items():
        rate = 100 * s["match"] / s["total"] if s["total"] else 0
        tot_m += s["match"]; tot_t += s["total"]
        for j, v in enumerate([cat, s["parts"], s["total"], s["match"], f"{rate:.1f}%"], 1):
            cell = ws2.cell(row=r, column=j, value=v); cell.border = BORDER
        ws2.cell(row=r, column=5).fill = HIST_FILL if rate >= 90 else SLOT_FILL
        r += 1
    overall = 100 * tot_m / tot_t if tot_t else 0
    ws2.cell(row=r + 1, column=1, value="合計").font = Font(bold=True)
    ws2.cell(row=r + 1, column=3, value=tot_t).font = Font(bold=True)
    ws2.cell(row=r + 1, column=4, value=tot_m).font = Font(bold=True)
    ws2.cell(row=r + 1, column=5, value=f"{overall:.1f}%").font = Font(bold=True, color="375623")
    for col, w in zip("ABCDE", [16, 12, 12, 10, 10]):
        ws2.column_dimensions[col].width = w

    wb.save(out_path)
    return overall


# ── main ──────────────────────────────────────────────────────────────────────

def build_portal_data():
    """JSON-able summary for the portal 'master automation' tab:
    reconciliation per category + a preview of the aggregated table."""
    actual_through = snapshot_actual_through()
    agg = build_aggregated(actual_through)
    master, all_months = read_master_buhin_yosoku()
    stats = reconcile(agg, master, actual_through)

    cats, tot_m, tot_t = [], 0, 0
    for cat, s in stats.items():
        rate = round(100 * s["match"] / s["total"], 1) if s["total"] else 0
        tot_m += s["match"]; tot_t += s["total"]
        cats.append({"cat": cat, "parts": s["parts"], "cells": s["total"],
                     "match": s["match"], "rate": rate})
    overall = round(100 * tot_m / tot_t, 1) if tot_t else 0

    # preview: recent 6 history months + 3 slot months for ~40 parts
    hist_months = [m for m in all_months if m <= actual_through][-6:]
    slot_months = [m for m in all_months if m > actual_through][:3]
    cols = hist_months + slot_months
    preview = []
    for (cat, nm) in sorted(agg.keys())[:40]:
        row = {"cat": cat, "sku": nm, "cells": []}
        for ym in cols:
            if ym <= actual_through:
                v = agg[(cat, nm)].get(ym)
                row["cells"].append({"v": round(v, 1) if v is not None else None, "slot": False})
            else:
                # forecast region = existing master value (displayed, not computed)
                v = master.get((cat, nm), {}).get(ym)
                row["cells"].append({"v": round(v, 1) if v is not None else None, "slot": True})
        preview.append(row)

    return {
        "actual_through": f"{actual_through[0]}-{actual_through[1]:02d}",
        "slot_from": f"{slot_months[0][0]}-{slot_months[0][1]:02d}" if slot_months else "",
        "overall": overall, "categories": cats,
        "months": [f"{y}-{m:02d}" for (y, m) in cols],
        "hist_count": len(hist_months), "preview": preview,
    }


def run():
    actual_through = snapshot_actual_through()
    print(f"Desktop master history-aggregation PoC")
    print(f"  master: {MASTER_PATH.name}")
    print(f"  actual-through month: {actual_through[0]}-{actual_through[1]:02d}  "
          f"(later months = ★forecast slot)\n")

    print("  reading sub-file 生産予測 + aggregating history...")
    agg = build_aggregated(actual_through)
    master, all_months = read_master_buhin_yosoku()
    stats = reconcile(agg, master, actual_through)

    print("\n  === RECONCILIATION: auto-aggregated vs real master (history) ===")
    print(f"  {'大分類':<16}{'parts':>7}{'cells':>8}{'match':>8}{'rate':>8}")
    print("  " + "-" * 50)
    tot_m = tot_t = 0
    for cat, s in stats.items():
        rate = 100 * s["match"] / s["total"] if s["total"] else 0
        tot_m += s["match"]; tot_t += s["total"]
        print(f"  {cat:<16}{s['parts']:>7}{s['total']:>8}{s['match']:>8}{rate:>7.1f}%")
    overall = 100 * tot_m / tot_t if tot_t else 0
    print("  " + "-" * 50)
    print(f"  {'TOTAL':<16}{'':>7}{tot_t:>8}{tot_m:>8}{overall:>7.1f}%")

    out = HERE / f"部品予測_自動生成_{date.today():%Y%m%d}.xlsx"
    write_excel(agg, master, all_months, actual_through, stats, out)
    print(f"\n  ✓ history reproduced from sub-files at {overall:.1f}% match")
    print(f"  ✓ forward months DISPLAY the master's existing forecast (no computation)")
    print(f"  → {out.name}")
    return overall


if __name__ == "__main__":
    run()
