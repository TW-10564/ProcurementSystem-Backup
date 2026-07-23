"""ZP21 "組込" (build-incorporation) data — manually-placed weekly exports,
one non-overlapping week per file (data/sap/組込/0525_0531組込.xlsx etc).

Found 2026-07-23: this REPLACES SAP部材管理_詳細版 (【クエリ】部材利用(出荷),
ingestion/sap_shipments.py) as the WeeklyActual source for
forecast_views.plan_vs_actual. Per procurement's own explanation (relayed
by the user, 2026-07-23): purchasing tracks demand by the timing a part is
incorporated into a build (組み込み), NOT by the timing the finished PC
ships — 組込数量 is the metric procurement actually uses, and can
meaningfully lead PC-shipment timing for build-to-stock items. The
shipment file measured the wrong timestamp for this app's purpose; this
one measures the right one.

Also structurally better than the shipment file for backfill purposes:
- Each file covers ONE non-overlapping week (開始日/終了日 columns are
  right there in the data, not a rolling 4-week window with an ambiguous
  per-brand anchor date like sap_shipments.py's source has to work around).
- One row per item (no aggregation across storage locations needed, unlike
  ingestion/sap_stock_history.py's ZM13 stock files) — except a rare
  same-file duplicate 品目 row (found 2026-07-23: 0622_0628組込.xlsx has
  521 rows for 520 distinct items), summed here defensively.
- 品目 resolves via the same 商品コード ItemAlias join key SYOCODE/ZM13
  already use — verified 2026-07-23: 584/773 (75.5%) of codes across the
  8 files on hand already resolve.

Content-based file discovery (sheet name "Data" + required headers), same
approach as ingestion/sap_stock_history.py, NOT folder-name-based — and
disambiguated from that module's ZM13 stock files (same "Data" sheet name)
by requiring 組込数量/開始日/終了日, which the stock files don't have."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from ..config import SAP_DIR
from .excel import read_xlsx_rows

SHEET_NAME = "Data"
REQUIRED_HEADERS = {"品目", "組込数量", "開始日", "終了日"}


def _to_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def find_zp21_files(base_dir: Path | None = None) -> list[Path]:
    base = base_dir or SAP_DIR
    hits = []
    for path in sorted(Path(base).rglob("*.xlsx")):
        try:
            rows = read_xlsx_rows(path, sheet=SHEET_NAME, max_row=1, required=False)
        except Exception:
            continue
        if not rows:
            continue
        header = {str(v) for v in rows[0] if v is not None}
        if REQUIRED_HEADERS <= header:
            hits.append(path)
    return hits


def parse_zp21_file(path: Path) -> dict | None:
    """One weekly snapshot: {"path", "week_start", "week_end", "rows":
    [{"code", "qty", "name_text"}]}. Returns None if the file's own
    開始日/終了日 column doesn't parse to a real date, or rows disagree on
    the week (found live 2026-07-23: every file checked carries exactly
    ONE uniform date pair across all its rows, but a hand-maintained export
    could in principle mix two — safer to bail than silently pick one)."""
    sheet_rows = read_xlsx_rows(path, sheet=SHEET_NAME)
    hdr = {str(v): i for i, v in enumerate(sheet_rows[0]) if v is not None}
    idx_code = hdr["品目"]
    idx_qty = hdr["組込数量"]
    idx_start = hdr["開始日"]
    idx_end = hdr["終了日"]
    idx_name = hdr.get("品目テキスト")

    agg: dict[str, dict] = {}
    week_starts: set[date] = set()
    week_ends: set[date] = set()
    for row in sheet_rows[1:]:
        if idx_code >= len(row):
            continue
        code = row[idx_code]
        if code in (None, ""):
            continue
        code = str(code).strip()
        qty = row[idx_qty] if idx_qty < len(row) else None
        if not isinstance(qty, (int, float)):
            continue
        start = _to_date(row[idx_start]) if idx_start < len(row) else None
        end = _to_date(row[idx_end]) if idx_end < len(row) else None
        if start is None or end is None:
            continue
        week_starts.add(start)
        week_ends.add(end)
        name_text = row[idx_name] if idx_name is not None and idx_name < len(row) else None
        entry = agg.setdefault(code, {"code": code, "qty": 0.0, "name_text": name_text})
        entry["qty"] += float(qty)
        if name_text:
            entry["name_text"] = name_text

    if len(week_starts) != 1 or len(week_ends) != 1:
        return None
    return {"path": path, "week_start": week_starts.pop(), "week_end": week_ends.pop(),
           "rows": list(agg.values())}


def parse_all_zp21(base_dir: Path | None = None) -> list[dict]:
    """One entry per distinct week_start found, oldest first."""
    by_week: dict[date, dict] = {}
    for path in find_zp21_files(base_dir):
        parsed = parse_zp21_file(path)
        if parsed is not None and parsed["week_start"] not in by_week:
            by_week[parsed["week_start"]] = parsed
    return [by_week[d] for d in sorted(by_week)]
