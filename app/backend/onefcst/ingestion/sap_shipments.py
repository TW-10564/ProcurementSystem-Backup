"""SAP部材管理_詳細版*.xlsx's 【クエリ】部材利用(出荷) sheet — real per-item
WEEKLY SHIPPED (出荷) quantities: procurement's own rolling 4-week query
against SAP shipment data, reusing the same workbook name_crosswalk.py
already reads (for its 製品名称読みかえ sheet) — no new source path needed.

Found 2026-07-15 (user's own observation, confirmed by inspecting the real
file): the `Actual` table — real per-item sales history, read by FVA
scoring (verify/fva.py) and the Forecast page's "latest selling" sort
(forecast_views._last_active_month_by_item) — had ZERO rows in the live DB.
Nothing in the ingestion pipeline had ever written to it. This is the
source that fixes that.

Sheet layout (verified against the real file, 397 rows, checked row by
row before writing this parser — do not assume it can't shift, this is a
hand-maintained procurement query sheet, not a stable system export):
  col A: 読みかえ関数 output (converted display name), or "該当無し" when
         the conversion found no match — try this FIRST when resolving.
  col B: the raw SAP-side code/label (closer to 綽名 style) — fallback.
  cols C-F: 4 weekks of shipped qty, one column per week.
  Two header rows (found at whatever row index actually holds datetimes,
  not hardcoded) carry the week-ending date per column, but two different
  product lines (raytrek/GALLERIA) show ANCHOR DATES OFFSET BY UP TO 6
  DAYS in the same column position — a real ambiguity in the source itself
  (their own ERP week boundary differs slightly by brand), not a parsing
  bug. This parser uses the FIRST header row's dates uniformly; the
  resulting month bucket is correct either way for every date in every
  observed export (all mid-month, nowhere near a month boundary) — flagged
  here in case a future export puts a week label near a month edge.

  After the real per-item rows, the sheet continues with DERIVED/aggregate
  blocks — category 合計 (subtotal) rows, then (further down) a Windows
  OS/OEM-DSP breakdown and a CPU-tier percentage summary that RE-COUNTS
  the CPU rows already parsed above by tier (u9/i9/r9/u7/i7/r7/...).
  Including any of that would double-count or resolve against dangerously
  generic 2-3 character labels ("i5", "r7", "OEM"). This parser hard-stops
  at the first "Windows11" row — verified this is always the first row of
  that derived block in the real file, well after every real per-item row."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .excel import read_xlsx_rows
from .name_crosswalk import latest_crosswalk_file

SHEET_NAME = "【クエリ】部材利用(出荷)"
STOP_AT_LABEL = "Windows11"   # first row of the derived/aggregate tail block
_SKIP_LABELS = {"合計", "合算", None}


def _is_real_item_row(name_b) -> bool:
    if name_b in _SKIP_LABELS:
        return False
    if isinstance(name_b, str) and name_b.endswith("合計"):
        return False
    return True


def parse_sap_shipments(path: Path | None = None) -> dict | None:
    """Returns {"path", "week_ending_dates": [4 dates], "rows": [{"name_a",
    "name_b", "weekly_qty": [4 floats]}, ...]}, or None if the source
    workbook or its shipment sheet isn't available — optional input, same
    non-blocking pattern as the rest of ingestion (see
    name_crosswalk.py's own docstring for why)."""
    path = path or latest_crosswalk_file()
    if path is None:
        return None
    sheet_rows = read_xlsx_rows(path, SHEET_NAME, required=False)
    if not sheet_rows:
        return None

    week_ending_dates = None
    header_idx = None
    for i, row in enumerate(sheet_rows):
        tail = row[2:6] if len(row) >= 6 else ()
        if len(tail) == 4 and all(isinstance(v, datetime) for v in tail):
            week_ending_dates = [v.date() for v in tail]
            header_idx = i
            break
    if week_ending_dates is None:
        return None

    rows = []
    for row in sheet_rows[header_idx + 1:]:
        if not row or len(row) < 6:
            continue
        name_a, name_b = row[0], row[1]
        if name_b == STOP_AT_LABEL:
            break
        if not _is_real_item_row(name_b):
            continue
        weekly = row[2:6]
        if not all(isinstance(v, (int, float)) for v in weekly):
            continue   # blank spacer row
        rows.append({"name_a": name_a, "name_b": name_b,
                     "weekly_qty": [float(v) for v in weekly]})

    return {"path": path, "week_ending_dates": week_ending_dates, "rows": rows}


def aggregate_by_month(parsed: dict) -> dict[str, dict]:
    """{"name_a": ..., "name_b": ..., "months": {date: qty}} per row —
    sums whichever weekly columns land in the same calendar month (all
    observed exports keep 4 weeks inside one month, but this doesn't
    assume that). Feeds Actual (monthly grain — see its own docstring for
    why FVA/recency-sort need that grain specifically)."""
    dates = parsed["week_ending_dates"]
    months = [d.replace(day=1) for d in dates]
    out = []
    for row in parsed["rows"]:
        by_month: dict = {}
        for m, qty in zip(months, row["weekly_qty"]):
            by_month[m] = by_month.get(m, 0.0) + qty
        out.append({"name_a": row["name_a"], "name_b": row["name_b"], "months": by_month})
    return out


def flatten_weekly(parsed: dict) -> list[dict]:
    """{"name_a", "name_b", "weeks": {date: qty}} per row, at the RAW
    per-week grain (no month aggregation) — feeds WeeklyActual, for the
    plan-vs-actual weekly comparison view (forecast_views.plan_vs_actual).
    week_start here is whatever date the source sheet itself used as that
    column's anchor (see module docstring's raytrek/GALLERIA offset note —
    not forced onto a Monday-start convention; the comparison view handles
    aligning this against the Monday-anchored disaggregated forecast)."""
    dates = parsed["week_ending_dates"]
    out = []
    for row in parsed["rows"]:
        weeks = {d: qty for d, qty in zip(dates, row["weekly_qty"])}
        out.append({"name_a": row["name_a"], "name_b": row["name_b"], "weeks": weeks})
    return out
