"""Unit tests for the Tier-2 attach-rate engine — pure functions, no DB.
The outlier-guard test uses the exact real numbers found 2026-07-07 (a
corrupted 部品予測 coefficient exploding CPU projections into the tens of
millions for one specific future month)."""
from datetime import date

import pytest

from onefcst.engine import tier2


def series(start, values):
    from onefcst.engine.baseline import add_months
    return {add_months(start, i): v for i, v in enumerate(values)}


def test_sanitize_drops_the_real_corrupted_month():
    # mirrors actual i913900K-style data: stable in the hundreds, then one
    # month explodes to tens of millions
    s = series(date(2026, 1, 1), [100, 120, 90, 110, 105, 95, 74_823_210, 108])
    clean = tier2.sanitize_series(s)
    assert date(2026, 7, 1) not in clean          # the exploded month is gone
    assert len(clean) == 7
    assert clean[date(2026, 8, 1)] == 108          # months around it survive


def test_sanitize_keeps_genuine_growth():
    # a real ramp shouldn't be mistaken for corruption — growth is gradual,
    # never one month at >15x the trailing median
    s = series(date(2026, 1, 1), [100, 150, 220, 340, 500, 720])
    clean = tier2.sanitize_series(s)
    assert clean == s


def test_monthly_ratios_only_uses_overlapping_nonzero_months():
    item = series(date(2026, 1, 1), [10, 20, 30])
    tier1 = {date(2026, 1, 1): 1000, date(2026, 2, 1): 0, date(2026, 4, 1): 3000}
    ratios = tier2.monthly_ratios(item, tier1)
    assert set(ratios) == {date(2026, 1, 1)}       # Feb=0 tier1, Mar missing from tier1
    assert ratios[date(2026, 1, 1)] == 0.01


def test_compute_attach_rate_is_median_not_mean_and_needs_minimum_overlap():
    item = series(date(2026, 1, 1), [10, 10, 10, 1000])   # one wild outlier month
    tier1 = series(date(2026, 1, 1), [1000, 1000, 1000, 1000])
    rate = tier2.compute_attach_rate(item, tier1)
    assert rate == 0.01                                   # median ignores the outlier

    short_item = {date(2026, 1, 1): 10, date(2026, 2, 1): 10}   # only 2 months
    assert tier2.compute_attach_rate(short_item, tier1) is None


def test_make_tier2_forecast_applies_rate_to_tier1_forecast():
    item = series(date(2025, 1, 1), [10] * 12)
    tier1 = series(date(2025, 1, 1), [1000] * 12)
    tier1_forecast = {date(2026, 1, 1): 2000, date(2026, 2, 1): 4000}
    rows = tier2.make_tier2_forecast(item, tier1, tier1_forecast)
    assert dict(rows) == {date(2026, 1, 1): 20.0, date(2026, 2, 1): 40.0}


def test_holdout_eval_isolates_attach_rate_error_from_tier1_forecast_error():
    item = series(date(2024, 1, 1), [10] * 30)
    tier1 = series(date(2024, 1, 1), [1000] * 30)
    ev = tier2.holdout_eval(item, tier1, date(2026, 7, 1))
    assert ev is not None
    assert ev["attach_rate"] == 0.01
    assert ev["wape"] == 0.0                              # perfect ratio, perfect fit


def test_category_typical_rate_needs_minimum_peers():
    assert tier2.category_typical_rate([0.01, 0.02]) is None          # only 2 peers
    assert tier2.category_typical_rate([0.01, 0.02, 0.015]) == 0.015  # 3 is enough, median


def test_category_typical_rate_is_robust_to_one_mild_outlier_peer():
    # modest spread (CV well under the threshold) — median should still
    # land on the middle value, unmoved by one somewhat-higher peer
    rates = [0.01, 0.012, 0.011, 0.009, 0.02]
    assert tier2.category_typical_rate(rates) == 0.011


def test_category_typical_rate_refuses_when_peers_are_too_inconsistent():
    # the REAL case found 2026-07-07: category "ｿﾌﾄｳｪｱS" mixed Windows/
    # Office licenses with an unrelated bundled item, and peer rates spanned
    # an ~840x range (CV=3.43) — a "typical rate" there isn't a real signal,
    # it just LOOKS like one. Must refuse, not silently average through it.
    rates = [0.01, 0.012, 0.011, 0.009, 5.0]     # one peer 500x the others
    assert tier2.category_typical_rate(rates) is None


def test_category_typical_rate_cv_boundary():
    # sanity check the threshold itself is being applied, not just always
    # triggered by presence of any spread at all
    consistent = [0.01, 0.011, 0.0105, 0.0115]    # low CV, real category
    assert tier2.category_typical_rate(consistent) is not None


def test_leave_one_out_wape_scores_how_well_peers_predict_a_real_member():
    # this member's own true rate is 0.01; its peers' typical rate is close
    # but not identical (0.011) — leave_one_out_wape should reflect exactly
    # that gap, not the member's own (unused) rate
    tier1 = series(date(2025, 1, 1), [1000] * 12)
    member_obs = series(date(2025, 1, 1), [10] * 12)        # true rate 0.01
    peer_rates = [0.011, 0.012, 0.010]                       # median 0.011
    w = tier2.leave_one_out_wape(member_obs, peer_rates, tier1)
    assert w == pytest.approx(10.0, abs=0.1)                 # |0.011-0.01|/0.01 = 10%


def test_leave_one_out_wape_none_without_enough_peers():
    tier1 = series(date(2025, 1, 1), [1000] * 12)
    member_obs = series(date(2025, 1, 1), [10] * 12)
    assert tier2.leave_one_out_wape(member_obs, [0.01, 0.02], tier1) is None
