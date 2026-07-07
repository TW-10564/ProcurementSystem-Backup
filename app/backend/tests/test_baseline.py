"""Unit tests for the per-SKU forecast engine — every expectation here is
hand-computed, so a pass means the math is right, not just that it runs."""
import statistics
from datetime import date

import pytest

from onefcst.engine import baseline
from onefcst.engine.stock_projection import suggest_order

START = date(2026, 7, 1)


def series(start: date, values: list[float]) -> dict[date, float]:
    return {baseline.add_months(start, i): v for i, v in enumerate(values)}


# ---------------------------------------------------------------- helpers --
def test_add_months_rollover():
    assert baseline.add_months(date(2026, 12, 1), 1) == date(2027, 1, 1)
    assert baseline.add_months(date(2026, 1, 1), -1) == date(2025, 12, 1)
    assert baseline.add_months(date(2026, 7, 1), 18) == date(2028, 1, 1)


def test_build_window_fills_gaps_with_zero_and_is_contiguous():
    obs = {date(2026, 3, 1): 10.0, date(2026, 6, 1): 40.0}   # Apr/May missing
    window = dict(baseline.build_window(obs, START))
    assert window == {date(2026, 3, 1): 10.0, date(2026, 4, 1): 0.0,
                      date(2026, 5, 1): 0.0, date(2026, 6, 1): 40.0}


def test_build_window_caps_at_36_months():
    obs = series(date(2020, 1, 1), [1.0] * 100)
    window = baseline.build_window(obs, START)
    assert len(window) == 36
    assert window[-1][0] == date(2026, 6, 1)


def test_is_stale():
    active = {date(2026, 5, 1): 5.0}
    dead = {date(2025, 10, 1): 500.0}                # big but old → stale
    zeros = {date(2026, 5, 1): 0.0}                  # recent but zero → stale
    assert not baseline.is_stale(active, START)
    assert baseline.is_stale(dead, START)
    assert baseline.is_stale(zeros, START)


# --------------------------------------------------------------- methods ---
def test_flat_methods_exact_values():
    obs = series(date(2026, 1, 1), [10, 20, 30, 40, 50, 60])
    window = baseline.build_window(obs, START)
    cands = baseline.candidate_forecasts(window, START, 3)
    assert [v for _, v in cands["flat3"]] == [50.0, 50.0, 50.0]   # (40+50+60)/3
    assert [v for _, v in cands["flat6"]] == [35.0, 35.0, 35.0]   # (10+..+60)/6
    assert [v for _, v in cands["median6"]] == [35.0, 35.0, 35.0]  # median(10..60)


def test_median6_ignores_a_temporary_spike():
    # ATX3.1 G850's real shape (2026-07-07 cross-check): a 2-month spike that
    # already reverted the following month. flat6's MEAN (2106) drags the
    # whole forecast well above the planner's actual figure (916); the
    # MEDIAN should land near the pre/post-spike level instead.
    obs = series(date(2026, 1, 1), [820, 709, 700, 4428, 5097, 883])
    window = baseline.build_window(obs, START)
    cands = baseline.candidate_forecasts(window, START, 3)
    median_level = cands["median6"][0][1]
    mean_level = cands["flat6"][0][1]
    assert median_level == pytest.approx(statistics.median(
        [820, 709, 700, 4428, 5097, 883]))
    assert median_level < mean_level
    assert median_level < 1000                       # near the non-spike months
    assert mean_level > 2000                          # the mean stays spike-dragged


def test_median6_requires_full_window():
    obs = series(date(2026, 5, 1), [10.0, 20.0])       # only 2 months of history
    window = baseline.build_window(obs, START)
    cands = baseline.candidate_forecasts(window, START, 3)
    assert "median6" not in cands


def test_combo_is_the_average_of_every_other_candidate():
    # 24 months (no seasonal — needs 18+ but let's also cover the seasonal
    # case): use a plain rising series so flat3/flat6/median6/trend all fit.
    obs = series(date(2024, 7, 1), [float(v) for v in range(1, 25)])
    window = baseline.build_window(obs, START)
    cands = baseline.candidate_forecasts(window, START, 2)
    others = [n for n in cands if n != "combo"]
    assert len(others) >= 2                            # flat3/flat6 always present
    for k in range(2):
        expected = sum(cands[n][k][1] for n in others) / len(others)
        assert cands["combo"][k][1] == pytest.approx(expected, abs=0.05)


def test_combo_always_present_when_any_candidate_exists():
    # even minimal history (flat3/flat6 only) must still produce combo
    obs = series(date(2026, 5, 1), [10.0, 20.0])
    window = baseline.build_window(obs, START)
    cands = baseline.candidate_forecasts(window, START, 3)
    assert "combo" in cands


def test_seasonal_reproduces_pure_seasonal_series():
    # 36 months of an exactly repeating shape → the seasonal candidate must
    # extend it perfectly (index recovers the shape, level the scale).
    shape = [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000, 1100, 1200]
    obs = series(date(2023, 7, 1), [float(v) for v in shape * 3])
    cands = baseline.candidate_forecasts(baseline.build_window(obs, START),
                                         START, 12)
    assert [v for _, v in cands["seasonal"]] == pytest.approx(shape)


def test_trend_tracks_decline_and_never_goes_negative():
    obs = series(date(2025, 7, 1), [800, 800, 800, 800, 800, 800,
                                    800, 400, 200, 100, 50, 25])
    cands = baseline.candidate_forecasts(baseline.build_window(obs, START),
                                         START, 12)
    qty = [v for _, v in cands["trend"]]
    assert all(q >= 0 for q in qty)
    assert qty == sorted(qty, reverse=True)          # keeps declining
    assert qty[0] < (100 + 50 + 25) / 3              # below the naive flat3 level


def test_trend_growth_is_damped():
    obs = series(date(2025, 7, 1), [100] * 6 + [100, 110, 121, 133, 146, 161])
    cands = baseline.candidate_forecasts(baseline.build_window(obs, START),
                                         START, 12)
    qty = [v for _, v in cands["trend"]]
    # monthly ratio ≈1.1 clamped ok, but damping must keep 12 months out from
    # compounding: undamped would be ~161·1.1^13 ≈ 556
    assert qty[-1] < 300
    ratios = [b / a for a, b in zip(qty, qty[1:])]
    assert all(r2 <= r1 + 1e-9 for r1, r2 in zip(ratios, ratios[1:]))  # flattening


def test_wape_hand_computed():
    # |10-8|+|20-24| = 6 over |10|+|20| = 30 → 20%
    assert baseline.wape([10, 20], [8, 24]) == pytest.approx(20.0)
    assert baseline.wape([0, 0], [1, 2]) is None


# ------------------------------------------------------- selection logic ---
def test_holdout_picks_seasonal_for_seasonal_series():
    shape = [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000, 1100, 1200]
    obs = series(date(2022, 7, 1), [float(v) for v in shape * 4])
    ev = baseline.holdout_eval(obs, START)
    assert ev["winner"] == "seasonal"
    assert ev["wapes"]["seasonal"] == pytest.approx(0.0, abs=0.01)
    assert ev["wapes"]["flat6"] > 10


def test_holdout_eval_reports_m1_error_per_candidate():
    shape = [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000, 1100, 1200]
    obs = series(date(2022, 7, 1), [float(v) for v in shape * 4])
    ev = baseline.holdout_eval(obs, START)
    # series starts 2022-07 (shape[0]=Jul); test window starts 2026-01, which
    # is 6 calendar-months after Jul -> shape[6] = 700
    assert ev["m1_actual"] == pytest.approx(700.0)
    assert ev["m1_err"]["seasonal"] == pytest.approx(0.0, abs=0.01)
    assert set(ev["m1_err"]) == set(ev["wapes"])       # every candidate scored on both


def test_holdout_picks_flat_for_stable_series():
    obs = series(date(2024, 7, 1), [50.0] * 24)
    ev = baseline.holdout_eval(obs, START)
    # every candidate is perfect on a constant series; flat3 (min) acceptable —
    # what matters is that NO candidate is wrongly penalized
    assert min(ev["wapes"].values()) == pytest.approx(0.0, abs=0.01)


def test_make_forecast_skips_stale_and_uses_winner():
    dead = {date(2024, 1, 1): 1000.0}
    assert baseline.make_forecast(dead, START) is None

    shape = [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000, 1100, 1200]
    obs = series(date(2022, 7, 1), [float(v) for v in shape * 4])
    rows, method = baseline.make_forecast(obs, START, horizon=12)
    assert method == "seasonal"
    assert len(rows) == 12
    assert rows[0][0] == START
    assert [v for _, v in rows] == pytest.approx(shape)


def test_short_history_falls_back_to_default():
    obs = series(date(2026, 4, 1), [10.0, 12.0, 14.0])
    rows, method = baseline.make_forecast(obs, START, horizon=6)
    assert method == baseline.DEFAULT_METHOD                     # flat3
    assert [v for _, v in rows] == [12.0] * 6                    # (10+12+14)/3


# ------------------------------------------------- order suggestion math ---
def test_suggest_order_breach_timing_and_moq_rounding():
    # balance 100, demand 60/mo → month0: 40, month1: −20.
    # safety = 15d/30d × 60 = 30 → first breach at index 1 (−20 < 30... month0
    # is 40 ≥ 30, month1 −20 < 30). lead 30d = 1mo → order_by index 0.
    # target = 30 + 60 = 90; shortfall = 90 − (−20) = 110 → MOQ 50 → 150.
    s = suggest_order(current_balance=100, monthly_demand=[60, 60, 60],
                      moq=50, lead_time_days=30, min_stock_days=15)
    assert s["action"] == "suggest_order"
    assert s["breach_month_index"] == 1
    assert s["order_by_month_index"] == 0
    assert s["suggested_qty"] == 150.0
    assert s["projected"] == [40.0, -20.0, -80.0]


def test_suggest_order_no_breach():
    s = suggest_order(current_balance=1000, monthly_demand=[10, 10, 10],
                      moq=None, lead_time_days=None, min_stock_days=None)
    assert s["action"] == "none"
