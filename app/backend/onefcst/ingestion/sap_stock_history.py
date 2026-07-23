"""Weekly-dated raw SAP ZM13 stock exports ("Data" sheet) — a manually
maintained backfill/history source, distinct from SAP_STOCK_GLOB.

Found 2026-07-23: the user placed 9 files (data/sap/6月/.../0601.xlsx ...
data/sap/7月/.../0721.xlsx) that are a raw ZM13 pull — column shape
品目 | 在庫数量 | 単位 | 保管場所 | ... | 品目テキスト(50) — NOT the
SBRCODE/あだ名/SYOCODE/SYONAME/QTYの合計 shape SAP_STOCK_GLOB/parse_sap_stock_xlsx
expect (that shape is a reshaped/joined file someone builds by hand from
this raw export — e.g. 260629在庫.xlsx alongside SAP在庫(ZM13)0629.xlsx for
the same date). Nothing in ingestion/ read this raw shape before this file.

品目 (the numeric item code, repeated verbatim in 旧品目コード) is the SAME
code space as SYOCODE/商品コード elsewhere in this app — verified 2026-07-23
against the live DB: 540/725 (74.5%) of this file's 品目 codes already
resolve via the existing ItemAlias(source_system="商品コード") table, the
same join key ingestion/sap.py's SYOCODE resolution already relies on. So
resolution reuses that exact path (build.py calls resolve_any(...,
prefer_source="商品コード")); this module only parses and aggregates, exactly
like ingestion/sap.py's own division of labor (parsing here, DB-aware
resolution in build.py).

One real SAP item code can appear on MULTIPLE rows here (one per 保管場所 /
storage location) — aggregation by 品目 happens in THIS module (unlike
ingestion/sap.py's SYOCODE aggregation, which happens in build.py, because
here the same raw code is always the resolution key, so there's no
ambiguity to defer past this point).

as_of_date is taken from the FILENAME (MMDD, e.g. "0721.xlsx" -> July 21),
never file mtime — these files are typically all placed on disk the same
day, well after any of their real as-of dates (confirmed 2026-07-23: all 9
files share one mtime from the day they were added), so mtime would badly
misdate every historical snapshot. Year is inferred as "today's year,
unless that would put the date in the future" (these files only ever
document PAST weeks) — good enough for a manually-curated dated backfill;
a file whose year genuinely needs disambiguating beyond that should encode
the year in its own name (as 260629在庫.xlsx / SAP部材管理_詳細版260714.xlsx
already do elsewhere in this folder).

Written history rows must be preserved across onefcst.rebuild_preserving's
full drop+rebuild the same way WeeklyActual already is — see that module;
StockState itself has no dedicated preservation yet as of this writing."""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from ..config import SAP_DIR
from .excel import read_xlsx_rows

SHEET_NAME = "Data"
REQUIRED_HEADERS = {"品目", "在庫数量", "品目テキスト(50)"}
_MMDD_RE = re.compile(r"(\d{2})(\d{2})\.xlsx$", re.IGNORECASE)


def _infer_as_of_date(path: Path, today: date) -> date | None:
    m = _MMDD_RE.search(path.name)
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    year = today.year
    try:
        candidate = date(year, month, day)
    except ValueError:
        return None
    if candidate > today:
        candidate = date(year - 1, month, day)
    return candidate


def find_stock_history_files(base_dir: Path | None = None) -> list[Path]:
    """Recursively finds every xlsx under SAP_DIR whose first sheet is
    literally named "Data" and whose header row has the raw-ZM13 columns
    this module expects — content-based, not folder-name-based, since
    procurement may place these under any folder structure (found
    2026-07-23 nested as e.g. data/sap/6月/6月/0601.xlsx)."""
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


def parse_stock_history_file(path: Path, today: date | None = None) -> dict | None:
    """One backfill snapshot: {"path", "as_of_date", "rows": [{"code", "qty", "name_text"}]}.
    Returns None if the filename doesn't carry a parseable MMDD date — a
    file this module can't confidently date is skipped rather than guessed
    at, same "don't silently mis-date" stance as ingestion/sap.py's own
    as_of_date fix (see that module's docstring)."""
    as_of = _infer_as_of_date(path, today or date.today())
    if as_of is None:
        return None
    sheet_rows = read_xlsx_rows(path, sheet=SHEET_NAME)
    hdr = {str(v): i for i, v in enumerate(sheet_rows[0]) if v is not None}
    idx_code = hdr["品目"]
    idx_qty = hdr["在庫数量"]
    idx_name = hdr["品目テキスト(50)"]

    agg: dict[str, dict] = {}
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
        name_text = row[idx_name] if idx_name < len(row) else None
        entry = agg.setdefault(code, {"code": code, "qty": 0.0, "name_text": name_text})
        entry["qty"] += float(qty)
        if name_text:
            entry["name_text"] = name_text
    return {"path": path, "as_of_date": as_of, "rows": list(agg.values())}


def parse_all_stock_history(base_dir: Path | None = None, today: date | None = None) -> list[dict]:
    """One entry per distinct as_of_date found, oldest first — build.py
    writes one StockState snapshot per entry.

    De-duped by as_of_date, not just by path: found 2026-07-23, the SAME
    2026-06-29 stock pull exists twice on disk (data/sap/6月/6月/0629.xlsx
    and data/sap/SAP在庫(ZM13)0629.xlsx, byte-identical data) — the second
    is presumably the original export that 0629.xlsx / 260629在庫.xlsx were
    later copied/reshaped from. Keeping both would write two redundant
    StockState snapshots for the same date, not corrupt data (content is
    identical here) but pointless duplication — first match (sorted path
    order) wins."""
    by_date: dict[date, dict] = {}
    for path in find_stock_history_files(base_dir):
        parsed = parse_stock_history_file(path, today)
        if parsed is not None and parsed["as_of_date"] not in by_date:
            by_date[parsed["as_of_date"]] = parsed
    return [by_date[d] for d in sorted(by_date)]
