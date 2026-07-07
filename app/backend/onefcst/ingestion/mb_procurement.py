"""MB 製品一覧 parser — take-or-pay (FCST引き取り責任) and exclusive-part flags.

Verified 2026-07-06: exclusive boards carry supplier commitment liability —
if forecast N+1, purchasing must buy or pay (あり) or negotiate (要交渉).
Board-model granularity (e.g. "ASRock B860 TW") is finer than our chipset-
level registry items (e.g. "B860"), so this aggregates by CHIPSET: an item is
flagged exclusive/take-or-pay if ANY board model on that chipset carries the
flag — a conservative (over-alert rather than under-alert) rollup, appropriate
for a financial-liability signal.
"""
from __future__ import annotations

from .excel import read_xlsb_rows
from ..engine.compat import CHIPSET_SOCKET

_MB_FILE = "ONE_08_MB_社外秘.xlsb"


def _find_chipset(model_name: str) -> str | None:
    n = model_name.upper()
    best = None
    for code in CHIPSET_SOCKET:
        if code in n and (best is None or len(code) > len(best)):
            best = code
    return best


def parse_mb_takeorpay(one_dir) -> list[dict]:
    path = one_dir / _MB_FILE
    if not path.exists():
        return []
    rows = read_xlsb_rows(path, "製品一覧", max_row=200, max_col=12)
    # header is row 3 (0-indexed row 2): 仕入先|型番|専用か否か|専用部材|専用部材コスト| |N+1|N+2|N+3
    out = []
    for row in rows[3:]:
        supplier, model = row[0], row[1]
        if not model:
            continue
        exclusive = str(row[2] or "").strip() == "専用"
        n1, n2, n3 = row[6], row[7], row[8]
        chipset = _find_chipset(str(model))
        out.append({"supplier": supplier, "model": str(model).strip(),
                   "chipset": chipset, "exclusive": exclusive,
                   "takeorpay_n1": str(n1).strip() if n1 else None,
                   "takeorpay_n2": str(n2).strip() if n2 else None,
                   "takeorpay_n3": str(n3).strip() if n3 else None})
    return out


def chipset_liability_rollup(rows: list[dict]) -> dict[str, dict]:
    """chipset -> {'exclusive': bool, 'takeorpay_n1': bool, 'models': [...]}."""
    roll: dict[str, dict] = {}
    for r in rows:
        if not r["chipset"]:
            continue
        agg = roll.setdefault(r["chipset"], {"exclusive": False,
                                            "takeorpay_n1": False, "models": []})
        agg["exclusive"] |= r["exclusive"]
        agg["takeorpay_n1"] |= (r["takeorpay_n1"] == "あり")
        agg["models"].append(r["model"])
    return roll
