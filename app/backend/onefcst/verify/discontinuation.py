"""Possible-discontinuation report — surfaces items the model is still
forecasting real volume for, despite a sharp recent falloff in demand.

Two existing signals already handle the common cases:
  - engine/baseline.is_stale (6-month lookback, absent months count as 0)
    drops an item from the forecast run entirely once it has had zero real
    demand for 6 straight months.
  - engine/baseline.apply_discontinuation_signal carries a CONFIRMED,
    already-committed zero plan value forward once 2+ such months appear
    in a row at the end of the known window.
  - Item.lifecycle (新規/発売中/EOL在庫あり/EOL完全終息/開発中止) is an
    authoritative source, but is only populated for NotePC items (from
    ingestion/note_plan.py) — every other category (CPU/MB/VGA/RAM/PSU/
    Case/etc, the large majority of items) has no such flag at all.
  - Item.marked_no_reorder (added 2026-07-14, see ingestion/no_reorder_flag.py)
    is purchasing's own gray-cell "won't reorder" marking — a real signal
    that can precede eol_flag, but only readable for 6 of 17 categories
    (VGA/RTX/MEM/SSD/PSU/Mouse&KB; the rest are .xlsb, unreadable for cell
    styling with current tooling) — a corroborating flag on candidates
    below, not a replacement for the decline-based check itself.

Checked live against forecast run data (2026-07-10): both existing signals
catch nearly everything. What slips through is the middle case — an item
that is CLEARLY fading (its last 6 real months are a small fraction of the
6 months before that) but hasn't gone fully silent, so it clears the
6-month stale bar and never accumulates 2 confirmed zero months. A
trend/seasonal method extrapolating from that item's own recent history
can still forecast meaningful volume for it. This report does not silently
zero those out (a human hasn't confirmed the item is actually dead) — it
flags them for review, the same "system surfaces, human decides" pattern
used elsewhere (compat rules, source reconciliation).

A third signal, added 2026-07-15 (user's own domain heuristic): SAP's
PHYSICAL stock balance (sap_qty — see verify/stock_reconciliation.py for
why this is trusted over ONE's simulated one_balance) sitting at exactly
zero across EVERY SAP snapshot observed for an item, for a long enough
span, is itself evidence of discontinuation independent of the demand
history above — a part nobody has bothered to restock for months is
usually a part nobody plans to sell again. See find_zero_stock_candidates
for the important caveat: this needs multiple snapshots spread over real
calendar time to mean anything, and as of this writing the DB holds only
ONE SAP snapshot (rebuild_preserving.py's weekly cycle will accumulate
more from here — see PILOT_DEPLOY_PLAN's blocker #1 fix)."""
from __future__ import annotations

from datetime import date

from ..db import get_session
from ..models import Forecast, ForecastRun, Item, StockState
from ..forecasting import _series_by_item

MIN_PRIOR_VOLUME = 12      # ignore items too small to say anything about
DECLINE_RATIO_MAX = 0.20   # recent 6mo must be under this fraction of prior 6mo
FORECAST_RATIO_MIN = 0.40  # ...yet the model's next 6mo forecast is still
                           # at least this fraction of that faded prior volume

MIN_ZERO_STOCK_DAYS = 60     # SAP balance must have read 0 for at least this
                             # long (first-zero-snapshot to latest-snapshot)
MIN_ZERO_SNAPSHOTS = 2       # ...across at least this many distinct SAP
                             # snapshots, so one noisy/mis-timed read can't
                             # trigger this alone


def _add_months(d: date, n: int) -> date:
    y = d.year + (d.month - 1 + n) // 12
    m = (d.month - 1 + n) % 12 + 1
    return date(y, m, 1)


def find_winddown_candidates(session, run: ForecastRun | None = None) -> list[dict]:
    """Items with a sharp recent demand falloff that the forecast hasn't
    caught up to yet. Returns one row per candidate item, sorted by the
    forecast volume most at risk of being an overstatement (largest
    next-6-month forecast first)."""
    if run is None:
        run = session.query(ForecastRun).order_by(ForecastRun.id.desc()).first()
    if run is None:
        return []

    first_month = (session.query(Forecast.month)
                   .filter(Forecast.run_id == run.id)
                   .order_by(Forecast.month).first())
    if not first_month:
        return []
    start = first_month[0]
    recent6 = [_add_months(start, -i) for i in range(1, 7)]
    prior6 = [_add_months(start, -i) for i in range(7, 13)]
    next6_cutoff = _add_months(start, 6)

    series = _series_by_item(session)

    fc_total: dict[int, float] = {}
    fc_next6: dict[int, float] = {}
    fc_method: dict[int, str] = {}
    for f in session.query(Forecast).filter(Forecast.run_id == run.id):
        fc_total[f.item_id] = fc_total.get(f.item_id, 0.0) + (f.baseline or 0.0)
        fc_method[f.item_id] = f.method
        if f.month < next6_cutoff:
            fc_next6[f.item_id] = fc_next6.get(f.item_id, 0.0) + (f.baseline or 0.0)

    candidates = []
    for item_id, total in fc_total.items():
        if total <= 1e-6:
            continue
        obs = series.get(item_id, {})
        recent_sum = sum(obs.get(m, 0.0) or 0.0 for m in recent6)
        prior_sum = sum(obs.get(m, 0.0) or 0.0 for m in prior6)
        if prior_sum < MIN_PRIOR_VOLUME:
            continue
        decline_ratio = recent_sum / prior_sum
        next6 = fc_next6.get(item_id, 0.0)
        if decline_ratio < DECLINE_RATIO_MAX and next6 > prior_sum * FORECAST_RATIO_MIN:
            item = session.query(Item).filter(Item.id == item_id).first()
            if item is None:
                continue
            candidates.append({
                "item_id": item_id, "name": item.canonical_name,
                "category": item.category, "lifecycle": item.lifecycle,
                "prior_6mo_actual": round(prior_sum, 1),
                "recent_6mo_actual": round(recent_sum, 1),
                "decline_pct": round(100 * (1 - decline_ratio), 1),
                "next_6mo_forecast": round(next6, 1),
                "method": fc_method.get(item_id),
                "marked_no_reorder": item.marked_no_reorder,
            })
    candidates.sort(key=lambda c: -c["next_6mo_forecast"])
    return candidates


def find_zero_stock_candidates(session, run: ForecastRun | None = None) -> dict:
    """Items whose real, SAP-VERIFIED physical stock (sap_qty — never
    ONE's simulated one_balance, see verify/stock_reconciliation.py) has
    read exactly 0 across EVERY observed SAP snapshot, for at least
    MIN_ZERO_STOCK_DAYS spanning MIN_ZERO_SNAPSHOTS distinct dates: nobody
    has bothered to restock this part in a long time, a decent proxy for
    "discontinued" independent of the demand-decline signal above.

    Returns both the candidates and the current SAP snapshot data-depth
    (oldest to newest date observed anywhere in the DB), so a caller can
    tell "checked, found nothing" apart from "not enough accumulated
    history yet to say anything real" — with a single ingestion run this
    will always be the latter; rebuild_preserving.py's weekly cycle
    accumulates more StockState history from here without wiping it."""
    if run is None:
        run = session.query(ForecastRun).order_by(ForecastRun.id.desc()).first()

    rows = (session.query(StockState.item_id, StockState.snapshot_date, StockState.sap_qty)
            .filter(StockState.sap_qty.isnot(None), StockState.item_id.isnot(None))
            .order_by(StockState.item_id, StockState.snapshot_date).all())
    by_item: dict[int, list[tuple[date, float]]] = {}
    for item_id, snap_date, qty in rows:
        if snap_date is None:
            continue
        by_item.setdefault(item_id, []).append((snap_date, qty))

    all_dates = [d for pairs in by_item.values() for d, _ in pairs]
    data_depth_days = (max(all_dates) - min(all_dates)).days if all_dates else 0

    next6_cutoff = None
    if run is not None:
        first_month = (session.query(Forecast.month).filter(Forecast.run_id == run.id)
                       .order_by(Forecast.month).first())
        if first_month:
            next6_cutoff = _add_months(first_month[0], 6)
    fc_next6: dict[int, float] = {}
    if next6_cutoff is not None:
        for f in session.query(Forecast).filter(Forecast.run_id == run.id):
            if f.month < next6_cutoff:
                fc_next6[f.item_id] = fc_next6.get(f.item_id, 0.0) + (f.baseline or 0.0)

    candidates = []
    for item_id, pairs in by_item.items():
        pairs.sort()
        latest_date, latest_qty = pairs[-1]
        if latest_qty != 0:
            continue  # has real stock right now — not a candidate

        # trailing run of zero-balance snapshots through to the latest one;
        # a single nonzero reading anywhere in that trailing window breaks
        # the streak (a real restock, however small, means it's still active)
        zero_run_dates = []
        for d, qty in reversed(pairs):
            if qty == 0:
                zero_run_dates.append(d)
            else:
                break
        if len(zero_run_dates) < MIN_ZERO_SNAPSHOTS:
            continue
        first_zero_date = min(zero_run_dates)
        duration_days = (latest_date - first_zero_date).days
        if duration_days < MIN_ZERO_STOCK_DAYS:
            continue

        item = session.get(Item, item_id)
        if item is None:
            continue
        candidates.append({
            "item_id": item_id, "name": item.canonical_name,
            "category": item.category, "lifecycle": item.lifecycle,
            "zero_since": first_zero_date.isoformat(),
            "latest_snapshot": latest_date.isoformat(),
            "zero_duration_days": duration_days,
            "zero_snapshots_observed": len(zero_run_dates),
            "next_6mo_forecast": round(fc_next6.get(item_id, 0.0), 1),
            "marked_no_reorder": item.marked_no_reorder,
        })
    candidates.sort(key=lambda c: -c["zero_duration_days"])
    return {"candidates": candidates, "data_depth_days": data_depth_days,
           "sap_items_with_history": len(by_item)}


def run(session=None) -> dict:
    own_session = session is None
    if own_session:
        session = get_session()
    try:
        latest_run = session.query(ForecastRun).order_by(ForecastRun.id.desc()).first()
        candidates = find_winddown_candidates(session, latest_run)
        zero_stock = find_zero_stock_candidates(session, latest_run)
        return {
            "run_id": latest_run.id if latest_run else None,
            "count": len(candidates),
            "candidates": candidates,
            "note": ("Items whose last 6 months of real demand fell to under "
                    f"{DECLINE_RATIO_MAX * 100:.0f}% of the 6 months before "
                    "that, but where the model still forecasts the NEXT 6 "
                    f"months at {FORECAST_RATIO_MIN * 100:.0f}% or more of "
                    "that older, already-faded volume. This is not a "
                    "confirmed discontinuation — trend/seasonal methods can "
                    "legitimately still expect residual demand from a real "
                    "decline. It flags candidates worth a human look, the "
                    "same way source-reconciliation and compat-rule drafts "
                    "do: the system surfaces, a person decides. Only "
                    "NotePC items carry an authoritative lifecycle flag "
                    "(Item.lifecycle); every other category is inferred "
                    "from demand history alone, so this report is the "
                    "closest independent check available for those."),
            "zero_stock_count": len(zero_stock["candidates"]),
            "zero_stock_candidates": zero_stock["candidates"],
            "sap_data_depth_days": zero_stock["data_depth_days"],
            "sap_items_with_history": zero_stock["sap_items_with_history"],
            "zero_stock_note": ("Items whose SAP-verified physical stock (never "
                    "ONE's simulated projection) read exactly 0 across EVERY "
                    f"observed SAP snapshot, for at least {MIN_ZERO_STOCK_DAYS} "
                    f"days spanning {MIN_ZERO_SNAPSHOTS}+ distinct snapshot "
                    "dates — nobody has restocked this part in a long time, a "
                    "signal independent of the demand-decline candidates above. "
                    f"Current SAP history depth in this DB: {zero_stock['data_depth_days']} "
                    "day(s) across all items — this list will only get more "
                    "meaningful as weekly rebuilds accumulate more snapshots "
                    "(rebuild_preserving.py); a shallow depth here means "
                    "'not enough history yet', not 'nothing found'."),
        }
    finally:
        if own_session:
            session.close()


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2, ensure_ascii=False, default=str))
