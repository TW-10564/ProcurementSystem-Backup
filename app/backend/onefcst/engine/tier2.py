"""Tier-2 (derived) part forecasting — pure functions, no I/O.

Per ONE計画.xlsm's own 仕様定義 (spec-definition) sheet — the system's
original design document, never fully implemented (製品マスター/製品計画
sheets it references don't exist in the live workbook) — the intended
method for accessory/customization parts is:

  カスタマイズ部品は組込実績から推算
  ("customization parts are estimated from actual assembly/embed records")

This module implements exactly that, without needing the BOM table the
original design assumed: instead of a per-product attach count, it uses
each Tier-2 part's own historical correlation with TOTAL Tier-1 production
(CPU+VGA+MB+Case — config.TIER1_CATEGORIES), since a part like a case fan
or a cable scales with how many PCs get built, not with one specific model.

  attach_rate = median(tier2_qty[m] / tier1_total[m]) over overlapping
                historical months (median, not mean — robust to a single
                noisy month; requires >= MIN_OVERLAP_MONTHS non-zero pairs
                or the rate is refused rather than guessed)
  forecast[m] = attach_rate × tier1_total_forecast[m]

This only reaches parts that have SOME historical plan_value (even sparse
or stale) — for parts with literally zero history (no correlation possible
at all), no forecast can be derived this way; see project notes on the
BOM/catalog-parsing path as the alternative for those.
"""
from __future__ import annotations

import statistics
from datetime import date

from .baseline import wape

MIN_OVERLAP_MONTHS = 3     # fewer than this and a ratio is noise, not a rate
HOLDOUT_MONTHS = 6         # same window discipline as the SKU engine
OUTLIER_MULTIPLE = 15      # a month more than this many times the trailing
                           # median is source corruption, not real demand —
                           # found 2026-07-07: 部品予測's 2027-03 column has a
                           # broken chain-formula coefficient that explodes
                           # EVERY CPU SKU's projection into the tens of
                           # millions for that one month (e.g. i913900K:
                           # 74,823,210 — cf. its neighboring months in the
                           # hundreds). Confirmed via raw cell inspection,
                           # not an ingestion bug on our side.


def sanitize_series(series: dict[date, float]) -> dict[date, float]:
    """Drop months that are implausibly large relative to the series'
    trailing history — a cheap, deterministic guard against source-file
    formula corruption (see OUTLIER_MULTIPLE), applied BEFORE any of this
    module's aggregation so one broken coefficient can't silently explode
    a whole company-wide total."""
    months = sorted(series)
    clean: dict[date, float] = {}
    window: list[float] = []
    for m in months:
        v = series[m] or 0.0
        recent = [x for x in window[-12:] if x > 0]
        if recent:
            baseline = statistics.median(recent)
            if baseline > 0 and v > baseline * OUTLIER_MULTIPLE:
                continue                     # drop — treat as missing, not zero
        clean[m] = v
        window.append(v)
    return clean


def monthly_ratios(item_obs: dict[date, float],
                   tier1_series: dict[date, float]) -> dict[date, float]:
    """{month: item_qty/tier1_qty} for months where both are present and
    the tier1 total is nonzero — the raw material for an attach rate."""
    return {m: item_obs[m] / tier1_series[m]
            for m in item_obs
            if m in tier1_series and (tier1_series[m] or 0) > 0
            and item_obs[m] is not None}


def compute_attach_rate(item_obs: dict[date, float], tier1_series: dict[date, float],
                        before: date | None = None) -> float | None:
    """Median historical ratio, using only months strictly before `before`
    when given (so a hold-out test can fit on past data only). None when
    there's too little overlap to trust a rate."""
    ratios = monthly_ratios(item_obs, tier1_series)
    if before is not None:
        ratios = {m: r for m, r in ratios.items() if m < before}
    values = [r for r in ratios.values() if r > 0]
    if len(values) < MIN_OVERLAP_MONTHS:
        return None
    return statistics.median(values)


def make_tier2_forecast(item_obs: dict[date, float], tier1_series: dict[date, float],
                        tier1_forecast: dict[date, float]) -> list[tuple[date, float]] | None:
    """attach_rate (fit on ALL available history) × tier1's own forecast,
    for every month tier1_forecast covers. None if no attach rate is
    computable (insufficient overlapping history)."""
    rate = compute_attach_rate(item_obs, tier1_series)
    if rate is None:
        return None
    return [(m, round(rate * (tier1_forecast.get(m) or 0.0), 1))
            for m in sorted(tier1_forecast)]


MIN_CATEGORY_MEMBERS = 3   # fewer peers and "typical rate" is barely an average
MAX_CATEGORY_RATE_CV = 1.5 # coefficient of variation (stdev/median) above this
                           # means the category is too heterogeneous for a
                           # single "typical" rate to mean anything — found
                           # 2026-07-07: category "ｿﾌﾄｳｪｱS" lumps Windows/
                           # Office licenses together with unrelated bundled
                           # items (e.g. a game bundle) under one label; peer
                           # rates spanned an 840x range (CV=3.43), so the
                           # borrowed rate applied to something like a game
                           # bundle was never meaningful — it just LOOKED
                           # like a real number. Refusing here means fewer
                           # forecasts, not wrong ones.


def category_typical_rate(member_rates: list[float]) -> float | None:
    """Median attach rate across category members that DO have their own
    history — the rate borrowed for a member with NONE at all. Refuses
    (returns None) when there are too few peers (< MIN_CATEGORY_MEMBERS) OR
    when the peers' own rates are too inconsistent to average meaningfully
    (see MAX_CATEGORY_RATE_CV) — a category label alone doesn't guarantee
    the members are actually comparable products."""
    if len(member_rates) < MIN_CATEGORY_MEMBERS:
        return None
    median = statistics.median(member_rates)
    if median > 0:
        cv = statistics.stdev(member_rates) / median
        if cv > MAX_CATEGORY_RATE_CV:
            return None
    return median


def leave_one_out_wape(member_obs: dict[date, float], other_member_rates: list[float],
                       tier1_series: dict[date, float]) -> float | None:
    """The honest question a zero-history item's forecast rests on: 'if
    this member had no history of its own, and we used its PEERS' typical
    rate instead, how wrong would that have been over its entire known
    history?' Excludes the member's own rate from other_member_rates by
    construction (caller's job) — this is cross-member validation, not a
    time hold-out, since there's no future to hide for a method whose
    whole point is having nothing to fit on."""
    rate = category_typical_rate(other_member_rates)
    if rate is None:
        return None
    months = sorted(m for m in member_obs if m in tier1_series)
    if not months:
        return None
    actual = [member_obs[m] or 0.0 for m in months]
    predicted = [rate * (tier1_series.get(m) or 0.0) for m in months]
    return wape(actual, predicted)


def holdout_eval(item_obs: dict[date, float], tier1_series: dict[date, float],
                 start_month: date, test_months: int = HOLDOUT_MONTHS) -> dict | None:
    """Hide the last test_months, fit the attach rate on strictly earlier
    history, apply it to the REAL (already-happened) tier1 totals for the
    test window — isolating the attach-rate step's own error from tier-1's
    forecast error, which is separately characterized (~11% WAPE at
    category level; see verify/backtest.py). The number here answers "how
    good is the RATIO," not "how good is the end-to-end pipeline" — the
    real-world number compounds both."""
    from .baseline import add_months, month_range
    test_start = add_months(start_month, -test_months)
    rate = compute_attach_rate(item_obs, tier1_series, before=test_start)
    if rate is None:
        return None
    months = month_range(test_start, test_months)
    actual = [item_obs.get(m, 0.0) or 0.0 for m in months]
    if sum(abs(a) for a in actual) == 0:
        return None
    predicted = [rate * (tier1_series.get(m) or 0.0) for m in months]

    naive_hist = [v for m, v in item_obs.items() if m < test_start]
    naive_level = (sum(naive_hist[-6:]) / len(naive_hist[-6:])) if naive_hist else 0.0
    naive_predicted = [naive_level] * test_months

    return {"attach_rate": round(rate, 6), "wape": wape(actual, predicted),
           "naive_wape": wape(actual, naive_predicted), "actual_total": sum(actual)}
