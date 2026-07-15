"""Real observed lead time (order date -> delivery date) from 購買計画 PO
history — see ingestion/legacy_po.py for the underlying parser.

Confirmed 2026-07-14 this diverges sharply from 部品マスター's static
lead_time_days for many items (e.g. an SSD: master says 7 days, 6 real
completed deliveries show a median of 112 days) — worth surfacing
alongside the master value, not replacing it silently, since a single bad
month could equally skew the observed side.

Only status='delivered' rows count (an 'open' PO's delivery date is a
target, not something that's actually happened yet; 'cancelled' rows have
no meaningful delivery at all). Requires MIN_SAMPLES completed deliveries
before trusting the median — fewer than that is too easily dominated by
one unusually fast or slow shipment.
"""
from __future__ import annotations

import statistics

from .legacy_po import read_all_pos

MIN_SAMPLES = 3
MAX_PLAUSIBLE_DAYS = 365   # excludes clear data-entry errors (e.g. wrong-year dates)


def compute_observed_lead_times() -> dict[str, tuple[float, int]]:
    """{raw SKU string: (median_lead_days, sample_count)} — resolution to an
    Item happens in registry/build.py (needs DB access), same division of
    responsibility as ingestion/sap.py."""
    pos = read_all_pos()
    by_sku: dict[str, list[int]] = {}
    for p in pos:
        if p["status"] != "delivered" or not p["order_date"] or not p["delivery"]:
            continue
        days = (p["delivery"] - p["order_date"]).days
        if not (0 <= days <= MAX_PLAUSIBLE_DAYS):
            continue
        by_sku.setdefault(p["sku"], []).append(days)

    return {sku: (statistics.median(days), len(days))
           for sku, days in by_sku.items() if len(days) >= MIN_SAMPLES}
