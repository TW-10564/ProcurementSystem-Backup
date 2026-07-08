"""Unit tests for stock projection / order suggestion — pure math, no DB."""
from onefcst.engine.stock_projection import project_stock, suggest_order


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
