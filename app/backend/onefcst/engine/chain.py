"""Tier-1 per-SKU plan chain — pure functions, no I/O.

Reproduces ①'s per-SKU month chain (validated 346/346 cells in poc_v0.py):
plan[m] = plan[m-1] × coefficient, coefficient = manual override if entered,
else the seasonal MoM default. Every cell beyond the seed month is classified
so manual intervention (~18% historically) is measured, not hidden.
"""
from __future__ import annotations


def classify_formula(formula: str | None) -> str:
    """chain: standard seed×coefficient step. lookup: pulls from a paste sheet.
    custom: any other formula. constant: hand-entered value (no formula)."""
    if formula is None:
        return "constant"
    if "$6=\"\"" in formula.replace(" ", ""):
        return "chain"
    if "貼付" in formula:
        return "lookup"
    return "custom"


def reproduce_chain(months: list, mom: list[float | None],
                    manual: list[float | None], sku_cells: list[list[dict]]) -> dict:
    """sku_cells: per SKU, a list of {"value":..,"formula":..} aligned to months.

    Returns aggregate stats: how many cells are chain/lookup/custom/override,
    and how many of the CHAIN cells the engine reproduces exactly.
    """
    n = len(months)
    stats = {"total_cells": 0, "seed_cells": 0, "chain_cells": 0, "chain_match": 0,
             "chain_mismatch": [], "lookup_cells": 0, "custom_cells": 0,
             "override_cells": 0}
    for cells in sku_cells:
        for i in range(min(n, len(cells))):
            v = cells[i]["value"]
            if v is None:
                continue
            stats["total_cells"] += 1
            if i == 0:
                stats["seed_cells"] += 1
                continue
            kind = classify_formula(cells[i].get("formula"))
            if kind == "chain":
                stats["chain_cells"] += 1
                prev = cells[i - 1]["value"]
                coef = manual[i] if manual[i] not in (None, "") else mom[i]
                if prev is None or coef is None:
                    continue
                mine = prev * coef
                if v == 0 and mine == 0:
                    stats["chain_match"] += 1
                elif v and abs(mine - v) <= 1e-9 * max(1.0, abs(v)):
                    stats["chain_match"] += 1
                else:
                    stats["chain_mismatch"].append((i, mine, v))
            elif kind == "lookup":
                stats["lookup_cells"] += 1
            elif kind == "custom":
                stats["custom_cells"] += 1
            else:
                stats["override_cells"] += 1
    return stats
