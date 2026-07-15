"""Tier-2 (derived) forecast generation — populates the SAME forecast run
with attach-rate-derived rows for parts that never get a direct per-SKU
statistical forecast (see engine/tier2.py for the method and why — no BOM
table exists, so this uses each part's own historical correlation with
total Tier-1 production instead).

Runs AFTER the main per-SKU loop in forecasting.generate(), reusing that
run's freshly-computed Tier-1 forecasts as the thing to derive from. Writes
to the SAME Forecast/AccuracyScore tables as the SKU engine, distinguished
only by method="tier2_attach_rate" — forecast_views.py already handles
arbitrary method strings generically, so no serving-layer changes are
needed for these to show up in the API/UI.

Deliberately separate from every Tier-1 SKU (already forecasted by the
per-SKU engine) and every Tier-1-category item (the correlation input,
not something to also derive) — see generate_tier2's filtering.
"""
from __future__ import annotations

from datetime import date

from .config import TIER1_CATEGORIES
from .models import AccuracyScore, Actual, Forecast, Item, StockState
from .engine import baseline
from .engine import tier2 as tier2_engine

METHOD = "tier2_attach_rate"
CATEGORY_METHOD = "tier2_category_average"
MIN_HISTORY_MONTHS = 6   # below this, even a median attach rate is a guess
RECENCY_MONTHS = 6       # FIX_PLAN M2: baseline.is_stale() only looks at
                         # TRAILING PAST months before `start`, but 部品予測
                         # is a forward-looking plan — Tier-2 accessory parts
                         # have their nonzero values concentrated in FUTURE
                         # months relative to `start`, not the trailing 6, so
                         # is_stale wrongly flagged nearly every one of these
                         # 626 items as stale. This bidirectional check
                         # treats activity dated at/after `start` as current
                         # (it's live forward-looking plan data, not history),
                         # while still refusing an item whose last activity —
                         # past OR future — is entirely outside this window,
                         # which is what is_stale was protecting against in
                         # the first place (see test_tier2_forecasting.py's
                         # synthetic-decade-old-item regression test).


def _has_current_activity(obs: dict[date, float], start: date,
                          months: int = RECENCY_MONTHS) -> bool:
    cutoff = baseline.add_months(start, -months)
    return any((v or 0) > 0 for m, v in obs.items() if m >= cutoff)


def _tier1_historical_series(series: dict[int, dict[date, float]],
                             items: dict[int, Item]) -> dict[date, float]:
    """Monthly total across TIER1_CATEGORIES, sanitized against source-file
    corruption (see engine/tier2.sanitize_series — found and fixed
    2026-07-07: a broken 部品予測 coefficient exploding one future month)."""
    out: dict[date, float] = {}
    for item_id, obs in series.items():
        item = items.get(item_id)
        if item and item.category in TIER1_CATEGORIES:
            for m, v in obs.items():
                out[m] = out.get(m, 0.0) + (v or 0.0)
    return tier2_engine.sanitize_series(out)


def _tier1_forecast_totals(session, run_id: int, items: dict[int, Item]) -> dict[date, float]:
    """Monthly total across TIER1_CATEGORIES from THIS run's own just-
    written Forecast rows — the future half of the correlation."""
    rows = session.query(Forecast).filter(Forecast.run_id == run_id).all()
    out: dict[date, float] = {}
    for f in rows:
        item = items.get(f.item_id)
        if item and item.category in TIER1_CATEGORIES:
            m = date.fromisoformat(f.month) if isinstance(f.month, str) else f.month
            out[m] = out.get(m, 0.0) + (f.baseline or 0.0)
    return out


def generate_tier2(session, run, series: dict[int, dict[date, float]],
                   items: dict[int, Item], already_forecasted: set[int],
                   start: date) -> dict:
    """Forecast every item that (a) isn't Tier-1 category, (b) wasn't
    already given a per-SKU statistical forecast, and (c) has enough
    history for a trustworthy attach rate. Returns a summary merged into
    the run's note alongside the SKU engine's own summary."""
    tier1_hist = _tier1_historical_series(series, items)
    tier1_forecast = _tier1_forecast_totals(session, run.id, items)

    forecasted = 0
    skipped_stale = 0
    skipped_no_rate = 0
    scored = []
    forecasted_ids: set[int] = set()
    for item_id, obs in series.items():
        if item_id in already_forecasted:
            continue
        item = items.get(item_id)
        if item is None or item.category in TIER1_CATEGORIES:
            continue
        if len(obs) < MIN_HISTORY_MONTHS:
            continue
        if not _has_current_activity(obs, start):
            skipped_stale += 1
            continue
        rows = tier2_engine.make_tier2_forecast(obs, tier1_hist, tier1_forecast)
        if rows is None:
            skipped_no_rate += 1
            continue
        forecasted += 1
        forecasted_ids.add(item_id)
        # same known-plan override as the SKU engine (baseline.make_forecast)
        # — an attach-rate GUESS for a month the item's own source data
        # already has a real, committed number for is worse than just
        # using that number.
        # "tier2_known_plan", not bare "known_plan" — forecasting.generate()'s
        # n_by_method bookkeeping counts the SKU engine's own "known_plan"
        # label separately from tier2's stages; reusing the same string
        # would silently double up that count against a different total.
        rows, n_overridden = baseline.apply_known_plan(rows, obs)
        if n_overridden == len(rows):
            row_method = "tier2_known_plan"
        elif n_overridden > 0:
            row_method = f"{METHOD}+known_plan"
        else:
            row_method = METHOD
        rows, discontinuing = baseline.apply_discontinuation_signal(rows, obs)
        if discontinuing:
            row_method = f"{row_method}+discontinuing"
        session.add_all([Forecast(run_id=run.id, item_id=item_id, month=m,
                                  baseline=v, method=row_method) for m, v in rows])

        ev = tier2_engine.holdout_eval(obs, tier1_hist, start)
        if ev and ev["wape"] is not None:
            scored.append(ev)
            session.add(AccuracyScore(run_id=run.id, item_id=item_id,
                                      category=item.category, month=start,
                                      level=METHOD, wape=ev["wape"]))

    def agg(rows):
        tot = sum(s["actual_total"] for s in rows)
        if not tot:
            return None
        return {"wape": round(sum(s["wape"] * s["actual_total"] for s in rows) / tot, 2),
                "naive_wape": round(sum((s["naive_wape"] or 0) * s["actual_total"]
                                        for s in rows) / tot, 2),
                "items": len(rows)}

    item_summary = {"items_forecasted": forecasted,
                    "items_skipped_stale": skipped_stale,
                    "items_skipped_no_attach_rate": skipped_no_rate,
                    "holdout": {"overall": agg(scored),
                               "note": "attach-rate-only error, isolated from Tier-1's "
                                       "own forecast error (separately ~11% at category "
                                       "level, see verify/backtest.py) — the real-world "
                                       "error compounds both. See engine/tier2.holdout_eval."}}

    category_summary = generate_category_fallback(
        session, run, series, items, already_forecasted | forecasted_ids, tier1_hist,
        tier1_forecast, start)
    item_summary["category_fallback"] = category_summary
    return item_summary


def generate_category_fallback(session, run, series: dict[int, dict[date, float]],
                               items: dict[int, Item], already_handled: set[int],
                               tier1_hist: dict[date, float],
                               tier1_forecast: dict[date, float], start: date) -> dict:
    """The items generate_tier2 fundamentally cannot reach: those with ZERO
    plan_value history ever (no ratio to compute — item-level attach rate
    needs the item's OWN past consumption). For these, borrow the MEDIAN
    attach rate from category peers that DO have history — validated by
    leave-one-out (see engine/tier2.leave_one_out_wape): for each peer,
    "if THIS peer had no history, how wrong would the OTHER peers' rate
    have been over its real known history?" That question is answerable
    with real data; a zero-history item's own accuracy is not — so no
    AccuracyScore is recorded per zero-history item (there is no actual to
    grade against), only an aggregate confidence figure in this summary.
    """
    # group every non-Tier-1 item with SOME history by category, to build
    # each category's peer-rate pool
    by_category: dict[str, list[tuple[int, dict]]] = {}
    for item_id, obs in series.items():
        item = items.get(item_id)
        if item is None or item.category is None or item.category in TIER1_CATEGORIES:
            continue
        if not obs:
            continue
        by_category.setdefault(item.category, []).append((item_id, obs))

    category_rates: dict[str, float] = {}
    loo_scores: list[tuple[float, float]] = []   # (wape, actual_total) across all peers
    for category, members in by_category.items():
        rates = {}
        for item_id, obs in members:
            r = tier2_engine.compute_attach_rate(obs, tier1_hist)
            if r is not None:
                rates[item_id] = r
        if len(rates) < tier2_engine.MIN_CATEGORY_MEMBERS:
            continue
        category_rates[category] = tier2_engine.category_typical_rate(list(rates.values()))
        for item_id, obs in members:
            if item_id not in rates:
                continue
            others = [r for iid, r in rates.items() if iid != item_id]
            w = tier2_engine.leave_one_out_wape(obs, others, tier1_hist)
            if w is not None:
                loo_scores.append((w, sum(abs(v or 0) for v in obs.values())))

    # zero-plan-history items: no obs at all in `series`, category known, not
    # already handled by any earlier stage. But zero PlanValue history alone
    # isn't enough to trust a borrowed category rate — confirmed 2026-07-14
    # against real NAS/SAP data that 170/171 items this fallback forecasted
    # in one run had ALSO never once appeared in StockState or Actual (i.e.
    # absent from every live SAP stock snapshot, current AND backup, and
    # never had a recorded sale) — bare registry entries with no sign of
    # being active parts at all, not merely under-tracked live ones. Only
    # items with at least one real stock or actual record ever get the
    # borrowed-rate forecast; the rest are skipped (see
    # items_skipped_no_real_signal) rather than fabricating demand for a
    # part with no operational footprint whatsoever.
    ids_with_stock = {r[0] for r in session.query(StockState.item_id).distinct()}
    ids_with_actual = {r[0] for r in session.query(Actual.item_id).distinct()}

    forecasted = 0
    skipped_no_category_rate = 0
    skipped_no_real_signal = 0
    for item_id, item in items.items():
        if item_id in already_handled or item_id in series:
            continue
        if item.category is None or item.category in TIER1_CATEGORIES:
            continue
        if item_id not in ids_with_stock and item_id not in ids_with_actual:
            skipped_no_real_signal += 1
            continue
        rate = category_rates.get(item.category)
        if rate is None:
            skipped_no_category_rate += 1
            continue
        forecasted += 1
        session.add_all([Forecast(run_id=run.id, item_id=item_id, month=m,
                                  baseline=round(rate * (tier1_forecast.get(m) or 0.0), 1),
                                  method=CATEGORY_METHOD)
                        for m in sorted(tier1_forecast)])

    loo_wape = None
    if loo_scores:
        tot = sum(a for _, a in loo_scores)
        loo_wape = round(sum(w * a for w, a in loo_scores) / tot, 2) if tot else None

    return {"items_forecasted": forecasted,
           "items_skipped_no_category_rate": skipped_no_category_rate,
           "items_skipped_no_real_signal": skipped_no_real_signal,
           "categories_with_a_rate": len(category_rates),
           "leave_one_out_wape": loo_wape,
           "note": "leave_one_out_wape answers 'if a category member with real "
                   "history had none, how wrong would borrowing its peers' rate "
                   "have been over its own known history' — the honest proxy for "
                   "how much to trust this method on items with NO history at all, "
                   "since those have no actual to grade against directly."}
