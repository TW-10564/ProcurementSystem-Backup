"""Shared read-only Excel access helpers (xlsx/xlsm via openpyxl, xlsb via pyxlsb).

Guarantees:
- Sources are opened read-only, values-only. We never save a source workbook.
- Every reader returns rows plus enough position info to record lineage.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from pathlib import Path


def file_meta(path: Path) -> dict:
    st = path.stat()
    h = hashlib.md5()
    with open(path, "rb") as f:          # noqa: PTH123
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return {"mtime": datetime.fromtimestamp(st.st_mtime), "checksum": h.hexdigest()}


def read_xlsx_rows(path: Path, sheet: str, max_row: int | None = None,
                   max_col: int | None = None, data_only: bool = True) -> list[tuple]:
    """data_only=True (default) returns cached VALUES (what Excel last computed).
    data_only=False returns FORMULA STRINGS instead, for cells that hold formulas
    (used by verify/parity.py's chain check to classify each cell's formula
    kind — see engine/chain.py's classify_formula)."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=data_only,
                                keep_vba=path.suffix == ".xlsm")
    try:
        ws = wb[sheet]
        return [row for row in ws.iter_rows(max_row=max_row, max_col=max_col,
                                            values_only=True)]
    finally:
        wb.close()


def _xlsb_datetime(v):
    """pyxlsb returns Excel serials as floats; convert plausible dates."""
    if isinstance(v, float) and 20000 < v < 60000:
        return datetime(1899, 12, 30) + timedelta(days=v)
    return v


def read_xlsb_rows(path: Path, sheet: str, max_row: int | None = None,
                   max_col: int | None = None, dates: bool = False) -> list[tuple]:
    from pyxlsb import open_workbook
    out = []
    with open_workbook(str(path)) as wb:
        with wb.get_sheet(sheet) as ws:
            for ri, row in enumerate(ws.rows(), 1):
                if max_row and ri > max_row:
                    break
                vals = [c.v for c in (row[:max_col] if max_col else row)]
                if dates:
                    vals = [_xlsb_datetime(v) for v in vals]
                out.append(tuple(vals))
    return out


def col_letter(idx0: int) -> str:
    """0-based column index -> Excel letters (for source_cell lineage)."""
    s, n = "", idx0 + 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s
