"""Regression test for FIX_PLAN C3 — resolve() must be deterministic when the
same alias string is claimed by items from two different source systems
(the DB's unique constraint is per (alias, source_system), so this is not
blocked at write time)."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from onefcst.models import Base, Item, ItemAlias
from onefcst.registry.build import resolve, resolve_any, build_normalized_alias_index


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def test_resolve_is_deterministic_across_repeated_calls_on_a_cross_system_collision(session):
    first = Item(canonical_name="First item", kind="part")
    second = Item(canonical_name="Second item", kind="part")
    session.add_all([first, second])
    session.flush()
    # same alias string, claimed by two different items from two different
    # source systems — allowed by the DB (unique constraint is per
    # (alias, source_system)), and exactly the case that used to resolve
    # arbitrarily depending on DB storage order.
    session.add(ItemAlias(item_id=first.id, alias="AMBIGUOUS", source_system="ONE綽名"))
    session.add(ItemAlias(item_id=second.id, alias="AMBIGUOUS", source_system="商品コード"))
    session.commit()

    results = {resolve(session, "AMBIGUOUS").id for _ in range(10)}
    assert len(results) == 1                     # never flips between calls
    assert results == {first.id}                 # first-recorded alias wins


def test_resolve_returns_none_for_unknown_alias(session):
    assert resolve(session, "does-not-exist") is None


def _seed_variant_collision(session):
    """Reproduces the real 2026-07-21 finding: a variant SKU (e.g. a
    Samsung-specific memory module) and its base part share the same
    numeric code under DIFFERENT source systems (汎用コード vs 商品コード).
    The variant's 汎用コード alias was inserted first (lower id), so plain
    resolve() picks it — silently wrong for a caller that knows this code
    is a SYOCODE/商品コード, like SAP stock ingestion."""
    variant = Item(canonical_name="LD4-16G32Sam", kind="part")
    base = Item(canonical_name="LD4-16G32", kind="part")
    session.add_all([variant, base])
    session.flush()
    session.add(ItemAlias(item_id=variant.id, alias="169933", source_system="汎用コード"))
    session.add(ItemAlias(item_id=base.id, alias="169933", source_system="商品コード"))
    session.commit()
    return variant, base


def test_prefer_source_overrides_the_default_tie_break_when_it_has_a_match(session):
    variant, base = _seed_variant_collision(session)
    assert resolve(session, "169933").id == variant.id          # old behavior, unchanged
    assert resolve(session, "169933", prefer_source="商品コード").id == base.id


def test_prefer_source_falls_back_to_the_tie_break_when_the_preferred_source_has_no_match(session):
    variant, _base = _seed_variant_collision(session)
    # no ONE綽名 alias exists for this code at all — must not return None,
    # falls through to the same result plain resolve() would give
    assert resolve(session, "169933", prefer_source="ONE綽名").id == variant.id


def test_resolve_any_passes_prefer_source_through(session):
    variant, base = _seed_variant_collision(session)
    norm_index = build_normalized_alias_index(session)
    assert resolve_any(session, norm_index, "169933").id == variant.id
    assert resolve_any(session, norm_index, "169933", prefer_source="商品コード").id == base.id
