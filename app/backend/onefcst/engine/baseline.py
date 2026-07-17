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
SEASONAL_DECLINE_GATE = 0.5  # recent-3mo level must be at least this fraction
                             # of the SAME 3-month period one year earlier
                             # (year-over-year, not vs the window's all-time
                             # peak — an all-time-peak version false-positived
                             # on genuinely cyclical series, since ANY normal
                             # seasonal trough sits well below the annual
                             # peak; a proven test fixture (12-month sawtooth,
                             # tests/test_baseline.py) failed under that
                             # version), or 'seasonal' is withheld as a
                             # candidate. Verified 2026-07-09: when a SKU is
                             # winding down, seasonal's deseasonalization step
                             # divides the (already depressed) recent level by
                             # a calendar-month index derived from the OLD,
                             # higher-volume months — pulling the forecast
                             # back toward the pre-decline level instead of
                             # tracking the real drop. Real case: TFX300 held
                             # prior demand ~700-3,800/mo for 30 months then
                             # collapsed to 7-535/mo in the final 3 — seasonal
                             # won its selection hold-out (252.5% WAPE,
                             # decisively ahead of every other candidate) but
                             # scored 1171.3% out-of-sample, worse than ALL
                             # other candidates including naive flat6
                             # (541.4%); gating it here drops TFX300 to 144.5%
                             # via the existing DEFAULT_METHOD fallback in
                             # make_forecast(). Portfolio-wide backtest (273
                             # items) with the year-over-year version: overall
                             # WAPE 99.44%→98.37%, 電源S 138.78%→130.87% (now
                             # beating naive), ｻﾌﾟﾗｲ用品N 240.85%→172.11%.
LEVEL_MONTHS = 6            # recent months that set the seasonal volume level
STALE_MONTHS = 6            # no data in the last N months → skip the SKU
HOLDOUT_MONTHS = 6          # test window for method selection + validation
DEFAULT_METHOD = "flat3"    # used when history is too short to run a hold-out
WINNER_MARGIN = 10.0        # relative WAPE points a single method must beat
                            # combo by to be crowned outright (FIX_PLAN M1) —
                            # winner-take-all on a noisy 6-point test window
                            # overfits to that window's noise; measured
                            # 2026-07-07, combo (equal-weight average of all
                            # candidates) beats winner-take-all 115.7% vs
                            # 121.5% volume-weighted WAPE. Only override
                            # combo when a candidate demonstrably clears it
                            # by a real margin, not hold-out noise.


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


def _is_seasonal_eligible(window: list[tuple[date, float]]) -> bool:
    """False when the series has collapsed relative to itself a year ago
    (see SEASONAL_DECLINE_GATE) — a winding-down SKU where seasonal's
    calendar-index deseasonalization would pull the forecast back toward
    the old, higher-volume level instead of tracking the real decline.

    Deliberately year-over-year, not vs the window's all-time peak: a
    normal seasonal trough also sits well below the annual peak, so an
    all-time-peak comparison false-positives on exactly the genuinely
    cyclical series this method exists for (caught by
    test_holdout_picks_seasonal_for_seasonal_series). Comparing the same
    3-month period 12 months apart isolates a real year-over-year drop
    from ordinary within-year seasonality."""
    if len(window) < 15:
        return True
    vals = [v for _, v in window]
    year_ago3 = sum(vals[-15:-12]) / 3
    if year_ago3 <= 0:
        return True
    recent3 = sum(vals[-3:]) / 3
    return recent3 / year_ago3 >= SEASONAL_DECLINE_GATE


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
    if len(window) >= SEASONAL_MIN_MONTHS and _is_seasonal_eligible(window):
        seasonal = _seasonal(window, months)
        if seasonal is not None:
            out["seasonal"] = seasonal
    if len(out) >= 2:
        names = list(out)
        out["combo"] = [(m, round(sum(out[n][k][1] for n in names) / len(names), 1))
                        for k, m in enumerate(months)]
    return out


def wape(actual: list[float], predicted: list[float], *, skip_none: bool = False,
        round_to: int | None = None) -> float | None:
    """Weighted Absolute Percentage Error — the one copy shared by baseline,
    tier2, and fva scoring (previously three independent copies that could
    silently drift; consolidated 2026-07-14).

    skip_none=True drops any (actual, predicted) pair where either side is
    None instead of letting abs() raise — engine/fva.py's callers can see
    un-forecasted months. round_to rounds the result (engine/fva.py rounds
    for display; baseline/tier2 leave it unrounded)."""
    pairs = zip(actual, predicted)
    if skip_none:
        pairs = [(a, p) for a, p in pairs if a is not None and p is not None]
    else:
        pairs = list(pairs)
    den = sum(abs(a) for a, _ in pairs)
    if not den:
        return None
    result = 100 * sum(abs(a - p) for a, p in pairs) / den
    return round(result, round_to) if round_to is not None else result


def select_winner(wapes: dict[str, float]) -> str:
    """Pick the hold-out winner from a {method: wape} map (FIX_PLAN M1).

    Winner-take-all on a noisy 6-point test window overfits to that
    window's noise — measured 2026-07-07, combo (equal-weight average of
    all candidates) beats winner-take-all 115.7% vs 121.5% volume-weighted
    WAPE. So only crown a single method over combo when it clears combo's
    WAPE by a real margin (WINNER_MARGIN); otherwise default to combo,
    which is the steadier choice on noisy per-SKU series."""
    best = min(wapes, key=wapes.get)
    combo_wape = wapes.get("combo")
    if combo_wape is None or best == "combo" or wapes[best] < combo_wape - WINNER_MARGIN:
        return best
    return "combo"


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
    winner = select_winner(wapes)
    # first-month error components per method — buyers act on M+1 (lead
    # times), so near-term accuracy is reported alongside the 6-month WAPE
    m1 = {name: abs(actual[0] - rows[0][1]) for name, rows in cands.items()}
    return {"wapes": {k: round(v, 2) for k, v in wapes.items()},
            "winner": winner, "actual_total": sum(actual),
            "m1_err": m1, "m1_actual": abs(actual[0])}


KNOWN_PLAN_OUTLIER_MULTIPLE = 15   # same guard, same threshold, as
                                    # engine/tier2.sanitize_series — the
                                    # documented 部品予測 chain-formula bug
                                    # (one future month exploding to
                                    # millions for every affected SKU) lives
                                    # in the SAME plan_value data this reads.


def apply_known_plan(rows: list[tuple[date, float]],
                     obs: dict[date, float]) -> tuple[list[tuple[date, float]], int]:
    """Override any forecast month with the source data's OWN already-
    entered value for that exact month, when one exists AND it isn't the
    documented source-file corruption (see KNOWN_PLAN_OUTLIER_MULTIPLE).

    Found 2026-07-08 during a Data Summary cross-check: obs already carries
    every month the source data covers — past AND future alike, since
    forecasting._series_by_item builds it from the full plan_value history
    without filtering by date. The statistical methods deliberately build
    their fitting window from months strictly before start_month (see
    build_window), so a real, already-committed number for month M+1/M+2/
    M+3 sitting right there in 部品予測 or the sub-file's own 生産計画 was
    never used — the pipeline always re-derived a fresh guess instead, even
    when the business had already decided the number. Measured impact:
    forecast vs the known plan ran +6.7% high one month out, growing to
    +24-25% two-three months out, as the (necessarily flatter) statistical
    projection failed to track an already-decided decline the source data
    already showed. A month that's already been decided is not a month to
    guess at — this only affects the ~4-month window where the business
    actually has committed numbers (see project notes); further out, obs
    has no entry and the statistical projection is unchanged.

    Found immediately after shipping the above: blindly trusting ANY future
    obs value reopened the exact corruption engine/tier2.sanitize_series
    already guards against elsewhere in this same data (one far-future
    month's coefficient exploding to absurd values for an affected SKU) —
    this override has its own copy of that guard so a corrupted "known"
    value can never win over a sane statistical one. Returns (rows,
    n_overridden) so the caller can label which months came from the real
    plan vs a genuine statistical projection."""
    # sorted by month, not dict-iteration order: obs is a per-month merge of
    # two sources (master preferred, one_row fallback — see
    # forecasting._series_by_item) built as one dict.update()'d onto
    # another, so its iteration order is NOT chronological — master-only
    # months (which can include ancient history one_row never covered)
    # land at the end of iteration order regardless of their actual date.
    # Found 2026-07-08: this silently fed the "trailing 12" slice a
    # handful of stray 2017-era near-zero months instead of the real
    # recent window, making the corruption guard reject perfectly
    # legitimate near-term known values as false-positive outliers.
    recent_sorted = sorted((m, v) for m, v in obs.items()
                          if rows and m < rows[0][0] and v and v > 0)
    recent_level = (statistics.median(v for _, v in recent_sorted[-12:])
                    if recent_sorted else None)

    # Cross-check against the OTHER known months in this SAME forecast
    # window (added 2026-07-17, found via real-SKU audit): a genuine
    # sustained ramp — every known month agreeing on a new, higher level —
    # was being rejected wholesale just because recent_level (anchored to
    # OLD history, sometimes across a data gap of a year+) hadn't caught
    # up yet. Real corruption (the documented chain-formula bug) shows as
    # ONE month exploding while its neighbors stay normal — an isolated
    # spike, not multi-month agreement. window_level is the median of every
    # OTHER known month in `rows`' own span; a value close to ITS OWN
    # window peers is real regardless of how far it sits from ancient
    # history, and is only rejected as corrupt when it disagrees with both
    # recent_level AND its own window peers.
    known_in_window = {m: obs[m] for m, _ in rows if m in obs and obs[m] and obs[m] > 0}

    out = []
    n_overridden = 0
    for m, v in rows:
        known = obs.get(m)
        window_peers = [kv for km, kv in known_in_window.items() if km != m]
        window_level = statistics.median(window_peers) if window_peers else None
        agrees_with_window = (known is not None and window_level is not None
                              and known <= window_level * KNOWN_PLAN_OUTLIER_MULTIPLE)
        is_corrupt = (known is not None and recent_level
                     and known > recent_level * KNOWN_PLAN_OUTLIER_MULTIPLE
                     and not agrees_with_window)
        if known is not None and not is_corrupt:
            out.append((m, known))
            n_overridden += 1
        else:
            out.append((m, v))
    return out, n_overridden


DISCONTINUATION_MIN_ZERO_MONTHS = 2  # consecutive KNOWN (not guessed) near-
                                     # zero months, immediately following
                                     # real historical demand, treated as a
                                     # likely wind-down rather than a data gap


def _is_likely_discontinuing(rows: list[tuple[date, float]],
                             obs: dict[date, float]) -> bool:
    """True when this item has real prior demand AND its own known
    (already-committed, not statistically guessed) months run to zero for
    DISCONTINUATION_MIN_ZERO_MONTHS+ in a row right up to where the known
    window ends.

    Found 2026-07-08: an established item (54 months of real history) had
    its known plan correctly show zero for 4 straight confirmed months
    (a real transition) — but the month right after the known window ran
    out, the forecast jumped back to ~6,000 units, because the statistical
    method has no concept of "this is winding down" and just extrapolated
    the OLD historical average once it ran out of known data to override.
    A sustained run of confirmed zeros is strong enough evidence to keep
    projecting zero into the unknown tail too, rather than silently
    reverting to pre-transition volume."""
    if not rows:
        return False
    horizon_start = rows[0][0]
    has_prior_history = any(v and v > 0 for m, v in obs.items() if m < horizon_start)
    if not has_prior_history:
        return False
    run = 0
    for m, _ in rows:
        if m not in obs:
            break                          # known window ended — evaluate the run so far
        run = run + 1 if abs(obs[m]) < 1e-9 else 0
    return run >= DISCONTINUATION_MIN_ZERO_MONTHS


def apply_discontinuation_signal(rows: list[tuple[date, float]],
                                 obs: dict[date, float]) -> tuple[list[tuple[date, float]], bool]:
    """When _is_likely_discontinuing fires, carry the confirmed zero
    forward into every month beyond the known window too (months already
    in obs are untouched — apply_known_plan already set them correctly).
    Returns (rows, triggered) so the caller can label the method."""
    if not _is_likely_discontinuing(rows, obs):
        return rows, False
    return [(m, obs[m]) if m in obs else (m, 0.0) for m, v in rows], True


def make_forecast(obs: dict[date, float], start_month: date,
                  horizon: int = 6) -> tuple[list[tuple[date, float]], str] | None:
    """Full pipeline for one SKU: pick the method that won this SKU's
    hold-out (DEFAULT_METHOD when no hold-out is possible), forecast the
    full window with it, then let any already-known future plan value
    override the statistical guess for that specific month (see
    apply_known_plan). None → item skipped (stale / no usable history)."""
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
    rows, n_overridden = apply_known_plan(cands[method], obs)
    if n_overridden == len(rows):
        method = "known_plan"
    elif n_overridden > 0:
        method = f"{method}+known_plan"
    rows, discontinuing = apply_discontinuation_signal(rows, obs)
    if discontinuing:
        method = f"{method}+discontinuing"
    return rows, method
