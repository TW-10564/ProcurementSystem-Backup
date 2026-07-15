"""SAP vs ONE stock reconciliation — surfaces where the two source systems'
inventory numbers disagree, and how much of the item catalogue is running
on unverified data.

Ground truth rule (confirmed by the user 2026-07-15): SAP's physical stock
count is authoritative; ONE is a downstream system that gets updated FROM
SAP, not the other way around. This is already enforced everywhere the app
picks a "current balance" to act on — see forecast_views.trusted_current_balance
and _prefer_sap_row — SAP always wins when both are present. This module
doesn't change that priority; it makes the SIZE of the disagreement, and
which items have no SAP backing at all, visible instead of silent.

Checked live against the real DB (2026-07-15, 583 items with any stock
data): 414 items have both sources, of which 18 disagree by more than
DIVERGENCE_THRESHOLD_PCT and 3 have a negative ONE balance (physically
impossible — ONE's 在庫残高 is a simulated running projection, not a
physical count, see CLAUDE.md constraint #3). 84 items — including several
current-generation CPUs, a Tier-1 planning-owned category — have NO SAP
coverage at all and rely entirely on ONE's unverified number."""
from __future__ import annotations

from datetime import date

from ..db import get_session
from ..models import Item, StockState
from ..forecast_views import trusted_current_balance

DIVERGENCE_THRESHOLD_PCT = 30.0  # flag when |sap - one| / max(|sap|,|one|,1) exceeds this


def _latest_by_source(rows: list[StockState]) -> tuple[StockState | None, StockState | None]:
    """Most recent row carrying a SAP value, and most recent row carrying an
    ONE value, independently — the two are written as separate StockState
    rows per item (see models.StockState docstring), not always the same
    row, so picking "the latest row" alone would silently drop whichever
    source that row didn't happen to carry."""
    sap_rows = [r for r in rows if r.sap_qty is not None]
    one_rows = [r for r in rows if r.one_balance is not None]
    sap_latest = max(sap_rows, key=lambda r: r.snapshot_date or date.min) if sap_rows else None
    one_latest = max(one_rows, key=lambda r: r.snapshot_date or date.min) if one_rows else None
    return sap_latest, one_latest


def compute_stock_reconciliation(session) -> dict:
    rows = (session.query(StockState).filter(StockState.item_id.isnot(None))
            .order_by(StockState.item_id).all())
    by_item: dict[int, list[StockState]] = {}
    for r in rows:
        by_item.setdefault(r.item_id, []).append(r)

    divergences = []
    no_sap_coverage = []
    negative_one_clamped = []
    sap_covered = 0

    for item_id, item_rows in by_item.items():
        item = session.get(Item, item_id)
        if item is None:
            continue
        sap_latest, one_latest = _latest_by_source(item_rows)

        if sap_latest is not None:
            sap_covered += 1

        if sap_latest is not None and one_latest is not None:
            sap, one = sap_latest.sap_qty, one_latest.one_balance
            denom = max(abs(sap), abs(one), 1.0)
            pct = round(abs(sap - one) / denom * 100, 1)
            if pct > DIVERGENCE_THRESHOLD_PCT:
                divergences.append({
                    "item_id": item_id, "name": item.canonical_name,
                    "category": item.category, "tier": item.tier,
                    "sap_qty": sap, "sap_snapshot_date": (sap_latest.snapshot_date.isoformat()
                                                          if sap_latest.snapshot_date else None),
                    "one_balance": one, "one_snapshot_date": (one_latest.snapshot_date.isoformat()
                                                               if one_latest.snapshot_date else None),
                    "pct_diff": pct,
                    "trusted_value": sap,  # SAP wins regardless of divergence size
                })
        elif one_latest is not None:  # no SAP row for this item at all
            balance, confidence = trusted_current_balance(one_latest)
            entry = {
                "item_id": item_id, "name": item.canonical_name,
                "category": item.category, "tier": item.tier,
                "one_balance": one_latest.one_balance,
                "trusted_value": balance, "confidence": confidence,
            }
            no_sap_coverage.append(entry)
            if confidence == "one_only_negative_clamped":
                negative_one_clamped.append(entry)

    divergences.sort(key=lambda d: -d["pct_diff"])
    # Tier-1 (planning-owned: CPU/MB/VGA/Case) items with zero physical
    # verification are the highest-priority gap — surface those first.
    no_sap_coverage.sort(key=lambda d: (d["tier"] != 1, d["name"]))

    return {
        "generated_for_items": len(by_item),
        "sap_covered_items": sap_covered,
        "divergence_threshold_pct": DIVERGENCE_THRESHOLD_PCT,
        "divergences": divergences,
        "no_sap_coverage": no_sap_coverage,
        "negative_one_balance_clamped": negative_one_clamped,
        "note": ("SAP's physical count is trusted over ONE's own projection "
                "wherever both exist — the numbers below are for visibility, "
                "not a suggestion to average or split the difference. "
                "`no_sap_coverage` items have zero physical verification; "
                "`negative_one_balance_clamped` items would have inflated "
                "their order-suggestion quantity if the negative number had "
                "been used literally — see forecast_views.trusted_current_balance."),
    }


def run(session=None) -> dict:
    own_session = session is None
    if own_session:
        session = get_session()
    try:
        return compute_stock_reconciliation(session)
    finally:
        if own_session:
            session.close()


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2, ensure_ascii=False, default=str))
