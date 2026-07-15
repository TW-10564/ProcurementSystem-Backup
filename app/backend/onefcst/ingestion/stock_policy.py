"""Parses the procurement-maintained 在庫の持ち方 category safety-stock-days
policy table — a small human-curated file directly under the ONE NAS folder
(filename date-suffixed and refreshed ad hoc, e.g. 在庫の持ち方20260618.xlsx;
see config.STOCK_POLICY_GLOB).

Feeds Item.min_stock_days as a CATEGORY-LEVEL FALLBACK only, for items where
部品マスター (ingestion/one_master.py) didn't supply a per-item value — it
never overrides a real per-item figure, since that's more specific and
presumably more accurate than a category default. See the "category-level
min_stock_days fallback" step in registry/build.py's build_registry() for
where this gets applied.

Two labels in the source table (Case GE/GEm, Case コラボ) name a case
sub-line this registry has no reliable way to identify — Item.subcategory
only distinguishes form factor (ATX/mATX/mITX/Slim), not model line — and
one of those two is non-numeric anyway ("個別相談" = case-by-case
negotiation, no fixed number). Both are skipped rather than guessed at."""
from __future__ import annotations

import re
from glob import glob

import openpyxl

from ..config import STOCK_POLICY_GLOB

# source label -> (Item.category, Item.subcategory or None-for-"all subcats")
CATEGORY_MAP = {
    "CPU": ("CPU", None),
    "Long-DIMM": ("メモリ", "Long"),
    "SO-DIMM": ("メモリ", "SO"),
    "SSD": ("SSD", None),
    "VGA": ("グラフィックカード", None),
    "MB": ("ﾏｻﾞｰﾎﾞｰﾄﾞ", None),
    "Case その他": ("PCｹｰｽ", None),
    "PSU": ("電源S", None),
}
# labels that fan out to more than one Item.category value
FAN_CATEGORIES = ["ファンN", "ファンS"]
NOTE_CATEGORIES = ["NotePC", "ノートベア"]

SKIPPED_LABELS = {"Case GE/GEm", "Case コラボ"}


def _parse_days(raw) -> float | None:
    """'14日' -> 14.0; '45-60日' -> 52.5 (midpoint); '個別相談' -> None."""
    if not isinstance(raw, str):
        return None
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", raw)]
    if not nums:
        return None
    return sum(nums) / len(nums)


def parse_stock_policy() -> dict:
    """Returns {"policy": {(category, subcategory_or_None): days},
    "skipped": [(label, raw_value), ...], "source_file": path or None}."""
    hits = sorted(glob(STOCK_POLICY_GLOB))
    if not hits:
        return {"policy": {}, "skipped": [], "source_file": None}
    path = hits[-1]      # latest by filename (date-suffixed)

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]

    policy: dict = {}
    skipped: list = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        label, raw_days = row[0], row[1]
        if label is None:
            continue
        if label in SKIPPED_LABELS:
            skipped.append((label, raw_days))
            continue
        days = _parse_days(raw_days)
        if days is None:
            skipped.append((label, raw_days))
            continue
        if label == "Fan":
            for cat in FAN_CATEGORIES:
                policy[(cat, None)] = days
        elif label == "Note":
            for cat in NOTE_CATEGORIES:
                policy[(cat, None)] = days
        elif label in CATEGORY_MAP:
            cat, subcat = CATEGORY_MAP[label]
            policy[(cat, subcat)] = days
        else:
            skipped.append((label, raw_days))    # unrecognized — needs a CATEGORY_MAP update

    return {"policy": policy, "skipped": skipped, "source_file": path}
