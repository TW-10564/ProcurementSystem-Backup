"""Writes must be attributed to the VERIFIED identity, not to what the client says.

Before SSO, `author` was whatever the browser sent — the UI kept a name in
localStorage for the user to type, and one call site defaulted to the literal
string "bala". So the audit log recorded a claim the client made about itself,
which is worth nothing precisely when it matters: a confirmed compat rule or an
order action is the record you would want to trust in a dispute.

The rule these tests pin: when a validated token is present it WINS, and the
client's value is discarded rather than merged. "Use the supplied name if it
looks filled in" would leave the same hole open — anyone could still attribute
a write to a colleague.
"""
from __future__ import annotations

import pytest

from onefcst import config
from onefcst.api import auth as auth_mod
from onefcst.api import entra
from onefcst.api.auth import effective_author

from test_entra_auth import CLIENT, TENANT, jwks, keypair, make_token  # noqa: F401


class FakeRequest:
    """Just enough Request for effective_author: it only reads request.state."""

    class _State:
        pass

    def __init__(self, identity=None):
        self.state = self._State()
        self.state.identity = identity


def an_identity(email="tanaka@twave.co.jp", oid="oid-1", name="田中 太郎"):
    return entra.Identity(subject="sub", oid=oid, email=email, name=name)


# ------------------------------------------------ identity always wins ---

def test_verified_identity_overrides_whatever_the_client_sent():
    req = FakeRequest(an_identity())
    assert effective_author(req, "somebody-else") == "tanaka@twave.co.jp"


def test_client_cannot_impersonate_a_colleague():
    """The specific abuse this closes."""
    req = FakeRequest(an_identity(email="me@twave.co.jp"))
    assert effective_author(req, "manager@twave.co.jp") == "me@twave.co.jp"


def test_identity_wins_even_when_the_client_sends_nothing():
    req = FakeRequest(an_identity())
    assert effective_author(req, None) == "tanaka@twave.co.jp"
    assert effective_author(req, "") == "tanaka@twave.co.jp"


def test_display_name_is_never_used_as_the_author():
    """A display name is neither unique nor stable; the address is."""
    req = FakeRequest(an_identity(name="田中 太郎", email="tanaka@twave.co.jp"))
    assert effective_author(req, "x") != "田中 太郎"


def test_falls_back_to_object_id_when_no_address_is_present():
    req = FakeRequest(an_identity(email="", oid="oid-42"))
    assert effective_author(req, "typed-name") == "oid-42"


# ------------------------------------------- unchanged when SSO is off ---

def test_supplied_author_is_kept_when_there_is_no_identity():
    """Local dev and the pre-SSO deployment must behave exactly as before."""
    assert effective_author(FakeRequest(None), "bala") == "bala"


def test_blank_author_without_an_identity_becomes_unknown():
    """Never an empty string in the audit log — 'unknown' is at least honest."""
    assert effective_author(FakeRequest(None), "") == "unknown"
    assert effective_author(FakeRequest(None), None) == "unknown"
    assert effective_author(FakeRequest(None), "   ") == "unknown"


# ------------------------------------------------------ through HTTP ---

@pytest.fixture
def sso_client(monkeypatch, jwks):                                # noqa: F811
    from fastapi.testclient import TestClient
    from onefcst.api.main import app
    monkeypatch.setattr(config, "ENTRA_TENANT_ID", TENANT)
    monkeypatch.setattr(config, "ENTRA_CLIENT_ID", CLIENT)
    monkeypatch.setattr(config, "ENTRA_AUDIENCE", CLIENT)
    monkeypatch.setattr(config, "ENTRA_ALLOWED_GROUPS", [])
    monkeypatch.setattr(config, "ENTRA_ALLOWED_EMAILS", [])
    monkeypatch.setattr(auth_mod, "API_KEY", "")
    entra.reset_signing_keys(entra._test_signing_keys(jwks))
    yield TestClient(app)
    entra.reset_signing_keys(None)


def test_me_reports_the_author_that_will_be_recorded(sso_client, keypair):  # noqa: F811
    """/me exposes audit_author so the UI can show who writes are attributed to
    — and so a mismatch is visible before someone confirms fifty rules."""
    token = make_token(keypair)
    body = sso_client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert body["audit_author"] == "tanaka@twave.co.jp"
    assert body["audit_author"] != body["name"]
