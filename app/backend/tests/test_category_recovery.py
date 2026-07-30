"""Regression tests for the bunrui category-recovery fix in registry/build.py.

Before this fix, ~1,397 real items that only ever appear in 部品マスター
(never in 部品予測) got category=NULL forever — not a data gap, an
ingestion bug: 部品マスター's own "bunrui" field (a numeric category code)
was parsed but never used. Fixed 2026-07-07 by majority-voting each code's
meaning from items that DO have a real category (from 部品予測) and share
that code."""
import pytest

from realdata import HAS_BUILT_DB, SKIP_REASON

from onefcst.config import TIER1_CATEGORIES
from onefcst.db import get_session
from onefcst.models import Item

pytestmark = pytest.mark.skipif(not HAS_BUILT_DB, reason=SKIP_REASON)


@pytest.fixture(scope="module")
def session():
    s = get_session()
    yield s
    s.close()


def test_most_previously_blank_items_now_have_a_category(session):
    # exact figures from the 2026-07-07 rebuild: 1397 -> 573 blank
    still_blank = (session.query(Item)
                  .filter(Item.category.is_(None)).count())
    assert still_blank < 1000, \
        "category recovery should have fixed a majority of the ~1397 blank items"


def test_known_recovered_items_match_expected_categories(session):
    # spot-checked against real 部品マスター bunrui codes 2026-07-07
    expected = {
        "91008": "ｿﾌﾄｳｪｱN",
        "86033": "ファンN",
        "CA-SATA": "PC DIYｻﾌﾟﾗｲ",
        "D1TS": "HDD(内蔵)",
        "FUSB3Panel": "ｻﾌﾟﾗｲ用品N",
    }
    for name, expected_cat in expected.items():
        item = session.query(Item).filter(Item.canonical_name == name).first()
        assert item is not None, f"{name} not found"
        assert item.category == expected_cat


def test_recovered_tier1_category_gets_tier1_not_hardcoded_tier2(session):
    # bunrui code 79 -> PCｹｰｽ, a REAL Tier-1 category — any recovered item
    # in it must be tier=1, not the old hardcoded tier=2
    tier1_recovered = (session.query(Item)
                      .filter(Item.category.in_(TIER1_CATEGORIES)).all())
    assert all(i.tier == 1 for i in tier1_recovered)


def test_no_item_has_a_tier1_category_with_tier2_value(session):
    # a general invariant, not just for recovered items
    mismatched = (session.query(Item)
                 .filter(Item.category.in_(TIER1_CATEGORIES), Item.tier != 1).count())
    assert mismatched == 0
