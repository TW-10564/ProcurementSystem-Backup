"""Parses the procurement-maintained 在庫の持ち方 category stock-days CEILING
table — a small human-curated file directly under the ONE NAS folder
(filename date-suffixed and refreshed ad hoc, e.g. 在庫の持ち方20260618.xlsx;
see config.STOCK_POLICY_GLOB). Its own column header is 在庫日数(上限) —
"stock days (upper limit)" — a cap on how much stock a category should hold,
not a minimum.

Feeds Item.max_stock_days for every item with a matching category (see the
"category-level max_stock_days ceiling" step in registry/build.py's
build_registry()). This is a DIFFERENT field from Item.min_stock_days
(部品マスター's genuine per-item floor, ingestion/one_master.py) — until
2026-08-20 this table's ceiling values were merged into min_stock_days
instead, which stock_projection.py's reorder trigger then read as a floor,
confirmed causing live over-ordering (see stock_projection.suggest_order()'s
docstring). Keep the two fields separate; do not reintroduce that merge.

Two labels in the source table name a case sub-line Item.category/
subcategory can't identify on their own — Item.subcategory only
distinguishes form factor (ATX/mATX/mITX/Slim), not model line:

- "Case コラボ" has no fixed number anyway ("個別相談" = case-by-case
  negotiation) — genuinely nothing to assign, stays skipped.
- "Case GE/GEm" DOES have a real number (90日) — until 2026-08-20 this was
  also skipped outright, so every real GE/GEm item (6 confirmed live: GE
  ガンメタ/白/Creator黒/Creator白, GEm ガンメタ/白) silently fell through to
  the generic "Case その他" bucket at 60 days instead of its own 90 —
  invisible while max_stock_days was unused, but now that
  stock_projection.py actually sizes orders against it (see FIX 2026-08-20
  above), this too-low cap directly undersizes their real order
  suggestions. Fixed by matching on Item.canonical_name instead of
  category/subcategory — every real GE/GEm SKU's own name starts with
  "GE" or "GEm" (checked against real data, no other Item in this category
  does) — see NAME_PREFIX_POLICY below."""
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

SKIPPED_LABELS = {"Case コラボ"}

# labels identified by Item.canonical_name PREFIX (not category/subcategory —
# see module docstring for why) -> (Item.category, name_prefix)
NAME_PREFIX_MAP = {
    "Case GE/GEm": ("PCｹｰｽ", "GE"),
}


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
    "name_prefix_policy": {(category, name_prefix): days},
    "skipped": [(label, raw_value), ...], "source_file": path or None}."""
    hits = sorted(glob(STOCK_POLICY_GLOB))
    if not hits:
        return {"policy": {}, "name_prefix_policy": {}, "skipped": [], "source_file": None}
    path = hits[-1]      # latest by filename (date-suffixed)

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]

    policy: dict = {}
    name_prefix_policy: dict = {}
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
        elif label in NAME_PREFIX_MAP:
            name_prefix_policy[NAME_PREFIX_MAP[label]] = days
        elif label in CATEGORY_MAP:
            cat, subcat = CATEGORY_MAP[label]
            policy[(cat, subcat)] = days
        else:
            skipped.append((label, raw_days))    # unrecognized — needs a CATEGORY_MAP update

    return {"policy": policy, "name_prefix_policy": name_prefix_policy,
           "skipped": skipped, "source_file": path}
