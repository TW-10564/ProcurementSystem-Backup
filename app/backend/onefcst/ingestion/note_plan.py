"""Parser for ノート在庫使用計画_43期.xlsx — the notebook lifecycle master + MOQ.

マスタ sheet (verified 2026-07-06), row 1 header:
在庫使用計画にあるか|EOL連絡|ブランド|製品型番|商品コード|ベア名称|ID通称|綽名|
画面サイズ|Copilot OS|CPU|GPU|在庫|在庫+注残|MP出荷|販売開始|終息予定|備考|…
Lifecycle statuses: 1新規(発売前)/2発売中/3EOL(在庫あり)/4EOL(完全終息)/5開発中止.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ..config import NOTE_STOCK_PLAN
from .excel import read_xlsx_rows

LIFECYCLE_MAP = {
    "1": "新規(発売前)", "2": "発売中", "3": "EOL(在庫あり)",
    "4": "EOL(完全終息)", "5": "開発中止",
}


def _to_date(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, (int, float)) and 20000 < v < 60000:   # Excel serial
        return (datetime(1899, 12, 30) + timedelta(days=v)).date()
    return None


def parse_note_master() -> list[dict]:
    rows = read_xlsx_rows(NOTE_STOCK_PLAN, "マスタ")
    out = []
    for r in rows[1:]:
        model_no, code = r[3], r[4]
        if code in (None, "") and model_no in (None, ""):
            continue
        eol_raw = str(r[1] or "").strip()
        lifecycle = LIFECYCLE_MAP.get(eol_raw[:1]) if eol_raw else None
        out.append({
            "model_no": str(model_no).strip() if model_no not in (None, "") else None,
            "code": str(code).strip() if code not in (None, "") else None,
            "bare_name": str(r[5] or "").strip() or None,
            "id_name": str(r[6] or "").strip() or None,
            "adana": str(r[7]).strip() if r[7] not in (None, "") else None,
            "brand": str(r[2] or "").strip() or None,
            "cpu": str(r[10] or "").strip() or None,
            "gpu": str(r[11] or "").strip() or None,
            "lifecycle": lifecycle,
            "release_date": _to_date(r[15]),
            "eol_date": _to_date(r[16]),
            "note": str(r[17] or "").strip() or None,
        })
    return out


def parse_note_moq() -> list[dict]:
    rows = read_xlsx_rows(NOTE_STOCK_PLAN, "MOQ条件")
    out = []
    for r in rows[1:]:
        if r[2] in (None, ""):
            continue
        out.append({"adana": str(r[2]).strip(),
                    "moq": r[3] if isinstance(r[3], (int, float)) else None})
    return out
