"""Employee display names (advisory/profiles.py + the /me and /users routes).

The property under test throughout: the database keys people by EMAIL and only
ever *displays* the name, so renaming someone in Azure changes every historical
record's rendered author without touching a single stored key.

The security-shaped tests are the ones worth keeping honest — a profile is
written only for the caller's own verified identity, because the alternative
is any signed-in user being able to rewrite how a colleague's past changes are
attributed, which would quietly undo taking the author from the token at all.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from onefcst import config
from onefcst.advisory import profiles as profiles_svc
from onefcst.api import auth as auth_mod
from onefcst.api import entra
from onefcst.api.entra import Identity
from onefcst.api.main import app
from onefcst.models import Base, UserProfile

from test_entra_auth import (CLIENT, KID, TENANT, jwks, keypair,  # noqa: F401
                             make_token)


@pytest.fixture()
def session():
    # StaticPool + check_same_thread=False so the SAME in-memory database is
    # visible from TestClient's worker thread. Without both, the HTTP tests
    # below either see an empty schema (a fresh :memory: db per connection) or
    # raise "SQLite objects created in a thread can only be used in that same
    # thread". Harmless for the plain service tests, which never leave the
    # main thread.
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def ident(email="tanaka@twave.co.jp", name="田中 太郎", oid="oid-1"):
    return Identity(subject="sub-1", oid=oid, email=email, name=name)


# ------------------------------------------------------------ touch() ------

def test_touch_creates_a_row_from_token_claims(session):
    row = profiles_svc.touch(session, ident())
    assert row.email == "tanaka@twave.co.jp"
    assert row.display_name == "田中 太郎"
    assert row.source == "token"


def test_email_is_stored_lowercased_so_lookups_match(session):
    profiles_svc.touch(session, ident(email="Tanaka@TWave.co.JP"))
    # Author strings elsewhere arrive in whatever case Entra used that day;
    # a case-sensitive key would silently create a second person.
    assert profiles_svc.get(session, "tanaka@twave.co.jp") is not None
    assert profiles_svc.get(session, "TANAKA@TWAVE.CO.JP") is not None


def test_rename_in_azure_updates_the_displayed_name(session):
    profiles_svc.touch(session, ident(name="田中 太郎"))
    # Same person (same email/oid), new display name after a marriage, a
    # transliteration fix, or an HR correction.
    profiles_svc.touch(session, ident(name="佐藤 太郎"))

    rows = session.query(UserProfile).all()
    assert len(rows) == 1, "a rename must not create a second person"
    assert rows[0].display_name == "佐藤 太郎"
    # The key every adjustment/audit row joins on is untouched, which is the
    # whole reason the rename needs no backfill.
    assert rows[0].email == "tanaka@twave.co.jp"


def test_touch_does_not_erase_graph_details(session):
    """The regression that would show up as a job title vanishing on reload.

    /me calls touch() on every page load. The ID token has no job title, so a
    naive "write everything from the token" would blank the richer Graph
    fields each time.
    """
    profiles_svc.touch(session, ident())
    profiles_svc.apply_graph(session, ident(), {
        "display_name": "田中 太郎", "job_title": "購買課長",
        "department": "購買本部",
    })

    profiles_svc.touch(session, ident())

    row = profiles_svc.get(session, "tanaka@twave.co.jp")
    assert row.job_title == "購買課長"
    assert row.department == "購買本部"


def test_touch_without_an_email_writes_nothing(session):
    """A row with no join key could never be read back, so don't create one."""
    assert profiles_svc.touch(session, ident(email="")) is None
    assert session.query(UserProfile).count() == 0


def test_last_seen_is_not_rewritten_on_every_call(session):
    """touch() runs per page load; it must not be a write per page load."""
    row = profiles_svc.touch(session, ident())
    row.last_seen_at = datetime.utcnow() - timedelta(minutes=5)
    session.commit()
    before = row.last_seen_at

    profiles_svc.touch(session, ident())          # nothing changed, still fresh
    assert profiles_svc.get(session, "tanaka@twave.co.jp").last_seen_at == before

    row.last_seen_at = datetime.utcnow() - timedelta(hours=3)
    session.commit()
    profiles_svc.touch(session, ident())          # stale — now it updates
    assert profiles_svc.get(session, "tanaka@twave.co.jp").last_seen_at > before


# -------------------------------------------------------- apply_graph() ----

def test_graph_details_are_stored_and_marked_as_such(session):
    profiles_svc.apply_graph(session, ident(), {
        "display_name": "田中 太郎", "given_name": "太郎", "surname": "田中",
        "job_title": "購買課長", "department": "購買本部",
        "office_location": "東京本社",
    })
    row = profiles_svc.get(session, "tanaka@twave.co.jp")
    assert (row.job_title, row.department, row.office_location) == (
        "購買課長", "購買本部", "東京本社")
    # "graph" vs "token" is what the profile page uses to explain an empty
    # job title as "consent not granted" rather than "not set in Azure".
    assert row.source == "graph"


def test_graph_payload_cannot_choose_whose_profile_it_writes(session):
    """The security property: the email comes from the token, never the body.

    Without this, any signed-in user could POST a payload naming a colleague
    and rewrite how that colleague's past changes are displayed.
    """
    profiles_svc.apply_graph(session, ident(email="tanaka@twave.co.jp"), {
        "email": "boss@twave.co.jp",              # ignored — not a Graph field
        "display_name": "なりすまし",
    })
    assert profiles_svc.get(session, "boss@twave.co.jp") is None
    assert profiles_svc.get(session, "tanaka@twave.co.jp").display_name == "なりすまし"


def test_empty_graph_fields_do_not_erase_known_values(session):
    """Graph returns null for a job title nobody has filled in."""
    profiles_svc.touch(session, ident(name="田中 太郎"))
    profiles_svc.apply_graph(session, ident(), {
        "display_name": None, "job_title": None, "department": "購買本部",
    })
    row = profiles_svc.get(session, "tanaka@twave.co.jp")
    assert row.display_name == "田中 太郎"
    assert row.department == "購買本部"


def test_directory_lists_signed_in_users_without_leaking_internals(session):
    profiles_svc.touch(session, ident())
    profiles_svc.touch(session, ident(email="yamada@twave.co.jp",
                                      name="山田 花子", oid="oid-2"))
    users = profiles_svc.directory(session)
    assert {u["email"] for u in users} == {"tanaka@twave.co.jp", "yamada@twave.co.jp"}
    # oid and last_seen help nobody in the UI and needn't be handed out.
    assert all("oid" not in u and "last_seen_at" not in u for u in users)


# ------------------------------------------------------------- HTTP --------

@pytest.fixture
def client(isolated_api_db):
    """TestClient whose writes land in a throwaway database (see conftest).

    Without it these tests write into the developer's REAL
    app/data/onefcst.db, because TestClient runs the app's own `db()`
    dependency. `/me` upserts a profile, so the suite quietly added a
    fictional 田中 太郎 to the dev database — found 2026-07-31 while clearing
    test data. A test must never leave a person in the audit trail.
    """
    return TestClient(app)


@pytest.fixture
def sso_on(monkeypatch, jwks):                                # noqa: F811
    monkeypatch.setattr(config, "ENTRA_TENANT_ID", TENANT)
    monkeypatch.setattr(config, "ENTRA_CLIENT_ID", CLIENT)
    monkeypatch.setattr(config, "ENTRA_AUDIENCE", CLIENT)
    monkeypatch.setattr(config, "ENTRA_ALLOWED_GROUPS", [])
    monkeypatch.setattr(config, "ENTRA_ALLOW_API_KEY", True)
    monkeypatch.setattr(auth_mod, "API_KEY", "")
    entra.reset_signing_keys(entra._test_signing_keys(jwks))
    yield
    entra.reset_signing_keys(None)


def bearer(keypair, **kw):                                    # noqa: F811
    return {"Authorization": f"Bearer {make_token(keypair, **kw)}"}


def test_me_reports_the_profile_alongside_the_claims(client, sso_on, keypair):  # noqa: F811
    r = client.get("/me", headers=bearer(keypair))
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "entra"
    # audit_author stays the EMAIL — the name is display only.
    assert body["audit_author"] == "tanaka@twave.co.jp"
    assert "profile" in body


def test_profile_update_requires_a_real_token(client, sso_on):
    """With only the shared key there is no verified identity to attach to,
    and guessing would be worse than refusing."""
    r = client.put("/me/profile", json={"job_title": "購買課長"})
    assert r.status_code == 401


def test_profile_update_rejects_oversized_fields(client, sso_on, keypair):  # noqa: F811
    """Client-supplied input gets bounds — the lesson from audit finding F8,
    where POST /maintenance took an unvalidated dict."""
    r = client.put("/me/profile", headers=bearer(keypair),
                   json={"job_title": "x" * 5000})
    assert r.status_code == 422
