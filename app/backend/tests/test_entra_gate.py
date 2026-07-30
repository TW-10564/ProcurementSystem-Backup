"""The HTTP gate with Entra SSO switched on (api/auth.py:require_identity).

Separate from test_entra_auth.py, which tests the cryptography. This tests the
*policy*: which requests get through, what the 401 looks like, which endpoints
stay public, and — the one that matters most for the audit trail — that a
validated identity reaches the endpoint rather than being verified and dropped.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from onefcst import config
from onefcst.api import auth as auth_mod
from onefcst.api import entra
from onefcst.api.main import app

from test_entra_auth import (CLIENT, KID, TENANT, jwks, keypair,  # noqa: F401
                             make_token)


@pytest.fixture
def client():
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


# ------------------------------------------------------------ gate closed ---

def test_no_credential_is_rejected(client, sso_on):
    r = client.get("/items")
    assert r.status_code == 401
    # A 401 without this header leaves clients guessing at the scheme.
    assert "bearer" in r.headers.get("WWW-Authenticate", "").lower()


def test_forged_token_is_rejected(client, sso_on, keypair):    # noqa: F811
    from cryptography.hazmat.primitives.asymmetric import rsa
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    r = client.get("/items", headers=bearer(keypair, key=attacker))
    assert r.status_code == 401


def test_401_body_does_not_leak_why(client, sso_on, keypair):  # noqa: F811
    """Expired and forged must be indistinguishable to the caller."""
    from datetime import timedelta
    expired = client.get("/items", headers=bearer(keypair, exp_delta=timedelta(hours=-2)))
    wrong_aud = client.get("/items", headers=bearer(keypair, aud="another-app"))
    assert expired.status_code == wrong_aud.status_code == 401
    assert expired.json() == wrong_aud.json()


def test_malformed_authorization_header_is_rejected(client, sso_on):
    for value in ("", "Bearer", "Bearer ", "Basic abc", "token abc", "Bearer ..."):
        r = client.get("/items", headers={"Authorization": value})
        assert r.status_code == 401, value


# -------------------------------------------------------------- gate open ---

def test_valid_token_gets_through(client, sso_on, keypair):    # noqa: F811
    r = client.get("/items", headers=bearer(keypair))
    assert r.status_code == 200


def test_identity_reaches_the_endpoint(client, sso_on, keypair):  # noqa: F811
    """/me proves the verified identity is available for attribution.

    If this regresses to authenticated-but-anonymous, every adjustment and
    compat-rule confirmation gets logged against a self-asserted `author`.
    """
    r = client.get("/me", headers=bearer(keypair))
    assert r.status_code == 200
    body = r.json()
    assert body["authenticated"] is True
    assert body["source"] == "entra"
    assert body["email"] == "tanaka@twave.co.jp"
    assert body["audit_author"] == "tanaka@twave.co.jp"


def test_group_allowlist_is_enforced_at_the_gate(client, sso_on, keypair, monkeypatch):
    monkeypatch.setattr(config, "ENTRA_ALLOWED_GROUPS", ["g-purchasing"])
    outside = client.get("/items", headers=bearer(keypair, extra={"groups": ["g-sales"]}))
    inside = client.get("/items", headers=bearer(keypair, extra={"groups": ["g-purchasing"]}))
    assert outside.status_code == 401
    assert inside.status_code == 200


# ----------------------------------------------------------- public paths ---

def test_health_and_auth_config_stay_public(client, sso_on):
    assert client.get("/health").status_code == 200
    assert client.get("/auth/config").status_code == 200


def test_maintenance_read_stays_public_so_outages_can_be_explained(client, sso_on):
    assert client.get("/maintenance").status_code == 200


def test_auth_config_never_exposes_a_secret(client, sso_on, monkeypatch):
    monkeypatch.setattr(auth_mod, "API_KEY", "super-secret-key")
    monkeypatch.setattr(config, "API_KEY", "super-secret-key")
    body = client.get("/auth/config").text
    assert "super-secret-key" not in body
    assert client.get("/auth/config").json()["api_key_required"] is True


# ------------------------------------------------------- the API-key path ---

def test_api_key_still_accepted_for_scripted_callers(client, sso_on, monkeypatch):
    monkeypatch.setattr(auth_mod, "API_KEY", "secret123")
    monkeypatch.setattr(config, "ENTRA_ALLOW_API_KEY", True)
    r = client.get("/items", headers={"X-API-Key": "secret123"})
    assert r.status_code == 200


def test_api_key_can_be_switched_off_entirely(client, sso_on, monkeypatch):
    monkeypatch.setattr(auth_mod, "API_KEY", "secret123")
    monkeypatch.setattr(config, "ENTRA_ALLOW_API_KEY", False)
    r = client.get("/items", headers={"X-API-Key": "secret123"})
    assert r.status_code == 401


def test_api_key_auth_reports_itself_as_not_an_entra_identity(client, sso_on, monkeypatch):
    """Key auth must NOT masquerade as a person in the audit trail."""
    monkeypatch.setattr(auth_mod, "API_KEY", "secret123")
    monkeypatch.setattr(config, "API_KEY", "secret123")
    body = client.get("/me", headers={"X-API-Key": "secret123"}).json()
    assert body["authenticated"] is False
    assert body["source"] == "api-key"


# ------------------------------------------------- unchanged when SSO off ---

def test_sso_unconfigured_falls_back_to_the_shared_key(client, monkeypatch):
    monkeypatch.setattr(config, "ENTRA_TENANT_ID", "")
    monkeypatch.setattr(config, "ENTRA_CLIENT_ID", "")
    monkeypatch.setattr(auth_mod, "API_KEY", "secret123")
    assert client.get("/items").status_code == 401
    assert client.get("/items", headers={"X-API-Key": "secret123"}).status_code == 200


def test_sso_unconfigured_and_no_key_stays_open_for_local_dev(client, monkeypatch):
    monkeypatch.setattr(config, "ENTRA_TENANT_ID", "")
    monkeypatch.setattr(config, "ENTRA_CLIENT_ID", "")
    monkeypatch.setattr(auth_mod, "API_KEY", "")
    assert client.get("/items").status_code == 200


def test_a_bearer_token_is_ignored_when_sso_is_off(client, monkeypatch, keypair):  # noqa: F811
    """A server with no tenant configured must not honour tokens at all."""
    monkeypatch.setattr(config, "ENTRA_TENANT_ID", "")
    monkeypatch.setattr(config, "ENTRA_CLIENT_ID", "")
    monkeypatch.setattr(auth_mod, "API_KEY", "secret123")
    r = client.get("/items", headers=bearer(keypair))
    assert r.status_code == 401
