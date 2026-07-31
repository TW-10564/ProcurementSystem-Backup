"""Role-based access control (api/permissions.py).

The rules, as agreed 2026-07-31:
  * admin       — everything, on every item.
  * purchasing  — adjustments/order actions ONLY on the sub-files they own.
  * planning    — only the four Tier-1 categories 企画部 plans.
  * no role     — reads everything, writes nothing.

Reads are never scoped, on purpose: everyone can see every change anyone made.

The tests that matter most are the negative ones. A permission system is only
worth having if it says NO in the cases it is meant to, and the expensive
mistakes here are all "fails open" — an unowned part treated as unowned-by-
everyone rather than owned-by-nobody, a missing role treated as permissive, a
UI check trusted in place of a server check.
"""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from onefcst import config
from onefcst.api import auth as auth_mod
from onefcst.api import entra
from onefcst.api import permissions as perms
from onefcst.api.entra import Identity
from onefcst.api.main import app
from onefcst.models import (Base, Item, ItemScopeOverride, PlanValue, Snapshot,
                            UserAccess, UserScope)

from test_entra_auth import (CLIENT, KID, TENANT, jwks, keypair,  # noqa: F401
                             make_token)


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _sso_configured(monkeypatch):
    """Access control is opt-in with SSO (see role_for). conftest blanks the
    Entra settings for every test, so without this the whole module would be
    testing the permissive no-SSO path and pass while proving nothing."""
    monkeypatch.setattr(config, "ENTRA_TENANT_ID", TENANT)
    monkeypatch.setattr(config, "ENTRA_CLIENT_ID", CLIENT)
    monkeypatch.setattr(config, "BOOTSTRAP_ADMINS", [])


def ident(email="buyer@twave.co.jp", name="購買 太郎", oid="oid-1"):
    return Identity(subject="sub", oid=oid, email=email, name=name)


def grant(session, email, role, scopes=()):
    session.add(UserAccess(email=email, role=role))
    for key in scopes:
        session.add(UserScope(email=email, scope_key=key))
    session.commit()


def make_item(session, name, category=None, tier=2, subfile=None):
    """An item, optionally with the ingestion lineage that gives it an owner."""
    item = Item(canonical_name=name, category=category, tier=tier, kind="part")
    session.add(item)
    session.commit()
    if subfile:
        from onefcst.ingestion.subfile_config import FILES
        snap = Snapshot(source_file=FILES[subfile]["file"], sheet="部品詳細")
        session.add(snap)
        session.commit()
        session.add(PlanValue(snapshot_id=snap.id, item_id=item.id,
                              raw_name=name, month=date(2026, 9, 1),
                              level="subfile", value=1.0))
        session.commit()
    return item


# ------------------------------------------------------------- roles -------

def test_roles_are_not_enforced_when_sso_is_off(session, monkeypatch):
    """Pre-SSO and local dev must keep working unchanged.

    With no Entra configuration every caller is anonymous, so enforcing roles
    would lock EVERYONE out of the rebuild button instead of protecting
    anything. Turning SSO on is what turns access control on — the same opt-in
    rule auth.effective_author already follows.
    """
    monkeypatch.setattr(config, "ENTRA_TENANT_ID", "")
    monkeypatch.setattr(config, "ENTRA_CLIENT_ID", "")
    assert perms.role_for(session, None) == perms.ADMIN


def test_roles_ARE_enforced_once_sso_is_configured(session, monkeypatch):
    """The other half of the switch: with SSO on, an anonymous caller (no
    token, e.g. a direct request to :8600 bypassing nginx) gets nothing."""
    monkeypatch.setattr(config, "ENTRA_TENANT_ID", "tenant")
    monkeypatch.setattr(config, "ENTRA_CLIENT_ID", "client")
    monkeypatch.setattr(config, "BOOTSTRAP_ADMINS", [])
    assert perms.role_for(session, None) is None


def test_no_row_means_no_write_permission(session, monkeypatch):
    """The default is read-only. A signed-in stranger must not be able to write
    merely because authentication succeeded."""
    monkeypatch.setattr(config, "ENTRA_TENANT_ID", "tenant")
    monkeypatch.setattr(config, "ENTRA_CLIENT_ID", "client")
    assert perms.role_for(session, ident()) is None
    item = make_item(session, "CPU-A", "CPU", tier=1, subfile="CPU")
    assert perms.can_write_item(session, ident(), item) is False


def test_bootstrap_admin_works_without_a_database_row(session, monkeypatch):
    """The recovery path: somebody must be able to edit the roster before
    anyone is in it, and after the last admin row is deleted by accident."""
    monkeypatch.setattr(config, "BOOTSTRAP_ADMINS", ["boss@twave.co.jp"])
    assert perms.role_for(session, ident(email="boss@twave.co.jp")) == perms.ADMIN


def test_role_lookup_is_case_insensitive(session):
    """Entra is inconsistent about the case of the address it sends."""
    grant(session, "buyer@twave.co.jp", perms.PURCHASING)
    assert perms.role_for(session, ident(email="Buyer@TWave.co.JP")) == perms.PURCHASING


def test_unknown_role_string_is_not_honoured(session):
    """A typo'd or hand-edited role must fail closed, not grant something."""
    session.add(UserAccess(email="buyer@twave.co.jp", role="superuser"))
    session.commit()
    assert perms.role_for(session, ident()) is None


# ------------------------------------------------------------- admin -------

def test_admin_may_write_any_item_including_unowned(session):
    grant(session, "boss@twave.co.jp", perms.ADMIN)
    boss = ident(email="boss@twave.co.jp")
    owned = make_item(session, "MEM-A", "メモリ", subfile="MEM")
    orphan = make_item(session, "SUPPLY-A", "ｻﾌﾟﾗｲ用品N")
    assert perms.can_write_item(session, boss, owned) is True
    assert perms.can_write_item(session, boss, orphan) is True


# -------------------------------------------------------- purchasing -------

def test_purchasing_may_write_only_their_own_sub_files(session):
    grant(session, "buyer@twave.co.jp", perms.PURCHASING, ["MEM", "SSD"])
    mine = make_item(session, "MEM-A", "メモリ", subfile="MEM")
    theirs = make_item(session, "VGA-A", "グラフィックカード", tier=1, subfile="VGA")
    assert perms.can_write_item(session, ident(), mine) is True
    assert perms.can_write_item(session, ident(), theirs) is False


def test_unowned_items_are_not_open_season_for_purchasing(session):
    """The failure that would matter most in practice.

    A third of forecastable items belong to no sub-file. If "no owner" fell
    through to "anyone may edit", the least-understood part of the catalogue
    would become the least protected.
    """
    grant(session, "buyer@twave.co.jp", perms.PURCHASING, ["MEM"])
    orphan = make_item(session, "SUPPLY-A", "ｻﾌﾟﾗｲ用品N")
    assert perms.scope_of_item(session, orphan) is None
    assert perms.can_write_item(session, ident(), orphan) is False


def test_admin_override_gives_an_orphan_an_owner(session):
    """Option 3 in practice: the admin assigns the leftovers, and the buyer who
    owns that sub-file can then work on them."""
    grant(session, "buyer@twave.co.jp", perms.PURCHASING, ["MEM"])
    orphan = make_item(session, "SUPPLY-A", "ｻﾌﾟﾗｲ用品N")
    session.add(ItemScopeOverride(canonical_name="SUPPLY-A", scope_key="MEM"))
    session.commit()
    assert perms.scope_of_item(session, orphan) == "MEM"
    assert perms.can_write_item(session, ident(), orphan) is True


def test_override_wins_over_the_ingestion_lineage(session):
    """A correction has to actually stick, even when the file lineage disagrees."""
    grant(session, "buyer@twave.co.jp", perms.PURCHASING, ["SSD"])
    item = make_item(session, "MEM-A", "メモリ", subfile="MEM")
    assert perms.scope_of_item(session, item) == "MEM"
    session.add(ItemScopeOverride(canonical_name="MEM-A", scope_key="SSD"))
    session.commit()
    assert perms.scope_of_item(session, item) == "SSD"
    assert perms.can_write_item(session, ident(), item) is True


def test_scope_is_keyed_by_name_so_it_survives_a_rebuild(session):
    """Item ids are recreated by every rebuild; canonical_name is the stable
    key. An id-keyed override would silently repoint at a different part."""
    session.add(ItemScopeOverride(canonical_name="SUPPLY-A", scope_key="MEM"))
    session.commit()
    # Same name, brand-new id — exactly what build.py produces.
    reborn = make_item(session, "SUPPLY-A", "ｻﾌﾟﾗｲ用品N")
    assert perms.scope_of_item(session, reborn) == "MEM"


# ---------------------------------------------------------- planning -------

def test_planning_may_tune_only_the_four_core_categories(session):
    grant(session, "planner@twave.co.jp", perms.PLANNING)
    planner = ident(email="planner@twave.co.jp")
    for cat in ("CPU", "グラフィックカード", "ﾏｻﾞｰﾎﾞｰﾄﾞ", "PCｹｰｽ"):
        core = make_item(session, f"core-{cat}", cat, tier=1)
        assert perms.can_write_item(session, planner, core) is True, cat

    derived = make_item(session, "MEM-A", "メモリ", tier=2, subfile="MEM")
    assert perms.can_write_item(session, planner, derived) is False


def test_planning_is_not_limited_by_sub_file_ownership(session):
    """Planning's remit is a property of the item, not an assignment — they own
    all four categories, wherever those parts happen to be filed."""
    grant(session, "planner@twave.co.jp", perms.PLANNING)
    item = make_item(session, "VGA-A", "グラフィックカード", tier=1, subfile="VGA")
    assert perms.scopes_for(session, "planner@twave.co.jp") == set()
    assert perms.can_write_item(session, ident(email="planner@twave.co.jp"), item) is True


# --------------------------------------------------------------- HTTP ------

@pytest.fixture
def client(isolated_api_db):
    return TestClient(app)


@pytest.fixture
def sso_on(monkeypatch, jwks):                                # noqa: F811
    monkeypatch.setattr(config, "ENTRA_TENANT_ID", TENANT)
    monkeypatch.setattr(config, "ENTRA_CLIENT_ID", CLIENT)
    monkeypatch.setattr(config, "ENTRA_AUDIENCE", CLIENT)
    monkeypatch.setattr(config, "ENTRA_ALLOWED_GROUPS", [])
    monkeypatch.setattr(config, "ENTRA_ALLOW_API_KEY", True)
    monkeypatch.setattr(config, "BOOTSTRAP_ADMINS", [])
    monkeypatch.setattr(auth_mod, "API_KEY", "")
    entra.reset_signing_keys(entra._test_signing_keys(jwks))
    yield
    entra.reset_signing_keys(None)


def bearer(keypair, **kw):                                    # noqa: F811
    return {"Authorization": f"Bearer {make_token(keypair, **kw)}"}


def test_rebuild_is_refused_without_admin(client, sso_on, keypair):  # noqa: F811
    """Audit finding F2: this drops every table and was reachable by anyone
    who could load the page."""
    r = client.post("/build/rebuild", headers=bearer(keypair))
    assert r.status_code == 403


def test_forecast_run_is_refused_without_admin(client, sso_on, keypair):  # noqa: F811
    r = client.post("/forecasts/run", headers=bearer(keypair))
    assert r.status_code == 403


def test_maintenance_is_refused_without_admin(client, sso_on, keypair):  # noqa: F811
    r = client.post("/maintenance", headers=bearer(keypair),
                    json={"enabled": True, "message": "test"})
    assert r.status_code == 403


def test_roster_endpoints_are_admin_only(client, sso_on, keypair,  # noqa: F811
                                         isolated_api_db):
    """An endpoint that grants roles, reachable by anyone who has one, is not
    an access-control system."""
    grant(isolated_api_db, "tanaka@twave.co.jp", perms.PURCHASING, ["MEM"])
    assert client.get("/access", headers=bearer(keypair)).status_code == 403
    assert client.put("/access", headers=bearer(keypair),
                      json={"email": "x@twave.co.jp",
                            "role": "admin"}).status_code == 403


def test_admin_can_grant_and_the_grant_takes_effect(client, sso_on, keypair,  # noqa: F811
                                                    isolated_api_db):
    grant(isolated_api_db, "tanaka@twave.co.jp", perms.ADMIN)

    r = client.put("/access", headers=bearer(keypair),
                   json={"email": "Buyer@twave.co.jp", "role": "purchasing",
                         "scopes": ["MEM", "SSD"], "note": "MEM/SSD担当"})
    assert r.status_code == 200
    assert r.json()["email"] == "buyer@twave.co.jp"      # normalised

    listed = client.get("/access", headers=bearer(keypair)).json()
    row = [u for u in listed["users"] if u["email"] == "buyer@twave.co.jp"][0]
    assert row["role"] == "purchasing"
    assert row["scopes"] == ["MEM", "SSD"]


def test_granting_replaces_scopes_rather_than_merging(client, sso_on, keypair,  # noqa: F811
                                                      isolated_api_db):
    """The admin screen sends the complete list; merging would make removing a
    sub-file impossible."""
    grant(isolated_api_db, "tanaka@twave.co.jp", perms.ADMIN)
    hdrs = bearer(keypair)
    client.put("/access", headers=hdrs, json={"email": "b@twave.co.jp",
                                              "role": "purchasing",
                                              "scopes": ["MEM", "SSD"]})
    client.put("/access", headers=hdrs, json={"email": "b@twave.co.jp",
                                              "role": "purchasing",
                                              "scopes": ["MEM"]})
    assert perms.scopes_for(isolated_api_db, "b@twave.co.jp") == {"MEM"}


def test_unknown_sub_file_is_rejected_not_silently_stored(client, sso_on,  # noqa: F811
                                                          keypair, isolated_api_db):
    """A typo'd scope key would own nothing, which looks exactly like a
    permissions bug when the buyer is then refused."""
    grant(isolated_api_db, "tanaka@twave.co.jp", perms.ADMIN)
    r = client.put("/access", headers=bearer(keypair),
                   json={"email": "b@twave.co.jp", "role": "purchasing",
                         "scopes": ["MEMORY"]})
    assert r.status_code == 400
    assert "MEMORY" in r.text


def test_adjustment_is_refused_outside_the_callers_scope(client, sso_on,  # noqa: F811
                                                         keypair, isolated_api_db):
    grant(isolated_api_db, "tanaka@twave.co.jp", perms.PURCHASING, ["MEM"])
    theirs = make_item(isolated_api_db, "VGA-A", "グラフィックカード",
                       tier=1, subfile="VGA")
    r = client.post("/adjustments", headers=bearer(keypair),
                    json={"item_id": theirs.id, "month": "2026-09-01",
                          "new_value": 100, "scope": "sku",
                          "reason": "test", "author": "ignored"})
    assert r.status_code == 403
    # The message should name what to do next, not just refuse.
    assert "VGA-A" in r.text


def test_refused_adjustment_leaves_no_row_behind(client, sso_on, keypair,  # noqa: F811
                                                 isolated_api_db):
    """Permission is checked BEFORE recording: a rejected write must leave no
    trace, not a row plus a rollback."""
    from onefcst.models import Adjustment
    grant(isolated_api_db, "tanaka@twave.co.jp", perms.PURCHASING, ["MEM"])
    theirs = make_item(isolated_api_db, "VGA-A", "グラフィックカード",
                       tier=1, subfile="VGA")
    client.post("/adjustments", headers=bearer(keypair),
                json={"item_id": theirs.id, "month": "2026-09-01",
                      "new_value": 100, "scope": "sku", "reason": "x",
                      "author": "a"})
    assert isolated_api_db.query(Adjustment).count() == 0


def test_adjustment_is_allowed_inside_the_callers_scope(client, sso_on,  # noqa: F811
                                                        keypair, isolated_api_db):
    grant(isolated_api_db, "tanaka@twave.co.jp", perms.PURCHASING, ["MEM"])
    mine = make_item(isolated_api_db, "MEM-A", "メモリ", subfile="MEM")
    r = client.post("/adjustments", headers=bearer(keypair),
                    json={"item_id": mine.id, "month": "2026-09-01",
                          "new_value": 100, "scope": "sku",
                          "reason": "restock", "author": "ignored"})
    assert r.status_code == 200, r.text
    # Still attributed to the verified identity, never the supplied string.
    assert r.json()["author"] == "tanaka@twave.co.jp"


def test_me_reports_permissions_for_the_ui(client, sso_on, keypair,  # noqa: F811
                                           isolated_api_db):
    grant(isolated_api_db, "tanaka@twave.co.jp", perms.PURCHASING, ["MEM", "SSD"])
    body = client.get("/me", headers=bearer(keypair)).json()
    assert body["permissions"]["role"] == "purchasing"
    assert body["permissions"]["is_admin"] is False
    assert body["permissions"]["scopes"] == ["MEM", "SSD"]
