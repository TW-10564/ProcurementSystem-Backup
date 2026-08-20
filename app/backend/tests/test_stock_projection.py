"""Unit tests for stock projection / order suggestion — pure math, no DB."""
from datetime import date

from onefcst.engine.stock_projection import prorate_current_month, project_stock, suggest_order


def test_project_stock_treats_none_demand_as_zero():
    out = project_stock(100.0, [10.0, None, 20.0])
    assert out == [90.0, 90.0, 70.0]


def test_suggest_order_does_not_crash_on_none_in_monthly_demand():
    """FIX_PLAN H3 — the average-demand calculation lacked the same
    `d or 0.0` guard project_stock uses one line away, so a None entry
    crashed sum()/len() instead of being treated as zero."""
    result = suggest_order(current_balance=50.0, monthly_demand=[None, 30.0, 30.0],
                           moq=10, lead_time_days=30, min_stock_days=30)
    assert result["action"] in ("none", "suggest_order")   # must not raise


def test_suggest_order_average_matches_explicit_zero_substitution():
    with_none = suggest_order(current_balance=5.0, monthly_demand=[None, 20.0, 20.0],
                              moq=None, lead_time_days=0, min_stock_days=30)
    with_zero = suggest_order(current_balance=5.0, monthly_demand=[0.0, 20.0, 20.0],
                              moq=None, lead_time_days=0, min_stock_days=30)
    assert with_none["action"] == with_zero["action"] == "suggest_order"
    assert with_none["safety_stock"] == with_zero["safety_stock"]


def test_moq_rounds_suggested_qty_up_to_a_multiple():
    """moq=30 on a shortfall that isn't already a multiple of 30 must round
    UP (never down — an order short of the real need defeats the point).
    These inputs give a shortfall of exactly 100 — deliberately NOT a
    multiple of the moq, so moq_applied must come back True. (Was moq=25,
    which divides 100 evenly, so the test contradicted its own docstring
    and failed against correct production math — fixed 2026-07-24.)"""
    no_moq = suggest_order(current_balance=100.0, monthly_demand=[100.0] * 3,
                           moq=None, lead_time_days=0, min_stock_days=0)
    with_moq = suggest_order(current_balance=100.0, monthly_demand=[100.0] * 3,
                             moq=30, lead_time_days=0, min_stock_days=0)
    assert with_moq["action"] == "suggest_order"
    assert with_moq["moq_applied"] is True
    assert with_moq["suggested_qty"] % 30 == 0
    assert with_moq["suggested_qty"] >= no_moq["suggested_qty"]


def test_moq_not_flagged_when_shortfall_already_a_multiple():
    """shortfall=100 with moq=25 needs no rounding — moq_applied stays False."""
    r = suggest_order(current_balance=100.0, monthly_demand=[100.0] * 3,
                      moq=25, lead_time_days=0, min_stock_days=0)
    assert r["action"] == "suggest_order"
    assert r["moq_applied"] is False
    assert r["suggested_qty"] == 100.0


def test_lead_time_shifts_order_by_month_earlier_than_breach():
    """order_by_month_index must land BEFORE breach_month_index by roughly
    lead_time_days worth of months, so the order arrives before the
    breach — not merely get placed on the breach date."""
    no_lead = suggest_order(current_balance=100.0, monthly_demand=[50.0] * 6,
                            moq=None, lead_time_days=0, min_stock_days=0)
    with_lead = suggest_order(current_balance=100.0, monthly_demand=[50.0] * 6,
                              moq=None, lead_time_days=60, min_stock_days=0)
    assert no_lead["action"] == with_lead["action"] == "suggest_order"
    assert no_lead["order_by_month_index"] == no_lead["breach_month_index"]
    assert with_lead["breach_month_index"] == no_lead["breach_month_index"]
    assert with_lead["order_by_month_index"] < with_lead["breach_month_index"]


def test_min_stock_days_raises_safety_stock_and_breaches_earlier():
    """min_stock_days>0 must produce a nonzero safety_stock buffer and
    therefore breach at the same month or earlier than min_stock_days=0 —
    otherwise the safety-stock buffer math (stock_projection.py:50) is
    silently a no-op."""
    no_buffer = suggest_order(current_balance=200.0, monthly_demand=[50.0] * 6,
                              moq=None, lead_time_days=0, min_stock_days=0)
    with_buffer = suggest_order(current_balance=200.0, monthly_demand=[50.0] * 6,
                                moq=None, lead_time_days=0, min_stock_days=60)
    assert with_buffer["safety_stock"] > 0
    assert no_buffer["safety_stock"] == 0
    assert with_buffer["action"] == "suggest_order"
    if no_buffer["action"] == "suggest_order":
        assert with_buffer["breach_month_index"] <= no_buffer["breach_month_index"]


def test_prorate_current_month_scales_down_by_days_remaining():
    """Found 2026-08-14 via UAT (W11HAD): current_balance is dated to
    whatever day the stock snapshot was taken, not the 1st of the month, so
    subtracting the FULL month's demand double-counts the days already
    elapsed (already reflected in current_balance). Snapshot on the 9th of
    a 31-day month leaves 22 days remaining -> demand scaled to 22/31."""
    demand = [3100.0, 2000.0]
    out = prorate_current_month(demand, date(2026, 8, 1), date(2026, 8, 9))
    assert out[0] == 3100.0 * 22 / 31
    assert out[1] == 2000.0   # only month 0 is touched


def test_prorate_current_month_no_change_when_snapshot_predates_month():
    """A stale snapshot from BEFORE the forecast horizon started (e.g. last
    month's SAP pull, current month's forecast not yet begun) must not be
    prorated — that demand is genuinely still entirely ahead of it."""
    demand = [1000.0, 2000.0]
    out = prorate_current_month(demand, date(2026, 8, 1), date(2026, 7, 27))
    assert out == demand


def test_prorate_current_month_no_change_without_a_snapshot_date():
    demand = [1000.0, 2000.0]
    assert prorate_current_month(demand, date(2026, 8, 1), None) == demand


def test_prorate_current_month_near_zero_when_snapshot_is_last_day():
    demand = [1000.0, 2000.0]
    out = prorate_current_month(demand, date(2026, 8, 1), date(2026, 8, 31))
    assert out[0] == 0.0
