"""
Consolidation config — 部品詳細 layout for ALL 17 product ONE files.

Layouts validated by probe.py against the real files (2026-06-29) and
notes Section 33. Invariant confirmed: safety-target column == name_col
in every file (safety target sits in the 購買計画 row at the name column).

Each entry: (filename, fmt, label_col, name_col, first_date_col, expected_skus)
  label_col      — col holding 生産計画/購買計画/在庫残高/在庫日数 labels
  name_col       — col holding the SKU shortname (綽名); also the safety col
  first_date_col — first column of the daily date header (row 0)
  expected_skus  — count from notes Section 31, used by the reconciliation check
"""
import os
from pathlib import Path

# ── Source folder for the 17 ONE files ────────────────────────────────────────
# Default = the local copy in this repo. To read the LIVE NAS share instead,
# set the env var ONE_SOURCE_DIR to the mounted share path. Switching source
# is therefore a one-line/zero-code change.
#
# Live source (confirmed by 内田さん 2026-06-29) is a NAS Windows share:
#   \\172.25.20.11\サードウェーブグループ\43上期\サードウェーブ\購買本部\【通常業務】\ONE
# In WSL this must first be mounted (CIFS/SMB), e.g.:
#   sudo mount -t cifs //172.25.20.11/サードウェーブグループ /mnt/nas -o user=...,iocharset=utf8
#   export ONE_SOURCE_DIR="/mnt/nas/43上期/サードウェーブ/購買本部/【通常業務】/ONE"
_DEFAULT_BASE = Path(__file__).parent.parent / "ONE" / "ONE"
BASE = Path(os.environ.get("ONE_SOURCE_DIR", _DEFAULT_BASE))

# category -> config
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

# Per-category excess threshold (days) — from notes Section 37 / config.py
EXCESS_DAYS = {
    "CPU": 90, "MB": 100, "VGA": 100, "RTX": 100, "Case": 150,
    # default for the rest
    "_default": 120,
}

TOTAL_EXPECTED_SKUS = sum(c["expected_skus"] for c in FILES.values())  # 534
