"""Purchasing's own "won't reorder" cell-color marking in 部品詳細's SKU
name cell — a human decision that can precede the formal eol_flag (see
Item.marked_no_reorder in models.py). Confirmed 2026-07-14 against real NAS
data: in ONE_08_RTX_社外秘.xlsx, all 12 gray-marked SKUs already had
eol_flag=True; in ONE_08_VGA_社外秘.xlsx, RX7800XT/RX7600 were gray-marked
with eol_flag STILL False — an early warning ahead of the formal flag.

The color is an Excel THEME color ("White, Background 1, Darker 25%", i.e.
theme index 0 with tint ≈ -0.25), not a fixed RGB value — a plain-RGB scan
misses it entirely, which is why this needed a dedicated theme-aware check.

Only readable for the 6 category files stored as .xlsx (VGA/RTX/MEM/SSD/PSU/
Mouse&KB per subfile_config.FILES). The 11 .xlsb categories (CPU/MB/Case/
FAN/etc) are read via pyxlsb, which exposes NO cell styling at all — there
is no way to check this signal for those categories with the current
tooling; find_no_reorder_skus() returns an empty set for them rather than
guessing."""
from __future__ import annotations

from pathlib import Path

from .subfile_config import FILES, BLOCK_LABELS, BASE

GRAY_THEME_INDEX = 0
GRAY_TINT = -0.25
GRAY_TINT_TOLERANCE = 0.02


def _is_gray(fill) -> bool:
    if fill is None:
        return False
    fg = fill.fgColor
    if fg is None or fg.type != "theme" or fg.theme != GRAY_THEME_INDEX:
        return False
    return fg.tint is not None and abs(fg.tint - GRAY_TINT) <= GRAY_TINT_TOLERANCE


def find_no_reorder_skus(cat: str) -> set[str]:
    """Gray-marked SKU names (綽名) for one category, or an empty set if the
    category isn't in .xlsx format or its file isn't reachable."""
    cfg = FILES[cat]
    if cfg["fmt"] != "xlsx":
        return set()
    path = Path(cfg.get("_abspath") or (BASE / cfg["file"]))
    if not path.exists():
        return set()

    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    try:
        if "部品詳細" not in wb.sheetnames:
            return set()
        ws = wb["部品詳細"]
        label_col, name_col = cfg["label_col"], cfg["name_col"]
        target_label = BLOCK_LABELS[0]                          # "生産計画"
        skus = set()
        for row in ws.iter_rows():
            if len(row) <= max(label_col, name_col):
                continue
            if row[label_col].value != target_label:
                continue
            name_cell = row[name_col]
            if name_cell.value and _is_gray(name_cell.fill):
                skus.add(str(name_cell.value).strip())
        return skus
    finally:
        wb.close()


def find_all_no_reorder_skus() -> dict[str, set[str]]:
    """{category: {sku, ...}} across every .xlsx category — categories not
    stored as .xlsx are simply absent from the result (not present with an
    empty set), so a caller can tell "checked, none found" apart from
    "couldn't check this category at all" if that distinction ever matters."""
    out = {}
    for cat, cfg in FILES.items():
        if cfg["fmt"] != "xlsx":
            continue
        out[cat] = find_no_reorder_skus(cat)
    return out
