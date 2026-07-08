"""Integration tests for tier2_forecasting.generate_tier2 against the real
app DB.

Two regressions this file guards, in tension with each other:
  1. (fixed 2026-07-07) an earlier version forecasted 306 items before
     anyone noticed EVERY ONE was stale (no activity in the last 6 months)
     — an old attach rate would have projected demand for discontinued
     parts. Fixed by gating on recent activity.
  2. (fixed 2026-07-08, FIX_PLAN M2) that same gate (baseline.is_stale) only
     checks TRAILING PAST months before `start`. 部品予測 is a
     forward-looking plan, and Tier-2 accessory parts have their nonzero
     values concentrated in FUTURE months relative to `start` — so it
     wrongly flagged nearly all 626 real candidates as stale, producing
     items_forecasted=0. generate_tier2 now uses its own
     _has_current_activity, which treats activity dated AT OR AFTER `start`
     as current (it's live plan data, not history) while still catching an
     item whose activity, past OR future, is entirely outside the window."""
from datetime import date

import pytest

from onefcst.config import DB_PATH, TIER1_CATEGORIES
from onefcst.db import get_session
from onefcst.engine import baseline
from onefcst.forecasting import _series_by_item
from onefcst.models import Item
from onefcst import tier2_forecasting

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="onefcst.db not built")


@pytest.fixture(scope="module")
def session():
    s = get_session()
    yield s
    s.close()


def test_no_tier2_forecast_lacks_current_activity(session):
    """For every REAL item this run actually gave a tier2_attach_rate
    forecast, its own history must show activity at/after the recency
    cutoff (past OR future) — regression #1 above, restated for the fixed
    bidirectional check."""
    from onefcst.forecasting import latest_run
    from onefcst.models import Forecast

    run = latest_run(session)
    assert run is not None
    series = _series_by_item(session)

    tier2_rows = (session.query(Forecast)
                 .filter(Forecast.run_id == run.id,
                         Forecast.method == "tier2_attach_rate").all())
    start = date.fromisoformat(
        __import__("json").loads(run.note)["start_month"])
    for row in tier2_rows:
        obs = series.get(row.item_id)
        assert obs is not None
        assert tier2_forecasting._has_current_activity(obs, start), \
            f"item {row.item_id} got a tier2 forecast despite no current activity"


def test_generate_tier2_actually_forecasts_items(session):
    """Regression #2 (FIX_PLAN M2): before the fix, items_forecasted was 0
    for every real run because is_stale's past-only check rejected nearly
    every forward-dated Tier-2 candidate."""
    from onefcst.forecasting import latest_run
    import json

    run = latest_run(session)
    note = json.loads(run.note)
    assert note["tier2"]["items_forecasted"] > 0


def test_generate_tier2_reports_honest_counts(session):
    """The run's own summary must account for every non-Tier-1,
    not-already-forecasted candidate as either forecasted, stale-skipped,
    or no-attach-rate-skipped — no silent drops."""
    from onefcst.forecasting import latest_run
    import json

    run = latest_run(session)
    note = json.loads(run.note)
    t2 = note["tier2"]
    assert set(t2) >= {"items_forecasted", "items_skipped_stale",
                      "items_skipped_no_attach_rate", "holdout"}

    series = _series_by_item(session)
    items = {i.id: i for i in session.query(Item).all()}
    start = date.fromisoformat(note["start_month"])
    already = {iid for iid, obs in series.items()
              if baseline.make_forecast(obs, start, note["horizon"]) is not None}
    candidates = [iid for iid, obs in series.items()
                 if iid not in already
                 and items.get(iid) is not None
                 and items[iid].category not in TIER1_CATEGORIES
                 and len(obs) >= tier2_forecasting.MIN_HISTORY_MONTHS]
    total_accounted = (t2["items_forecasted"] + t2["items_skipped_stale"]
                      + t2["items_skipped_no_attach_rate"])
    assert total_accounted == len(candidates)


def test_forward_dated_item_is_no_longer_wrongly_treated_as_stale(session):
    """Direct proof of the FIX_PLAN M2 fix: a synthetic item whose ONLY
    nonzero history is in FUTURE months (relative to `start`) — the exact
    shape of the ~626 wrongly-rejected real candidates — must now pass the
    recency gate, whereas the old baseline.is_stale (past-only) rejects it."""
    start = date(2026, 7, 1)
    future_only_obs = {baseline.add_months(start, i): 50.0 for i in range(6)}

    assert baseline.is_stale(future_only_obs, start) is True     # the old, buggy gate
    assert tier2_forecasting._has_current_activity(future_only_obs, start) is True


def test_stale_synthetic_item_is_excluded_even_with_a_perfect_attach_rate(session):
    """Direct proof the original protection still holds: construct a
    synthetic series with a decade of perfect attach-rate history that
    stops 2 years ago (unambiguously stale, no future activity either) and
    confirm generate_tier2's own filtering logic would still skip it —
    without needing a live run, so this doesn't depend on what's in the DB
    today."""
    from types import SimpleNamespace

    items = {i.id: i for i in session.query(Item).all()}
    tier1_item_id = next(iid for iid, it in items.items()
                        if it.category in TIER1_CATEGORIES)
    stale_item_id = 999_999_001   # synthetic, won't collide with real ids

    def months(start, n):
        return [baseline.add_months(start, i) for i in range(n)]

    old_start = date(2015, 1, 1)
    stale_obs = {m: 100.0 for m in months(old_start, 24)}   # ended 2016-12, long stale
    series = {tier1_item_id: {m: 1000.0 for m in months(old_start, 24)},
             stale_item_id: stale_obs}
    items[stale_item_id] = SimpleNamespace(id=stale_item_id, category="ｻﾌﾟﾗｲ用品N")

    start = date(2026, 7, 1)
    assert not tier2_forecasting._has_current_activity(stale_obs, start)   # sanity
    assert stale_item_id not in {
        iid for iid, obs in series.items()
        if items[iid].category not in TIER1_CATEGORIES
        and len(obs) >= tier2_forecasting.MIN_HISTORY_MONTHS
        and tier2_forecasting._has_current_activity(obs, start)
    }
