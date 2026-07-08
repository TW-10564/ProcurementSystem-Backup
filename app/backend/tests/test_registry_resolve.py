"""Regression test for FIX_PLAN C3 — resolve() must be deterministic when the
same alias string is claimed by items from two different source systems
(the DB's unique constraint is per (alias, source_system), so this is not
blocked at write time)."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from onefcst.models import Base, Item, ItemAlias
from onefcst.registry.build import resolve


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
