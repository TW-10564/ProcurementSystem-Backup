"""Unit tests for the adjustment-overlay logic — pure functions, no DB.
Fixture data mirrors the real 2026-07-07 procurement-meeting adjustments
(X870E's August override) that motivated this feature."""
from datetime import date, datetime
from types import SimpleNamespace

from onefcst.forecast_views import _apply_adjustments


def _adj(item_id, month, new_value, reason="test", author="tester",
        created_at=None, scope="sku"):
    return SimpleNamespace(item_id=item_id, month=month, new_value=new_value,
                           reason=reason, author=author,
                           created_at=created_at or datetime(2026, 7, 7), scope=scope)


MONTHS = [date(2026, 7, 1), date(2026, 8, 1), date(2026, 9, 1), date(2026, 10, 1)]


def test_no_adjustments_returns_baseline_unchanged():
    baseline = [100.0, 200.0, 300.0, 400.0]
    final, detail = _apply_adjustments(1, MONTHS, baseline, {})
    assert final == baseline
    assert detail == {}


def test_single_month_override_leaves_other_months_untouched():
    baseline = [154.5, 154.5, 154.5, 154.5]
    adjustments = {(569, date(2026, 8, 1)): _adj(569, date(2026, 8, 1), 78.0,
                                                  reason="X3D phase-out")}
    final, detail = _apply_adjustments(569, MONTHS, baseline, adjustments)
    assert final == [154.5, 78.0, 154.5, 154.5]
    assert set(detail) == {1}                        # only August (index 1)
    assert detail[1] == {"baseline": 154.5, "adjusted": 78.0,
                         "reason": "X3D phase-out", "author": "tester",
                         "created_at": "2026-07-07T00:00:00"}


def test_adjustment_for_a_different_item_does_not_leak():
    baseline = [100.0, 100.0]
    adjustments = {(999, date(2026, 7, 1)): _adj(999, date(2026, 7, 1), 5.0)}
    final, detail = _apply_adjustments(1, [date(2026, 7, 1), date(2026, 8, 1)],
                                       baseline, adjustments)
    assert final == baseline
    assert detail == {}


def test_multiple_months_can_each_carry_their_own_override():
    baseline = [100.0, 100.0, 100.0]
    adjustments = {
        (1, date(2026, 7, 1)): _adj(1, date(2026, 7, 1), 10.0),
        (1, date(2026, 9, 1)): _adj(1, date(2026, 9, 1), 30.0),
    }
    months = [date(2026, 7, 1), date(2026, 8, 1), date(2026, 9, 1)]
    final, detail = _apply_adjustments(1, months, baseline, adjustments)
    assert final == [10.0, 100.0, 30.0]
    assert set(detail) == {0, 2}
