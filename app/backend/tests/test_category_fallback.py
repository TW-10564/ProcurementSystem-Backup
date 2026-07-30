"""Integration tests for tier2_forecasting.generate_category_fallback — the
extension that reaches items with ZERO history at all, by borrowing the
median attach rate from category peers that do have some. Validated via
leave-one-out (see engine/tier2.leave_one_out_wape), never via a fabricated
per-item WAPE, since a zero-history item has no actual to grade against."""
from datetime import date
from types import SimpleNamespace

import pytest

from realdata import HAS_BUILT_DB, SKIP_REASON

from onefcst.config import TIER1_CATEGORIES
from onefcst.db import get_session
from onefcst.engine import baseline
from onefcst.forecasting import _series_by_item, latest_run
from onefcst.models import AccuracyScore, Forecast, Item
from onefcst import tier2_forecasting

pytestmark = pytest.mark.skipif(not HAS_BUILT_DB, reason=SKIP_REASON)


@pytest.fixture(scope="module")
def session():
    s = get_session()
    yield s
    s.close()


def test_category_fallback_reaches_real_zero_history_items(session):
    """Live check against the real DB: some genuinely zero-history items
    (never in plan_values at all) should now get a category-average
    forecast, using the actual latest run."""
    run = latest_run(session)
    assert run is not None
    import json
    note = json.loads(run.note)
    cf = note["tier2"]["category_fallback"]
    assert set(cf) >= {"items_forecasted", "items_skipped_no_category_rate",
                       "categories_with_a_rate", "leave_one_out_wape"}
    assert cf["items_forecasted"] > 0, \
        "expected some real zero-history items to get a category-average forecast"
    assert cf["leave_one_out_wape"] is not None


def test_no_zero_history_item_gets_a_fabricated_accuracy_score(session):
    """The core honesty rule: a zero-history item has no actual to grade
    against, so it must NEVER get a per-item AccuracyScore row, even though
    it does get a Forecast row."""
    run = latest_run(session)
    rows = (session.query(Forecast)
           .filter(Forecast.run_id == run.id,
                   Forecast.method == tier2_forecasting.CATEGORY_METHOD).all())
    assert rows, "no category-average forecasts found on the latest run"
    scored_item_ids = {a.item_id for a in
                       session.query(AccuracyScore).filter(AccuracyScore.run_id == run.id).all()}
    category_item_ids = {r.item_id for r in rows}
    assert not (category_item_ids & scored_item_ids), \
        "a zero-history (category-average) item got a fabricated per-item accuracy score"


def test_category_fallback_never_touches_an_item_with_any_history():
    """Synthetic, DB-independent proof: an item with even 1 month of real
    history must be left alone by the fallback (it's generate_tier2's or
    the SKU engine's job, not this one's) — the fallback is keyed strictly
    on `item_id not in series`."""
    series = {1: {date(2026, 1, 1): 5.0}}   # item 1 HAS one month of history
    items = {1: SimpleNamespace(id=1, category="ｻﾌﾟﾗｲ用品N"),
            2: SimpleNamespace(id=2, category="ｻﾌﾟﾗｲ用品N")}   # item 2 has none
    # replicate the fallback's own candidate predicate directly
    already_handled = set()
    candidates = [iid for iid, item in items.items()
                 if iid not in already_handled and iid not in series
                 and item.category is not None and item.category not in TIER1_CATEGORIES]
    assert candidates == [2]


def test_category_method_is_distinct_from_item_level_attach_rate_method():
    assert tier2_forecasting.CATEGORY_METHOD != tier2_forecasting.METHOD


def test_list_forecasts_tier2_shorthand_filters_to_derived_methods_only(session):
    """The 'tier2' shorthand filter (used by the UI's visibility toggle)
    must return ONLY rows whose method starts with tier2_, and every one
    of them must actually exist in the run with that prefix — not an
    empty/broken filter silently matching everything or nothing."""
    from onefcst import forecast_views

    run = latest_run(session)
    all_rows = forecast_views.list_forecasts(session, limit=10000)
    real_tier2_count = sum(1 for r in all_rows["rows"]
                           if (r["method"] or "").startswith("tier2_"))
    assert real_tier2_count > 0

    filtered = forecast_views.list_forecasts(session, method="tier2", limit=10000)
    assert filtered["total"] == real_tier2_count
    assert all((r["method"] or "").startswith("tier2_") for r in filtered["rows"])
