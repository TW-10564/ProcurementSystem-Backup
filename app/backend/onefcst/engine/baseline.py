"""Per-SKU baseline forecast — pure functions, no I/O.

Method (no-ML policy, POC_BUILD_PLAN §"no ML" decision): per SKU, simple
candidate forecasters compete on a walk-forward hold-out over that SKU's
own last 6 observed months, and the winner produces the live forecast:

  seasonal — level (deseasonalized recent mean) × calendar-month index,
             the same idea proven at company level in seasonality.py
  trend    — damped 3-month-block ratio, for EOL/transition ramps
  flat6    — trailing 6-month mean, held flat
  flat3    — trailing 3-month mean, held flat (fastest to track a level shift)
  median6  — trailing 6-month MEDIAN, held flat (ignores a 1-2 month spike
             that a mean-based method would bake into the whole horizon)
  combo    — equal-weight average of all the above. Classic forecast
             combination: on noisy series the mean of several weak methods
             is routinely steadier than the single best one, because their
             individual errors partly cancel. Measured on real data
             (2026-07-07 experiment): volume-weighted hold-out WAPE 115.7%
             vs 121.5% for winner-take-all over the same five.

Why competition instead of always-seasonal: measured on real 部品予測
history, a blanket per-SKU seasonal fit LOST to a naive trailing mean
(WAPE 154% vs 136% volume-weighted) — per-SKU monthly demand is too lumpy
for 36 months of history to pin a reliable seasonal shape. Picking per SKU
by out-of-sample error keeps seasonality where it earns its place and a
robust mean everywhere else.

SKUs whose series has gone quiet (no nonzero value in the last 6 months)
are skipped rather than projected from stale volume — an EOL part must not
get a forecast from its glory days.

Everything here is deterministic and side-effect-free so the SAME code both
produces the live forecast and is scored in the hold-out validation that
forecasting.generate() runs and stores on every run.
"""
from __future__ import annotations

import statistics
from datetime import date

SEASONAL_WINDOW = 36        # months of history the seasonal fit may use
SEASONAL_MIN_MONTHS = 18    # below this, seasonality is noise → not a candidate
LEVEL_MONTHS = 6            # recent months that set the seasonal volume level
STALE_MONTHS = 6            # no data in the last N months → skip the SKU
HOLDOUT_MONTHS = 6          # test window for method selection + validation
DEFAULT_METHOD = "flat3"    # used when history is too short to run a hold-out


def add_months(month: date, n: int) -> date:
    y, m = divmod(month.year * 12 + (month.month - 1) + n, 12)
    return date(y, m + 1, 1)


def month_range(start: date, n: int) -> list[date]:
    return [add_months(start, i) for i in range(n)]


def build_window(obs: dict[date, float], start_month: date,
                 max_months: int = SEASONAL_WINDOW) -> list[tuple[date, float]]:
    """Contiguous monthly series ending the month before start_month.
    Months inside the observed span with no value count as 0 (an absent month
    in 部品予測 means no planned usage, not missing data)."""
    past = [m for m in obs if m < start_month]
    if not past:
        return []
    first = min(past)
    n_total = ((start_month.year - first.year) * 12
               + (start_month.month - first.month))
    n = min(n_total, max_months)
    months = month_range(add_months(start_month, -n), n)
    return [(m, obs.get(m, 0.0) or 0.0) for m in months]


def is_stale(obs: dict[date, float], start_month: date,
             stale_months: int = STALE_MONTHS) -> bool:
    """True when the series has no nonzero value in the recent past —
    forecasting such an item would project volume it no longer has."""
    cutoff = add_months(start_month, -stale_months)
    return not any(m >= cutoff and (v or 0) > 0 for m, v in obs.items()
                   if m < start_month)


def _flat(window: list[tuple[date, float]], months: list[date],
          n: int) -> list[tuple[date, float]]:
    recent = [v for _, v in window[-n:]]
    level = max(0.0, round(sum(recent) / len(recent), 1))
    return [(m, level) for m in months]


def _median_flat(window: list[tuple[date, float]], months: list[date],
                 n: int) -> list[tuple[date, float]] | None:
    """Trailing MEDIAN (not mean) of the last n months, held flat.

    A mean lets one anomalous month drag the whole level with it — e.g. a
    2-month order spike that already reverted the following month still
    pulls flat6/trend upward for the entire forecast horizon. The median
    ignores an outlier as long as it isn't the majority of the window, so
    this candidate only wins the per-SKU hold-out (baseline.holdout_eval)
    when it actually predicts better — it doesn't override anything."""
    if len(window) < n:
        return None
    recent = [v for _, v in window[-n:]]
    level = max(0.0, round(statistics.median(recent), 1))
    return [(m, level) for m in months]


TREND_CLAMP = (0.70, 1.15)  # monthly ratio bounds — declines track faster than growth
TREND_DAMPING = 0.85        # φ: each further-out month applies less of the trend


def _trend(window: list[tuple[date, float]],
           months: list[date]) -> list[tuple[date, float]] | None:
    """Damped trend: recent 3-month level extrapolated by the monthly ratio
    between the last two 3-month blocks, clamped and damped (the cumulative
    trend exponent is Σφ^i, so the projection flattens instead of compounding
    forever). Exists because EOL / product-transition ramps are the dominant
    real pattern in 部品予測 — a flat mean badly overshoots a series falling
    to zero."""
    if len(window) < 6:
        return None
    head = [v for _, v in window[-3:]]
    prev = [v for _, v in window[-6:-3]]
    h, p = sum(head) / 3, sum(prev) / 3
    if p <= 0 or h < 0:
        return None
    ratio = min(max((h / p) ** (1 / 3), TREND_CLAMP[0]), TREND_CLAMP[1])
    # h is centered on month −2, so the first forecast month is 2 steps out
    out = []
    exponent = 0.0
    for k, m in enumerate(months):
        exponent += TREND_DAMPING ** (k + 1) * (1 if k else 2)   # 2 steps to reach month 0
        out.append((m, max(0.0, round(h * ratio ** exponent, 1))))
    return out


def _seasonal(window: list[tuple[date, float]],
              months: list[date]) -> list[tuple[date, float]] | None:
    values = [v for _, v in window]
    grand = sum(values) / len(values)
    if grand <= 0:
        return None
    by_cal: dict[int, list[float]] = {}
    for m, v in window:
        by_cal.setdefault(m.month, []).append(v)
    index = {cm: (sum(vs) / len(vs)) / grand for cm, vs in by_cal.items()}
    recent = window[-LEVEL_MONTHS:]
    deseason = [v / index[m.month] for m, v in recent if index.get(m.month)]
    level = (sum(deseason) / len(deseason) if deseason
             else sum(v for _, v in recent) / len(recent))
    return [(m, max(0.0, round(level * index.get(m.month, 1.0), 1)))
            for m in months]


def candidate_forecasts(window: list[tuple[date, float]], start_month: date,
                        horizon: int) -> dict[str, list[tuple[date, float]]]:
    """Every method that can be fit on this window, keyed by name."""
    if not window:
        return {}
    months = month_range(start_month, horizon)
    out = {"flat3": _flat(window, months, min(3, len(window))),
           "flat6": _flat(window, months, min(6, len(window)))}
    median6 = _median_flat(window, months, LEVEL_MONTHS)
    if median6 is not None:
        out["median6"] = median6
    trend = _trend(window, months)
    if trend is not None:
        out["trend"] = trend
    if len(window) >= SEASONAL_MIN_MONTHS:
        seasonal = _seasonal(window, months)
        if seasonal is not None:
            out["seasonal"] = seasonal
    if len(out) >= 2:
        names = list(out)
        out["combo"] = [(m, round(sum(out[n][k][1] for n in names) / len(names), 1))
                        for k, m in enumerate(months)]
    return out


def wape(actual: list[float], predicted: list[float]) -> float | None:
    den = sum(abs(a) for a in actual)
    if not den:
        return None
    return 100 * sum(abs(a - p) for a, p in zip(actual, predicted)) / den


def holdout_eval(obs: dict[date, float], start_month: date,
                 test_months: int = HOLDOUT_MONTHS) -> dict | None:
    """Hide the last test_months observed months, forecast them with every
    candidate fit on the remaining history, score each. Returns
    {"wapes": {method: wape}, "winner": method, "actual_total": ...} or None
    when the history is too short or the test window is all zero."""
    test_start = add_months(start_month, -test_months)
    train_window = build_window(obs, test_start)
    if len(train_window) < 12:
        return None
    actual = [obs.get(m, 0.0) or 0.0
              for m in month_range(test_start, test_months)]
    if sum(abs(a) for a in actual) == 0:
        return None
    cands = candidate_forecasts(train_window, test_start, test_months)
    wapes = {name: w for name, rows in cands.items()
             if (w := wape(actual, [v for _, v in rows])) is not None}
    if not wapes:
        return None
    winner = min(wapes, key=wapes.get)
    # first-month error components per method — buyers act on M+1 (lead
    # times), so near-term accuracy is reported alongside the 6-month WAPE
    m1 = {name: abs(actual[0] - rows[0][1]) for name, rows in cands.items()}
    return {"wapes": {k: round(v, 2) for k, v in wapes.items()},
            "winner": winner, "actual_total": sum(actual),
            "m1_err": m1, "m1_actual": abs(actual[0])}


def make_forecast(obs: dict[date, float], start_month: date,
                  horizon: int = 12) -> tuple[list[tuple[date, float]], str] | None:
    """Full pipeline for one SKU: pick the method that won this SKU's
    hold-out (DEFAULT_METHOD when no hold-out is possible) and forecast the
    full window with it. None → item skipped (stale / no usable history)."""
    if not obs or is_stale(obs, start_month):
        return None
    window = build_window(obs, start_month)
    if not window:
        return None
    cands = candidate_forecasts(window, start_month, horizon)
    if not cands:
        return None
    ev = holdout_eval(obs, start_month)
    method = ev["winner"] if ev and ev["winner"] in cands else DEFAULT_METHOD
    if method not in cands:
        method = next(iter(cands))
    return cands[method], method
