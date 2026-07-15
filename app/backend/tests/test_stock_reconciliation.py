"""SAP is the authoritative source of physical inventory; ONE gets updated
FROM SAP, not the other way around (confirmed by the user 2026-07-15).
Covers: trusted_current_balance()'s priority/clamp rule (previously
untested, despite feeding every order-suggestion path), the negative-
ONE-balance order-quantity-inflation bug it fixes, and the new
stock_reconciliation report."""
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from onefcst.models import Base, Item, ForecastRun, Forecast, StockState
from onefcst.forecast_views import trusted_current_balance, order_suggestions
from onefcst.verify.stock_reconciliation import compute_stock_reconciliation


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


# ---------------------------------------------------------- trusted_current_balance

def test_sap_wins_when_both_present():
    stock = StockState(sap_qty=100.0, one_balance=9999.0)
    balance, confidence = trusted_current_balance(stock)
    assert balance == 100.0
    assert confidence == "sap_verified"


def test_sap_wins_even_when_negative_a_real_backorder_is_reported_honestly():
    stock = StockState(sap_qty=-50.0, one_balance=200.0)
    balance, confidence = trusted_current_balance(stock)
    assert balance == -50.0
    assert confidence == "sap_verified"


def test_falls_back_to_one_balance_when_no_sap_row():
    stock = StockState(sap_qty=None, one_balance=42.0)
    balance, confidence = trusted_current_balance(stock)
    assert balance == 42.0
    assert confidence == "one_only_unverified"


def test_negative_one_balance_is_clamped_to_zero_not_trusted_literally():
    stock = StockState(sap_qty=None, one_balance=-537.3)
    balance, confidence = trusted_current_balance(stock)
    assert balance == 0.0
    assert confidence == "one_only_negative_clamped"


def test_no_data_at_all():
    balance, confidence = trusted_current_balance(StockState(sap_qty=None, one_balance=None))
    assert balance is None
    assert confidence == "no_data"
    balance, confidence = trusted_current_balance(None)
    assert balance is None
    assert confidence == "no_data"


# --------------------------------------------- negative ONE balance no longer inflates orders

def test_negative_one_only_balance_does_not_inflate_suggested_order_qty(session):
    """Real bug found live 2026-07-15: item M2NM1TG4M had one_balance=-537.3
    with no SAP coverage. Before the fix, suggest_order's shortfall math
    (target_level - projected[breach]) inherited that negative starting
    point directly, inflating the suggested order by ~537 units beyond
    actual need. After the fix, the negative balance is clamped to 0 and
    the suggestion reflects only real demand vs a zero starting point."""
    starved = Item(canonical_name="Zero Stock (SAP-verified)", category="CPU", kind="part")
    fake_negative = Item(canonical_name="Fake Negative (ONE-only)", category="CPU", kind="part")
    session.add_all([starved, fake_negative])
    session.flush()
    session.add(StockState(item_id=starved.id, sap_qty=0.0))
    session.add(StockState(item_id=fake_negative.id, sap_qty=None, one_balance=-537.3))
    run = ForecastRun(method_version="v1")
    session.add(run)
    session.flush()
    for i in range(3):
        session.add(Forecast(run_id=run.id, item_id=starved.id,
                             month=date(2026, 7 + i, 1), baseline=100.0, method="flat3"))
        session.add(Forecast(run_id=run.id, item_id=fake_negative.id,
                             month=date(2026, 7 + i, 1), baseline=100.0, method="flat3"))
    session.commit()

    rows = {r["name"]: r for r in order_suggestions(session)["rows"]}
    # both start a real shortfall from the same effective (clamped) balance,
    # so their suggested order quantities must now match — the -537.3
    # phantom balance is not silently adding ~537 extra units on top.
    assert rows["Zero Stock (SAP-verified)"]["order_qty"] == rows["Fake Negative (ONE-only)"]["order_qty"]


# ---------------------------------------------------------- compute_stock_reconciliation

def test_flags_large_divergence_between_sap_and_one(session):
    item = Item(canonical_name="Divergent Part", category="MB", tier=1, kind="part")
    session.add(item)
    session.flush()
    session.add(StockState(item_id=item.id, sap_qty=100.0, one_balance=1000.0,
                           snapshot_date=date(2026, 7, 1)))
    session.commit()

    report = compute_stock_reconciliation(session)
    assert len(report["divergences"]) == 1
    d = report["divergences"][0]
    assert d["name"] == "Divergent Part"
    assert d["trusted_value"] == 100.0  # SAP wins regardless of divergence size
    assert d["pct_diff"] > 30.0


def test_small_divergence_is_not_flagged(session):
    item = Item(canonical_name="Close Enough", category="MB", kind="part")
    session.add(item)
    session.flush()
    session.add(StockState(item_id=item.id, sap_qty=100.0, one_balance=105.0))
    session.commit()

    report = compute_stock_reconciliation(session)
    assert report["divergences"] == []


def test_no_sap_coverage_items_are_listed_with_tier1_first(session):
    tier1 = Item(canonical_name="Tier1 No SAP", category="CPU", tier=1, kind="part")
    tier2 = Item(canonical_name="Tier2 No SAP", category="Mouse&KB", tier=None, kind="part")
    session.add_all([tier1, tier2])
    session.flush()
    session.add(StockState(item_id=tier2.id, sap_qty=None, one_balance=10.0))
    session.add(StockState(item_id=tier1.id, sap_qty=None, one_balance=5.0))
    session.commit()

    report = compute_stock_reconciliation(session)
    names = [r["name"] for r in report["no_sap_coverage"]]
    assert names == ["Tier1 No SAP", "Tier2 No SAP"]


def test_negative_one_balance_items_are_called_out_separately(session):
    item = Item(canonical_name="Phantom Negative", category="CPU", kind="part")
    session.add(item)
    session.flush()
    session.add(StockState(item_id=item.id, sap_qty=None, one_balance=-12.0))
    session.commit()

    report = compute_stock_reconciliation(session)
    assert len(report["negative_one_balance_clamped"]) == 1
    assert report["negative_one_balance_clamped"][0]["name"] == "Phantom Negative"
    assert report["negative_one_balance_clamped"][0]["trusted_value"] == 0.0


def test_items_with_only_sap_are_not_flagged_as_missing_coverage(session):
    item = Item(canonical_name="SAP Only", category="CPU", kind="part")
    session.add(item)
    session.flush()
    session.add(StockState(item_id=item.id, sap_qty=10.0, one_balance=None))
    session.commit()

    report = compute_stock_reconciliation(session)
    assert report["no_sap_coverage"] == []
    assert report["sap_covered_items"] == 1
