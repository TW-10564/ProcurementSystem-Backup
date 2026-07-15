"""Per-file 部品詳細 column layout for all 17 ONE sub-files.

Layouts originally validated against the real files by the frozen
consolidation/ POC (probe.py, 2026-06-29) — copied in-house here 2026-07-14
so the active app has no runtime dependency on consolidation/ (which is
frozen/reference-only per CLAUDE.md; a change made there under the
assumption it's dead code would otherwise silently break ingestion here).
This is a ONE-TIME reuse of already-proven layouts, not a live import.

label_col      — col holding 生産計画/購買計画/在庫残高/在庫日数 labels
name_col       — col holding the SKU shortname (綽名); also the safety col
first_date_col — first column of the daily date header (row 0)
expected_skus  — count used by build.py's Gate A reconciliation check
"""
from ..config import ONE_DIR

BASE = ONE_DIR

FILES = {
    "CPU":        {"file": "ONE_08_CPU_社外秘.xlsb",        "fmt": "xlsb", "label_col": 0, "name_col": 1, "first_date_col": 2, "expected_skus": 38},
    "MB":         {"file": "ONE_08_MB_社外秘.xlsb",         "fmt": "xlsb", "label_col": 0, "name_col": 1, "first_date_col": 2, "expected_skus": 38},
    "VGA":        {"file": "ONE_08_VGA_社外秘.xlsx",        "fmt": "xlsx", "label_col": 0, "name_col": 1, "first_date_col": 2, "expected_skus": 28},
    "RTX":        {"file": "ONE_08_RTX_社外秘.xlsx",        "fmt": "xlsx", "label_col": 0, "name_col": 1, "first_date_col": 2, "expected_skus": 24},
    "MEM":        {"file": "ONE_08_MEM_社外秘.xlsx",        "fmt": "xlsx", "label_col": 0, "name_col": 1, "first_date_col": 2, "expected_skus": 26},
    "SSD":        {"file": "ONE_08_SSD_社外秘.xlsx",        "fmt": "xlsx", "label_col": 0, "name_col": 1, "first_date_col": 2, "expected_skus": 45},
    "HDD":        {"file": "ONE_08_HDD_社外秘.xlsb",        "fmt": "xlsb", "label_col": 0, "name_col": 1, "first_date_col": 2, "expected_skus": 9},
    "PSU":        {"file": "ONE_08_PSU_社外秘.xlsx",        "fmt": "xlsx", "label_col": 0, "name_col": 1, "first_date_col": 2, "expected_skus": 17},
    "Case":       {"file": "ONE_08_Case_社外秘.xlsb",       "fmt": "xlsb", "label_col": 0, "name_col": 1, "first_date_col": 2, "expected_skus": 42},
    "Case_other": {"file": "ONE_08_Case_other_社外秘.xlsb", "fmt": "xlsb", "label_col": 0, "name_col": 1, "first_date_col": 2, "expected_skus": 19},
    # non-standard: SKU name in col 3 (col 1 = maker code "NX", col 2 = product code)
    "Case_梱包":  {"file": "ONE_08_Case_梱包_社外秘.xlsb",  "fmt": "xlsb", "label_col": 0, "name_col": 3, "first_date_col": 4, "expected_skus": 60},
    # non-standard: extra supplier col 0, label col 1, name col 2
    "FAN":        {"file": "ONE_08_FAN_社外秘.xlsb",        "fmt": "xlsb", "label_col": 1, "name_col": 2, "first_date_col": 3, "expected_skus": 77},
    "ODD":        {"file": "ONE_08_ODD_社外秘.xlsb",        "fmt": "xlsb", "label_col": 0, "name_col": 1, "first_date_col": 2, "expected_skus": 7},
    "NotePC":     {"file": "ONE_08_NotePC_社外秘.xlsb",     "fmt": "xlsb", "label_col": 0, "name_col": 1, "first_date_col": 2, "expected_skus": 81},
    "OS":         {"file": "ONE_08_OS_社外秘.xlsb",         "fmt": "xlsb", "label_col": 0, "name_col": 1, "first_date_col": 2, "expected_skus": 10},
    "Mouse&KB":   {"file": "ONE_08_Mouse&KB_社外秘.xlsx",   "fmt": "xlsx", "label_col": 0, "name_col": 1, "first_date_col": 2, "expected_skus": 6},
    "国内一般品": {"file": "ONE_08_国内一般品_社外秘.xlsb", "fmt": "xlsb", "label_col": 0, "name_col": 1, "first_date_col": 2, "expected_skus": 7},
}

# Block labels in 部品詳細 (4 consecutive rows per SKU)
BLOCK_LABELS = ["生産計画", "購買計画", "在庫残高", "在庫日数"]
