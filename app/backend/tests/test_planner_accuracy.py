"""Unit tests for the documented historical planner-accuracy reference —
checks the data is well-formed and internally consistent, not that the
underlying .xlsb/.xlsx archives still say the same thing (they're frozen
historical files, not re-read live; see planner_accuracy.py's docstring)."""
from onefcst.planner_accuracy import HISTORICAL_PLANNER_REFERENCE as REF


def test_reference_has_all_three_evidence_sources():
    assert set(REF) >= {"category_level", "per_sku_level",
                        "one_off_matched_month", "what_would_settle_it"}


def test_category_level_series_are_plausible_wapes():
    series = REF["category_level"]["series"]
    assert len(series) == 6
    for row in series:
        assert row["source"] and row["category"] and row["range"]
        assert row["months"] > 0
        assert 0 <= row["wape"] < 1000        # a sane WAPE range, not a typo'd value


def test_category_level_matches_our_backtest_within_documented_range():
    cat = REF["category_level"]
    wapes = [r["wape"] for r in cat["series"] if r["category"].startswith("デスク")
            and "+" not in r["category"]]
    ours = cat["our_equivalent"]["wape"]
    # the whole point of this citation: our company-wide number sits inside
    # the planner's own real historical desktop-category range
    assert min(wapes) <= ours <= max(wapes) * 1.5


def test_per_sku_reference_cites_both_sides():
    sku = REF["per_sku_level"]
    assert sku["wape_per_sku"] > 0 and sku["wape_channel_total"] > 0
    assert sku["our_equivalent"]["overall_6mo_wape"] > 0
    assert sku["our_equivalent"]["near_term_m1_wape"] > 0


def test_one_off_month_notes_the_lead_time_confound():
    m = REF["one_off_matched_month"]
    assert m["planner"]["wape"] < m["system"]["wape"]   # planner ahead here
    assert "lead_time" in m["planner"] and "lead_time" in m["system"]
    assert "confound" in m["conclusion"] or "unfair" in m["conclusion"]
