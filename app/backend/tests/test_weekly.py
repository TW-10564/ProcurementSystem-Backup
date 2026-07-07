"""Unit tests for monthly->weekly forecast disaggregation — pure math,
no DB. The core invariant tested throughout: quantity is always conserved
(no units invented or dropped at month/week boundaries)."""
from datetime import date

import pytest

from onefcst.engine.weekly import (disaggregate_forecast,
                                   disaggregate_month_to_weeks,
                                   week_starts_covering)


def test_week_starts_are_mondays_and_cover_the_whole_month():
    # July 2026: 1st is a Wednesday
    weeks = week_starts_covering(date(2026, 7, 1))
    assert all(w.weekday() == 0 for w in weeks)          # all Mondays
    assert weeks[0] <= date(2026, 7, 1)
    assert weeks[-1] + __import__("datetime").timedelta(days=6) >= date(2026, 7, 31)


def test_disaggregate_conserves_total_quantity():
    rows = disaggregate_month_to_weeks(date(2026, 7, 1), 1000.0)
    assert sum(r["qty"] for r in rows) == pytest.approx(1000.0, abs=0.1)
    assert sum(r["weight"] for r in rows) == pytest.approx(1.0, abs=1e-6)


def test_boundary_week_gets_correct_day_share():
    # July 2026 starts Wednesday (2026-07-01, weekday()==2). Its first
    # overlapping week starts Monday 2026-06-29: Mon/Tue fall in June,
    # Wed-Sun (5 days) fall in July.
    rows = disaggregate_month_to_weeks(date(2026, 7, 1), 3100.0)
    first = next(r for r in rows if r["week_start"] == date(2026, 6, 29))
    assert first["days_in_month"] == 5
    assert first["qty"] == pytest.approx(3100.0 * 5 / 31, abs=0.1)


def test_zero_quantity_produces_zero_weeks_not_errors():
    rows = disaggregate_month_to_weeks(date(2026, 7, 1), 0.0)
    assert all(r["qty"] == 0.0 for r in rows)


def test_disaggregate_forecast_merges_shared_boundary_weeks():
    # a week spanning June/July should get contributions from BOTH months,
    # summed into a single entry, not two separate/duplicated rows
    forecast = [(date(2026, 6, 1), 3000.0), (date(2026, 7, 1), 3100.0)]
    weekly = disaggregate_forecast(forecast)
    week_starts = [w["week_start"] for w in weekly]
    assert len(week_starts) == len(set(week_starts))     # no duplicate weeks
    total = sum(w["qty"] for w in weekly)
    assert total == pytest.approx(6100.0, abs=0.5)        # both months fully conserved
