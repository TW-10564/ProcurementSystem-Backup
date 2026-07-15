"""SAP ZM13 weekly stock parser → physical stock per row (actuals source='SAP').

Layout (verified Phase-0): SBRCODE | SBRNAME | あだ名 | SYOCODE | SYONAME | QTYの合計.

SYOCODE is the primary join key (standardized 2026-07-09): it's SAP's own
part code and lines up exactly with 商品コード in ONE's 部品マスター — already
stored as an ItemAlias(source_system="商品コード") by registry/build.py, so
resolve()/resolve_any() can match a SYOCODE string directly with no new
lookup table. Verified against the real 260629在庫.xlsx + ONE計画.xlsm:
SYOCODE resolves 152/368 blank-あだ名 rows via 商品コード alone (vs 41/368
previously via the SYONAME crosswalk below), and for 31/353 non-blank-あだ名
rows SYOCODE resolves to a DIFFERENT item than the あだ名 string does — SAP's
own code is trusted over the display name in that case. あだ名 (then the
SYONAME crosswalk) remain fallbacks for rows whose SYOCODE isn't itself a
known 商品コード. Resolution happens in build.py (needs DB access); this
module only parses and returns one record per SAP row — no pre-aggregation,
since the resolution key (SYOCODE) may differ from the display key (あだ名)
and aggregation must happen post-resolution, by item.

Blank-あだ名 rows unresolved by SYOCODE (368/~900 in the file checked
2026-07-07, mostly MB) fall back to the procurement team's OWN maintained
name crosswalk (製品名称読みかえ, see name_crosswalk.py) rather than a
regex/pattern heuristic — deliberately, since a wrong stock match feeds a
wrong order-quantity suggestion (real money), while a missed match just
means no suggestion appears at all. A human-curated mapping is far less
likely to produce the former.

Primary source as of 2026-07-14: SAP_ACCDB_PATH (SAP在庫抽出.accdb), the
live weekly-refreshed Access DB sitting in ONE_DIR — its 'SAP在庫データ加工後'
table already has this exact SYOCODE/QTY/SYONAME/SBRCODE/SBRNAME shape, just
with no あだ名 column at all (the accdb's own あだ名 table has every SYOCODE
blank — not usable as a join today). That's fine: あだ名 was already only
the SECONDARY resolution key (see above), so parse_sap_stock_accdb() simply
leaves adana unset per row and lets the same SYOCODE-then-crosswalk fallback
in build.py do the rest. SAP_STOCK_GLOB (a manually-placed xlsx snapshot,
the ONLY method before 2026-07-14) is now the fallback for when the accdb
is unreachable.

"as_of_date" (found and fixed 2026-07-14, was "month" = always the 1st of
the current calendar month, unconditionally) is the SOURCE FILE's own
modification time, not the day the build happened to run. This matters:
build.py writes one StockState row from this data and a SEPARATE one from
ONE's own subfile 在庫残高 projection (see ingestion/subfiles.py), and
forecast_views.py/verify/order_suggestions.py pick "most recent StockState
by snapshot_date" as current_balance. The old hardcoded day=1 made the SAP
row look OLDER than ONE's own row (dated to the real "today") on every day
of the month except the 1st — so the actual physical SAP count lost to
ONE's own forward-PROJECTED balance (which can be, and was found to be,
already negative for fast-moving items) on 29-30 days out of every month,
even though the SAP row was the fresher, more reliable real count.
"""
from __future__ import annotations

import csv
import glob
import subprocess
from datetime import date, datetime
from pathlib import Path

from ..config import SAP_ACCDB_PATH, SAP_STOCK_GLOB
from .excel import read_xlsx_rows
from .name_crosswalk import latest_crosswalk_file, load_crosswalk_tokens, resolve_via_crosswalk


def _file_as_of_date(path: Path) -> date:
    return datetime.fromtimestamp(path.stat().st_mtime).date()

ACCDB_TABLE = "SAP在庫データ加工後"


def latest_sap_file() -> Path | None:
    hits = sorted(glob.glob(SAP_STOCK_GLOB))
    return Path(hits[-1]) if hits else None


def _resolve_crosswalk(tokens, syoname) -> str | None:
    return resolve_via_crosswalk(syoname, tokens) if tokens else None


def parse_sap_stock_accdb(path: Path | None = None) -> dict | None:
    """Reads SAP_ACCDB_PATH's 'SAP在庫データ加工後' table via mdb-export
    (requires the system `mdbtools` package). Returns None (not a
    {"rows": []} dict) if the file is missing or mdb-export fails, so
    parse_sap_stock() can tell "accdb unavailable, fall back to xlsx" apart
    from "accdb read cleanly but is empty"."""
    path = path or SAP_ACCDB_PATH
    if not Path(path).exists():
        return None
    try:
        result = subprocess.run(["mdb-export", str(path), ACCDB_TABLE],
                                capture_output=True, text=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None

    crosswalk_path = latest_crosswalk_file()
    tokens = load_crosswalk_tokens(crosswalk_path) if crosswalk_path else []

    rows = []
    resolved_via_crosswalk = 0
    for r in csv.DictReader(result.stdout.splitlines()):
        syocode, syoname = r.get("SYOCODE"), r.get("SYONAME")
        try:
            q = float(r.get("QTY"))
        except (TypeError, ValueError):
            continue
        crosswalk_name = _resolve_crosswalk(tokens, syoname)
        if crosswalk_name:
            resolved_via_crosswalk += 1
        rows.append({
            "syocode": syocode.strip() if syocode else None,
            "adana": crosswalk_name,
            "syoname": syoname,
            "qty": q,
        })
    return {"path": Path(path), "rows": rows,
            "resolved_via_crosswalk": resolved_via_crosswalk,
            "as_of_date": _file_as_of_date(Path(path))}


def parse_sap_stock_xlsx() -> dict:
    path = latest_sap_file()
    if path is None:
        return {"path": None, "rows": [], "resolved_via_crosswalk": 0}
    # sheet=None: the SAP export's sheet name isn't fixed, always read the first
    sheet_rows = read_xlsx_rows(path)

    crosswalk_path = latest_crosswalk_file()
    tokens = load_crosswalk_tokens(crosswalk_path) if crosswalk_path else []

    hdr = None
    rows = []
    resolved_via_crosswalk = 0
    for row in sheet_rows:
        if hdr is None:
            if row and any(v == "あだ名" for v in row):
                hdr = {str(v): i for i, v in enumerate(row) if v is not None}
            continue
        adana = row[hdr["あだ名"]] if "あだ名" in hdr else None
        syoname = row[hdr["SYONAME"]] if "SYONAME" in hdr else None
        syocode = row[hdr["SYOCODE"]] if "SYOCODE" in hdr else None
        q = row[hdr.get("QTYの合計", len(row) - 1)]
        if not isinstance(q, (int, float)):
            continue
        crosswalk_name = None
        if adana in (None, ""):
            crosswalk_name = _resolve_crosswalk(tokens, syoname)
            if crosswalk_name:
                resolved_via_crosswalk += 1
        rows.append({
            "syocode": str(syocode).strip() if syocode not in (None, "") else None,
            "adana": str(adana).strip() if adana not in (None, "") else crosswalk_name,
            "syoname": syoname,
            "qty": float(q),
        })
    return {"path": path, "rows": rows,
            "resolved_via_crosswalk": resolved_via_crosswalk,
            "as_of_date": _file_as_of_date(path)}


def parse_sap_stock() -> dict:
    result = parse_sap_stock_accdb()
    if result is not None:
        return result
    return parse_sap_stock_xlsx()
