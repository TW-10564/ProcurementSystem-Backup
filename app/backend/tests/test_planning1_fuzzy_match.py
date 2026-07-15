"""Regression tests for FIX_PLAN M4 — ①販売計画 row labels miss ONE's 綽名 on
exact match due to a few documented naming-convention gaps (ASRock/RTX
vendor prefixes, GB vs G capacity suffix), not because they're unrelated."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from onefcst.models import Base, Item, ItemAlias
from onefcst.ingestion.planning1 import candidate_variants
from onefcst.registry.build import build_normalized_alias_index
from onefcst.build import _resolve_planning1


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def test_mb_vendor_prefix_is_tried_both_stripped_and_raw():
    """Revised 2026-07-14: no MB vendor reliably keeps or drops its own
    prefix in 綽名 (0/154 registered items keep "ASUS"/"GIGABYTE"; MSI keeps
    it for some boards, e.g. "MSI H610M D5", drops it for others, e.g.
    "Z890" is registered bare) — so every vendor prefix is tried BOTH ways,
    raw first (most specific / matches when the vendor DOES keep it)."""
    assert "B550M" in candidate_variants("ASR B550M")
    assert "B550M" in candidate_variants("ASRock B550M")
    variants = candidate_variants("MSI H610M D5")
    assert variants[0] == "MSI H610M D5"    # raw form tried first
    assert "H610M D5" in variants           # stripped form also offered
    variants = candidate_variants("ASUS B860M")
    assert "B860M" in variants
    variants = candidate_variants("GIGABYTE B850M")
    assert "B850M" in variants


def test_rtx_prefix_is_stripped_but_rx_is_not():
    assert "5070Ti" in candidate_variants("RTX5070Ti")
    variants = candidate_variants("RX9070XT")
    assert variants == ["RX9070XT"]         # AMD keeps "RX" in 綽名


def test_gb_suffix_normalizes_to_g():
    assert "16G" in " ".join(candidate_variants("RTX5060Ti 16GB"))


def test_variants_preserve_original_as_first_candidate():
    assert candidate_variants("B550M")[0] == "B550M"


def test_resolve_planning1_finds_item_via_asr_stripped_variant(session):
    item = Item(canonical_name="B550M", kind="part", category="ﾏｻﾞｰﾎﾞｰﾄﾞ")
    session.add(item)
    session.flush()
    session.add(ItemAlias(item_id=item.id, alias="B550M", source_system="ONE綽名"))
    session.commit()

    index = build_normalized_alias_index(session)
    resolved, via_variant = _resolve_planning1(session, index, "ASR B550M")
    assert resolved is not None and resolved.id == item.id
    assert via_variant is True


def test_resolve_planning1_finds_item_via_rtx_stripped_and_normalized_variant(session):
    item = Item(canonical_name="5060Ti 16G", kind="part", category="グラフィックカード")
    session.add(item)
    session.flush()
    session.add(ItemAlias(item_id=item.id, alias="5060Ti 16G", source_system="ONE綽名"))
    session.commit()

    index = build_normalized_alias_index(session)
    resolved, via_variant = _resolve_planning1(session, index, "RTX5060Ti 16GB")
    assert resolved is not None and resolved.id == item.id
    assert via_variant is True


def test_resolve_planning1_returns_none_for_a_structurally_unmatched_case_code(session):
    """Case/chassis color-code rows have no 綽名 counterpart at all — a
    structural gap M4 explicitly does not claim to solve."""
    index = build_normalized_alias_index(session)
    resolved, via_variant = _resolve_planning1(session, index, "CruX　WH")
    assert resolved is None
    assert via_variant is False
