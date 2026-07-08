"""Parser for ①販売計画 — the Tier-1 model-level sales plan seed (level='planning1').

コンシューマー / コマーシャル sheets: 手動補正-labeled columns are the plan
months; SKU rows below the date row. We capture the plan VALUE per SKU per
month (not the coefficient rows) — this is the pre-attach-rate model-level
number, upstream of ④'s parts conversion.
"""
from __future__ import annotations

import re
from datetime import datetime

from ..config import planning_file
from .excel import read_xlsx_rows, col_letter

SHEETS = ["コンシューマー", "コマーシャル"]

# FIX_PLAN M4: ①'s row labels are mostly CPU/GPU/MB shorthand codes (e.g.
# "ASR B550M", "RTX5060Ti 16GB"), not finished-product model names as
# originally assumed — but they still miss ONE's 綽名 on exact/normalized
# match because of a few DOCUMENTED, VERIFIED naming-convention gaps
# (checked against the live registry 2026-07-08), not because they're
# unrelated names:
#   - ASRock boards drop the vendor prefix in 綽名 ("ASR B550M" -> "B550M"),
#     while MSI/ASUS/GIGABYTE boards KEEP theirs ("MSI H610M D5") — so only
#     ASR/ASRock is stripped, never MSI/ASUS/GIGABYTE.
#   - NVIDIA RTX cards drop "RTX" in 綽名 ("RTX5070Ti" -> "5070Ti"), but AMD
#     Radeon cards KEEP "RX" ("RX9070XT" stays "RX9070XT") — only RTX is
#     stripped.
#   - VRAM capacity uses "G" in 綽名, not "GB" ("16GB" -> "16G").
# This is a bounded mitigation, not a full solve (per FIX_PLAN M4): case/
# chassis color-code rows ("CruX　WH", "SF 白 コラボ") have no 綽名
# counterpart at all — a structural gap, not a naming-convention one.
_ASR_PREFIX_RE = re.compile(r"^ASR(?:ock)?\s+", re.IGNORECASE)
_RTX_PREFIX_RE = re.compile(r"^RTX\s*", re.IGNORECASE)
_GB_SUFFIX_RE = re.compile(r"(\d+)\s*GB\b", re.IGNORECASE)


def candidate_variants(raw_name: str) -> list[str]:
    """Alternate 綽名 spellings to try for a ① row label, most-specific
    (closest to the original) first. Returns just [raw_name] when none of
    the known conventions apply."""
    variants = [raw_name]
    no_asr = _ASR_PREFIX_RE.sub("", raw_name)
    if no_asr != raw_name:
        variants.append(no_asr)
    no_rtx = _RTX_PREFIX_RE.sub("", raw_name)
    if no_rtx != raw_name:
        variants.append(no_rtx)
    gb_to_g = _GB_SUFFIX_RE.sub(r"\1G", raw_name)
    if gb_to_g != raw_name:
        variants.append(gb_to_g)
        no_rtx_gb = _RTX_PREFIX_RE.sub("", gb_to_g)
        if no_rtx_gb != gb_to_g:
            variants.append(no_rtx_gb)
    return variants


def parse_plan1_sheet(sheet: str) -> dict:
    path = planning_file("①")
    rows = read_xlsx_rows(path, sheet)
    date_cols = [i for i, v in enumerate(rows[2]) if v == "手動補正"]
    if not date_cols:
        return {"sheet": sheet, "rows": [], "months": []}
    date_row_idx = next((r for r, row in enumerate(rows[:30])
                        if isinstance(row[date_cols[0]], datetime)), None)
    if date_row_idx is None:
        return {"sheet": sheet, "rows": [], "months": []}
    months = [rows[date_row_idx][c] for c in date_cols]

    out = []
    for ri in range(date_row_idx + 1, len(rows)):
        row = rows[ri]
        name = row[1]
        if name is None or str(name).strip() in ("", "-") or "合計" in str(name):
            continue
        for ci, c in enumerate(date_cols):
            v = row[c]
            if isinstance(v, (int, float)):
                out.append({"raw_name": str(name).strip(),
                           "month": months[ci].date().replace(day=1),
                           "value": float(v),
                           "cell": f"{sheet}!{col_letter(c)}{ri + 1}"})
    return {"sheet": sheet, "rows": out, "months": [m.date().replace(day=1) for m in months]}


def parse_plan1() -> list[dict]:
    out = []
    for sheet in SHEETS:
        out.extend(parse_plan1_sheet(sheet)["rows"])
    return out
