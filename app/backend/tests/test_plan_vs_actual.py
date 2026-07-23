"""forecast_views.plan_vs_actual — system-planned quantity (weekly-
disaggregated forecast, WITH any adjustment applied) vs real weekly SAP-
shipped quantity (WeeklyActual). Built 2026-07-15 per the user's request
to compare planned vs actual sales weekly, with planner tuning going
through the existing Adjustment mechanism rather than a new write path."""
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from onefcst.models import (Base, Item, ForecastRun, Forecast, WeeklyActual,
                            Adjustment, PlanValue, Snapshot)
from onefcst.forecast_views import plan_vs_actual


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _seed_forecast(session, item, baseline=400.0):
    run = ForecastRun(method_version="v1")
    session.add(run)
    session.flush()
    session.add(Forecast(run_id=run.id, item_id=item.id, month=date(2026, 6, 1),
                         baseline=baseline, method="flat3"))
    session.commit()
    return run


def test_returns_none_for_unknown_item(session):
    assert plan_vs_actual(session, 999) is None


def test_returns_none_when_no_forecast_run_exists(session):
    item = Item(canonical_name="X", kind="part")
    session.add(item)
    session.commit()
    assert plan_vs_actual(session, item.id) is None


def test_disaggregates_monthly_plan_into_weeks(session):
    item = Item(canonical_name="X", kind="part")
    session.add(item)
    session.flush()
    _seed_forecast(session, item, baseline=400.0)   # June 2026 has ~4-5 weeks

    result = plan_vs_actual(session, item.id)
    assert result["name"] == "X"
    assert len(result["weeks"]) >= 4
    total_planned = sum(w["planned_qty"] for w in result["weeks"] if w["planned_qty"])
    assert round(total_planned) == 400   # disaggregation conserves the monthly total


def test_matches_real_actual_weeks_and_computes_variance(session):
    item = Item(canonical_name="X", kind="part")
    session.add(item)
    session.flush()
    _seed_forecast(session, item, baseline=400.0)
    # first Monday-anchored week of June 2026 is 2026-06-01 (a Monday)
    session.add(WeeklyActual(item_id=item.id, week_start=date(2026, 6, 1),
                             qty=120.0, source="SAP"))
    session.commit()

    result = plan_vs_actual(session, item.id)
    first_week = next(w for w in result["weeks"] if w["week_start"] == "2026-06-01")
    assert first_week["actual_qty"] == 120.0
    assert first_week["planned_qty"] is not None
    expected_variance = round((120.0 - first_week["planned_qty"]) / first_week["planned_qty"] * 100, 1)
    assert first_week["variance_pct"] == expected_variance
    expected_variance_qty = round(120.0 - first_week["planned_qty"], 1)
    assert first_week["variance_qty"] == expected_variance_qty


def test_actual_week_offset_from_monday_still_matches_the_containing_forecast_week(session):
    """Real SAP weeks aren't always Monday-anchored (see
    ingestion/sap_shipments.py's raytrek/GALLERIA offset note) — an actual
    dated a few days into a forecast week must still match that week, not
    be silently dropped or mismatched."""
    item = Item(canonical_name="X", kind="part")
    session.add(item)
    session.flush()
    _seed_forecast(session, item, baseline=400.0)
    # 2026-06-02 is a Tuesday, one day into the Monday-anchored week starting 06-01
    session.add(WeeklyActual(item_id=item.id, week_start=date(2026, 6, 2),
                             qty=50.0, source="SAP"))
    session.commit()

    result = plan_vs_actual(session, item.id)
    first_week = next(w for w in result["weeks"] if w["week_start"] == "2026-06-01")
    assert first_week["actual_qty"] == 50.0


def test_zero_planned_week_gets_variance_qty_but_not_variance_pct(session):
    """variance_pct is guarded against a planned=0 week (would divide by
    zero) and correctly returns None — but variance_qty (a plain
    subtraction) has no such restriction and should still report the real
    absolute gap, since "planned 0, actual 5" is a meaningful 5-unit miss
    even though it has no sensible percentage."""
    item = Item(canonical_name="X", kind="part")
    session.add(item)
    session.flush()
    _seed_forecast(session, item, baseline=0.0)
    session.add(WeeklyActual(item_id=item.id, week_start=date(2026, 6, 1),
                             qty=5.0, source="SAP"))
    session.commit()

    result = plan_vs_actual(session, item.id)
    first_week = next(w for w in result["weeks"] if w["week_start"] == "2026-06-01")
    assert first_week["planned_qty"] == 0.0
    assert first_week["actual_qty"] == 5.0
    assert first_week["variance_pct"] is None
    assert first_week["variance_qty"] == 5.0


def test_weeks_with_no_actual_data_yet_have_null_actual_and_variance(session):
    item = Item(canonical_name="X", kind="part")
    session.add(item)
    session.flush()
    _seed_forecast(session, item, baseline=400.0)
    session.commit()

    result = plan_vs_actual(session, item.id)
    assert all(w["actual_qty"] is None for w in result["weeks"])
    assert all(w["variance_pct"] is None for w in result["weeks"])
    assert all(w["variance_qty"] is None for w in result["weeks"])


def test_reflects_an_existing_adjustment_not_the_raw_baseline(session):
    """The whole point of this view: a planner's tune must be visible here
    immediately, since the tune goes through the existing Adjustment
    mechanism, not a separate write path on this view."""
    item = Item(canonical_name="X", kind="part")
    session.add(item)
    session.flush()
    _seed_forecast(session, item, baseline=400.0)
    session.add(Adjustment(item_id=item.id, month=date(2026, 6, 1),
                           old_value=400.0, new_value=800.0, scope="sku",
                           reason="promo expected", author="alice"))
    session.commit()

    result = plan_vs_actual(session, item.id, history_months=0)
    total_planned = sum(w["planned_qty"] for w in result["weeks"] if w["planned_qty"])
    assert round(total_planned) == 800   # adjusted value, not the 400 baseline
    assert all(w["has_adjustment"] for w in result["weeks"])


def test_weeks_without_an_adjustment_report_has_adjustment_false(session):
    item = Item(canonical_name="X", kind="part")
    session.add(item)
    session.flush()
    _seed_forecast(session, item, baseline=400.0)
    session.commit()

    result = plan_vs_actual(session, item.id)
    assert all(w["has_adjustment"] is False for w in result["weeks"])


# --------------------------------------------------------------- history ---

def _seed_plan_value(session, item, month, value, level="master"):
    snap = Snapshot(source_file="test.xlsx")
    session.add(snap)
    session.flush()
    session.add(PlanValue(snapshot_id=snap.id, item_id=item.id, month=month,
                          level=level, value=value))


def test_includes_one_trailing_month_of_history_by_default(session):
    item = Item(canonical_name="X", kind="part")
    session.add(item)
    session.flush()
    _seed_forecast(session, item, baseline=400.0)   # forecast starts June 2026
    _seed_plan_value(session, item, date(2026, 5, 1), 300.0)   # May's real ONE plan
    session.commit()

    result = plan_vs_actual(session, item.id)   # default history_months=1
    may_weeks = [w for w in result["weeks"] if w["month"] == "2026-05-01"]
    assert may_weeks, "expected May history weeks in the output"
    assert all(w["is_history"] for w in may_weeks)
    total_may_planned = sum(w["planned_qty"] for w in may_weeks if w["planned_qty"])
    assert round(total_may_planned) == 300


def test_history_weeks_are_never_tunable(session):
    item = Item(canonical_name="X", kind="part")
    session.add(item)
    session.flush()
    _seed_forecast(session, item, baseline=400.0)
    _seed_plan_value(session, item, date(2026, 5, 1), 300.0)
    session.commit()

    result = plan_vs_actual(session, item.id)
    history_weeks = [w for w in result["weeks"] if w["is_history"]]
    assert history_weeks
    assert all(w["has_adjustment"] is False for w in history_weeks)


def test_forecast_weeks_are_not_marked_as_history(session):
    item = Item(canonical_name="X", kind="part")
    session.add(item)
    session.flush()
    _seed_forecast(session, item, baseline=400.0)
    session.commit()

    result = plan_vs_actual(session, item.id, history_months=0)
    assert all(w["is_history"] is False for w in result["weeks"])


def test_history_months_zero_disables_history_entirely(session):
    item = Item(canonical_name="X", kind="part")
    session.add(item)
    session.flush()
    _seed_forecast(session, item, baseline=400.0)
    _seed_plan_value(session, item, date(2026, 5, 1), 300.0)
    session.commit()

    result = plan_vs_actual(session, item.id, history_months=0)
    assert not any(w["month"] == "2026-05-01" for w in result["weeks"])


def test_real_actual_older_than_the_history_window_is_still_shown_not_dropped(session):
    """Regression: a real SAP shipment week that predates even the
    trailing history window (or the whole forecast, if history_months=0)
    must not be silently dropped — that was the original bug the user's
    ask surfaced. It's shown keyed to its own Monday-anchored week with
    planned_qty=None rather than merged into an unrelated planned week."""
    item = Item(canonical_name="X", kind="part")
    session.add(item)
    session.flush()
    _seed_forecast(session, item, baseline=400.0)   # forecast starts June 2026
    # 2026-03-02 predates even the 1-month (May) history window
    session.add(WeeklyActual(item_id=item.id, week_start=date(2026, 3, 2),
                             qty=77.0, source="SAP"))
    session.commit()

    result = plan_vs_actual(session, item.id)
    old_week = next((w for w in result["weeks"] if w["actual_qty"] == 77.0), None)
    assert old_week is not None, "old actual week was silently dropped"
    assert old_week["planned_qty"] is None
    assert old_week["is_history"] is True


def test_history_plan_prefers_master_over_one_row_same_as_item_detail(session):
    item = Item(canonical_name="X", kind="part")
    session.add(item)
    session.flush()
    _seed_forecast(session, item, baseline=400.0)
    _seed_plan_value(session, item, date(2026, 5, 1), 999.0, level="one_row")
    _seed_plan_value(session, item, date(2026, 5, 1), 300.0, level="master")
    session.commit()

    result = plan_vs_actual(session, item.id)
    total_may = sum(w["planned_qty"] for w in result["weeks"]
                    if w["month"] == "2026-05-01" and w["planned_qty"])
    assert round(total_may) == 300   # master wins, not the one_row 999
