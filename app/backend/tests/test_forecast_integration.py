"""Integration tests against the real app DB and live API code paths.

These verify the numbers on the screen, end to end: DB rows are consistent
with the run summary, and what the API serves for a sample item equals an
independent recomputation from the raw stored history. Skipped automatically
when the DB has not been built (fresh checkout)."""
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from onefcst.config import DB_PATH
from onefcst.db import get_session
from onefcst.engine import baseline
from onefcst.forecasting import _series_by_item, latest_run
from onefcst.models import Forecast

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="onefcst.db not built")


@pytest.fixture(scope="module")
def session():
    s = get_session()
    yield s
    s.close()


@pytest.fixture(scope="module")
def client():
    from onefcst.api.main import app
    return TestClient(app)


def test_run_is_internally_consistent(session):
    run = latest_run(session)
    assert run is not None, "no forecast run — run: python3 -m onefcst.forecasting"
    note = json.loads(run.note)
    rows = session.query(Forecast).filter(Forecast.run_id == run.id).all()

    # row count = items × horizon, across ALL sources (SKU engine + both
    # tier2 stages), every qty non-negative, every method named
    tier2 = note["tier2"]
    total_items = (note["items_forecasted"] + tier2["items_forecasted"]
                  + tier2["category_fallback"]["items_forecasted"])
    assert len(rows) == total_items * note["horizon"]
    assert all((r.baseline or 0) >= 0 for r in rows)
    assert all(r.method in ("seasonal", "trend", "flat3", "flat6", "median6", "combo",
                            "tier2_attach_rate", "tier2_category_average")
               for r in rows)

    # every item's months are exactly start..start+horizon−1
    start = date.fromisoformat(note["start_month"])
    expected = set(baseline.month_range(start, note["horizon"]))
    by_item = {}
    for r in rows:
        m = date.fromisoformat(r.month) if isinstance(r.month, str) else r.month
        by_item.setdefault(r.item_id, set()).add(m)
    assert all(months == expected for months in by_item.values())

    # SKU-engine method counts in the summary match what was actually
    # stored FOR THOSE METHODS — note["methods"] only tracks the SKU
    # engine's own breakdown, not the separate tier2 stages
    stored = {}
    for r in rows:
        stored[r.method] = stored.get(r.method, 0) + 1
    sku_stored = {k: v for k, v in stored.items() if k in note["methods"]}
    assert {k: v // note["horizon"] for k, v in sku_stored.items()} == note["methods"]


def test_stored_forecast_equals_independent_recomputation(session):
    """The accuracy claim, verified: recompute the forecast for the 5 largest
    items straight from raw plan_values with the pure engine and require
    exact agreement with what the API will serve."""
    run = latest_run(session)
    note = json.loads(run.note)
    start = date.fromisoformat(note["start_month"])
    series = _series_by_item(session)

    rows = session.query(Forecast).filter(Forecast.run_id == run.id).all()
    by_item = {}
    for r in rows:
        by_item.setdefault(r.item_id, []).append(r)
    largest = sorted(by_item, key=lambda i: -sum(r.baseline or 0
                                                 for r in by_item[i]))[:5]
    for item_id in largest:
        expected, method = baseline.make_forecast(series[item_id], start,
                                                  note["horizon"])
        stored = sorted(by_item[item_id],
                        key=lambda r: str(r.month))
        assert stored[0].method == method
        assert [r.baseline for r in stored] == [v for _, v in expected]


def test_api_serves_the_stored_run(client, session):
    summary = client.get("/forecasts/summary").json()
    run = latest_run(session)
    assert summary["run"]["run_id"] == run.id
    assert summary["categories"], "summary has no categories"

    lst = client.get("/forecasts", params={"limit": 5}).json()
    note = json.loads(run.note)
    tier2 = note["tier2"]
    total_items = (note["items_forecasted"] + tier2["items_forecasted"]
                  + tier2["category_fallback"]["items_forecasted"])
    assert lst["total"] == total_items
    assert len(lst["months"]) == note["horizon"]

    top = lst["rows"][0]
    assert top["next12_total"] == round(sum(top["qty"][:12]))

    detail = client.get(f"/forecasts/{top['item_id']}").json()
    assert [f["qty"] for f in detail["forecast"]] == top["qty"]
    assert detail["item"]["name"] == top["name"]
    # history the chart shows is strictly before the forecast start
    if detail["history"]:
        assert max(h["month"] for h in detail["history"]) < detail["forecast"][0]["month"]

    assert client.get("/forecasts/99999999").status_code == 404


def test_export_forecasts_produces_workbook(client):
    r = client.get("/export/forecasts")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/vnd.openxml")
    assert len(r.content) > 10000


def test_near_term_metric_is_present_and_no_worse_than_overall(session):
    """The M+1 pooled metric should exist on every run since the v3 engine
    and, since it's a strictly easier (shorter-horizon) test, should not be
    a worse number than the 6-month blended WAPE."""
    run = latest_run(session)
    note = json.loads(run.note)
    near = note["holdout"]["near_term"]
    overall = note["holdout"]["overall"]
    assert near is not None and near["wape"] is not None
    assert near["wape"] <= overall["wape"] + 1e-6


def test_planner_snapshots_archived_on_run(session):
    from onefcst.models import PlannerSnapshot
    run = latest_run(session)
    note = json.loads(run.note)
    assert note["planner_snapshots_archived"] > 0
    start = date.fromisoformat(note["start_month"])
    n = (session.query(PlannerSnapshot)
        .filter(PlannerSnapshot.snapshot_date == start).count())
    assert n == note["planner_snapshots_archived"]


def test_adjustment_survives_full_regeneration(session):
    """The core promise of the adjustment-overlay feature: a decision
    recorded in one week's meeting must still apply after the NEXT week's
    /forecasts/run creates a brand-new ForecastRun with fresh baselines.
    Proves it for real by actually regenerating, not just inspecting code."""
    from onefcst.advisory.adjustments import record_adjustment
    from onefcst.forecasting import generate
    from onefcst import forecast_views

    run = latest_run(session)
    some_forecast = session.query(Forecast).filter(Forecast.run_id == run.id).first()
    item_id, target_month = some_forecast.item_id, some_forecast.month
    target_month = (date.fromisoformat(target_month) if isinstance(target_month, str)
                    else target_month)

    SENTINEL = 913_370.0                             # unmistakable test value
    adj = record_adjustment(session, item_id=item_id, month=target_month, old_value=None,
                            new_value=SENTINEL, scope="sku", author="test",
                            reason="regeneration-survival test")
    try:
        new_run_summary = generate(session)          # simulates next week's recalculation
        assert new_run_summary["run_id"] != run.id   # genuinely a new run, not a no-op

        detail = forecast_views.item_detail(session, item_id)
        matching = [f for f in detail["forecast"] if f["month"] == target_month.isoformat()]
        assert matching, "target month missing from the newly regenerated forecast"
        assert matching[0]["qty"] == SENTINEL
        assert matching[0]["adjustment"] is not None
        assert matching[0]["baseline_qty"] != SENTINEL   # raw statistical output, untouched
    finally:
        # this is a synthetic sentinel, not a real recorded decision — don't
        # let it linger in the live DB where a real user could see it
        from onefcst.models import Adjustment, AuditLog
        session.query(AuditLog).filter(AuditLog.what == f"adjustment#{adj.id} recorded (sku)").delete()
        session.query(Adjustment).filter(Adjustment.id == adj.id).delete()
        session.commit()


def test_planner_accuracy_endpoint_has_honest_empty_state(client):
    r = client.get("/reports/planner-accuracy")
    assert r.status_code == 200
    body = r.json()
    assert "gradeable" in body
    if body["gradeable"] == 0:
        assert body.get("note")                       # explains why, doesn't just say null
    # the documented historical reference is always attached, regardless of
    # whether live snapshot data is gradeable yet
    assert body["historical_reference"]["category_level"]["our_equivalent"]["wape"] == 10.95
