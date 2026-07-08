"""Regression tests for FIX_PLAN M3 — blank-あだ名 SAP rows (direct or
crosswalk-recovered) that differ from the stored ONE alias only by
width/spacing/hyphenation must still resolve, instead of resolve()'s exact
match leaving them stranded."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from onefcst.models import Base, Item, ItemAlias
from onefcst.registry.build import (_norm_for_crosswalk, build_normalized_alias_index,
                                    resolve_normalized, resolve, resolve_any)


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def test_norm_converts_fullwidth_and_strips_separators():
    assert _norm_for_crosswalk("Ｂ４５０Ｍ") == "B450M"
    assert _norm_for_crosswalk("B450-M Pro") == "B450MPRO"
    assert _norm_for_crosswalk("b550m_pro") == "B550MPRO"
    assert _norm_for_crosswalk(None) == ""


def test_resolve_normalized_recovers_a_width_and_spacing_mismatch(session):
    item = Item(canonical_name="B450M Pro", kind="part")
    session.add(item)
    session.flush()
    session.add(ItemAlias(item_id=item.id, alias="B450M Pro", source_system="ONE綽名"))
    session.commit()

    # exact match fails — SAP's fullwidth form differs from the stored alias
    assert resolve(session, "Ｂ４５０Ｍ Ｐｒｏ") is None

    index = build_normalized_alias_index(session)
    resolved = resolve_normalized(index, "Ｂ４５０Ｍ Ｐｒｏ")
    assert resolved is not None
    assert resolved.id == item.id


def test_resolve_normalized_returns_none_for_no_match(session):
    index = build_normalized_alias_index(session)
    assert resolve_normalized(index, "no such alias") is None


def test_resolve_any_is_the_standard_exact_then_normalized_path(session):
    """resolve_any is now the single shared resolver every ingestion path
    (master, ④, ①, sub-files, SAP) calls — a cross-system case difference
    like 'Ry53400G' vs the stored 'ry53400G' must resolve through it
    regardless of which source happens to hit it first."""
    item = Item(canonical_name="ry53400G", kind="part")
    session.add(item)
    session.flush()
    session.add(ItemAlias(item_id=item.id, alias="ry53400G", source_system="ONE綽名"))
    session.commit()
    index = build_normalized_alias_index(session)

    assert resolve(session, "ry53400G") is not None       # exact still works
    assert resolve(session, "Ry53400G") is None            # exact alone fails on case
    resolved = resolve_any(session, index, "Ry53400G")
    assert resolved is not None and resolved.id == item.id


def test_resolve_any_returns_none_when_nothing_matches_either_way(session):
    index = build_normalized_alias_index(session)
    assert resolve_any(session, index, "no such alias anywhere") is None
