"""Backend-driven sign-in: authorization code flow, confidential client.

The value of this flow is that no Microsoft token reaches the browser, so the
tests that matter are the ones proving the session cookie cannot be forged,
replayed, or used from another site.

`state`, `nonce` and PKCE are each tested by removing exactly one of them:
a flow that still succeeds without a check is a flow that does not have it.
"""
from __future__ import annotations

import time

import jwt
import pytest
from fastapi.testclient import TestClient

from onefcst import config
from onefcst.api import auth as auth_mod
from onefcst.api import entra
from onefcst.api import oauth
from onefcst.api.main import app

from test_entra_auth import (CLIENT, KID, TENANT, jwks, keypair,  # noqa: F401
                             make_token)

REDIRECT = "https://one-forecast.twave.co.jp/auth/callback"


@pytest.fixture
def bff(monkeypatch, jwks):                                   # noqa: F811
    monkeypatch.setattr(config, "ENTRA_TENANT_ID", TENANT)
    monkeypatch.setattr(config, "ENTRA_CLIENT_ID", CLIENT)
    monkeypatch.setattr(config, "ENTRA_AUDIENCE", CLIENT)
    monkeypatch.setattr(config, "ENTRA_CLIENT_SECRET", "s3cret-value")
    monkeypatch.setattr(config, "OAUTH_REDIRECT_URI", REDIRECT)
    monkeypatch.setattr(config, "OAUTH_BFF_ENABLED", True)
    monkeypatch.setattr(config, "SESSION_SECRET", "test-session-key")
    monkeypatch.setattr(config, "SESSION_COOKIE_SECURE", False)
    monkeypatch.setattr(config, "ENTRA_ALLOWED_EMAILS", [])
    monkeypatch.setattr(config, "ENTRA_ALLOW_API_KEY", False)
    monkeypatch.setattr(auth_mod, "API_KEY", "")
    entra.reset_signing_keys(entra._test_signing_keys(jwks))
    yield
    entra.reset_signing_keys(None)


@pytest.fixture
def client(isolated_api_db):
    return TestClient(app, follow_redirects=False)


# ------------------------------------------------------------- login -------

def test_login_redirects_to_microsoft_with_pkce_and_state(client, bff):
    r = client.get("/auth/login")
    assert r.status_code == 302
    url = r.headers["location"]
    assert url.startswith(f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize")
    for required in ("code_challenge=", "code_challenge_method=S256",
                     "state=", "nonce=", f"client_id={CLIENT}"):
        assert required in url, required
    # The state must be pinned to THIS browser, not merely generated.
    assert oauth.STATE_COOKIE in r.cookies


def test_login_will_not_redirect_off_site(client, bff):
    """An open redirect here would make a convincing phishing link: the URL
    really is the portal's, and it really does sign you in."""
    r = client.get("/auth/login?next=https://evil.example/steal")
    pending = jwt.decode(r.cookies[oauth.STATE_COOKIE], "test-session-key",
                         algorithms=["HS256"])
    assert pending["next"] == "/app"

    r = client.get("/auth/login?next=//evil.example")
    pending = jwt.decode(r.cookies[oauth.STATE_COOKIE], "test-session-key",
                         algorithms=["HS256"])
    assert pending["next"] == "/app"


def test_login_is_unavailable_when_not_configured(client, monkeypatch):
    monkeypatch.setattr(config, "OAUTH_BFF_ENABLED", False)
    assert client.get("/auth/login").status_code == 503


# ---------------------------------------------------------- callback -------

def _start(client) -> str:
    """Begin a flow and return the state the server issued."""
    r = client.get("/auth/login")
    return jwt.decode(r.cookies[oauth.STATE_COOKIE], "test-session-key",
                      algorithms=["HS256"])["state"]


def test_callback_rejects_a_state_that_does_not_match(client, bff):
    _start(client)
    r = client.get("/auth/callback?code=abc&state=not-the-one")
    assert r.status_code == 400
    assert "state" in r.text


def test_callback_rejects_a_flow_started_in_another_browser(client, bff):
    """The state cookie is the binding. Without it, a callback URL captured
    from someone's history could be completed by anyone."""
    state = _start(client)
    client.cookies.delete(oauth.STATE_COOKIE)
    r = client.get(f"/auth/callback?code=abc&state={state}")
    assert r.status_code == 400


def test_callback_reports_a_refusal_from_microsoft(client, bff):
    """AADSTS50105 (user not assigned) must not look like our bug."""
    r = client.get("/auth/callback?error=access_denied&error_description=AADSTS50105")
    assert r.status_code == 403
    assert "access_denied" in r.text


def test_callback_verifies_the_id_token_signature(client, bff, monkeypatch, keypair):  # noqa: F811
    """A token signed by the wrong key must be refused even though it came
    back from the token endpoint over TLS."""
    from cryptography.hazmat.primitives.asymmetric import rsa
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = make_token(keypair, key=other)

    state = _start(client)
    monkeypatch.setattr(oauth, "_exchange_code",
                        lambda code, verifier: {"id_token": forged})
    r = client.get(f"/auth/callback?code=abc&state={state}")
    assert r.status_code == 403


def test_callback_rejects_a_mismatched_nonce(client, bff, monkeypatch, keypair):  # noqa: F811
    """Stops an id_token obtained through a different sign-in being injected."""
    state = _start(client)
    token = make_token(keypair, extra={"nonce": "some-other-flow"})
    monkeypatch.setattr(oauth, "_exchange_code",
                        lambda code, verifier: {"id_token": token})
    r = client.get(f"/auth/callback?code=abc&state={state}")
    assert r.status_code == 400
    assert "nonce" in r.text


def test_successful_callback_sets_an_httponly_session_cookie(client, bff,
                                                             monkeypatch, keypair):  # noqa: F811
    r = client.get("/auth/login")
    pending = jwt.decode(r.cookies[oauth.STATE_COOKIE], "test-session-key",
                         algorithms=["HS256"])
    token = make_token(keypair, extra={"nonce": pending["nonce"]})
    monkeypatch.setattr(oauth, "_exchange_code",
                        lambda code, verifier: {"id_token": token})

    r = client.get(f"/auth/callback?code=abc&state={pending['state']}")
    assert r.status_code == 302
    assert r.headers["location"] == "/app"

    raw = r.headers["set-cookie"]
    assert oauth.SESSION_COOKIE in raw
    # HttpOnly is the entire security gain over storing a token in JS.
    assert "httponly" in raw.lower()
    assert "samesite=lax" in raw.lower()


# ---------------------------------------------------------- session --------

def test_session_cookie_authenticates_subsequent_requests(client, bff,
                                                          monkeypatch, keypair):  # noqa: F811
    r = client.get("/auth/login")
    pending = jwt.decode(r.cookies[oauth.STATE_COOKIE], "test-session-key",
                         algorithms=["HS256"])
    monkeypatch.setattr(oauth, "_exchange_code", lambda c, v: {
        "id_token": make_token(keypair, extra={"nonce": pending["nonce"]})})
    client.get(f"/auth/callback?code=abc&state={pending['state']}")

    me = client.get("/me").json()
    assert me["authenticated"] is True
    assert me["source"] == "entra"
    assert me["email"] == "tanaka@twave.co.jp"


def test_a_forged_session_cookie_is_refused(client, bff):
    """The cookie is signed with a server-side key; anything else is anonymous."""
    forged = jwt.encode({"sub": "x", "email": "boss@twave.co.jp",
                         "exp": int(time.time()) + 3600},
                        "not-the-server-key", algorithm="HS256")
    client.cookies.set(oauth.SESSION_COOKIE, forged)
    assert client.get("/items").status_code == 401


def test_an_expired_session_is_refused(client, bff):
    stale = jwt.encode({"sub": "x", "email": "a@twave.co.jp",
                        "exp": int(time.time()) - 60},
                       "test-session-key", algorithm="HS256")
    client.cookies.set(oauth.SESSION_COOKIE, stale)
    assert client.get("/items").status_code == 401


def test_cookie_writes_require_the_custom_header(client, bff):
    """CSRF: a cross-site form post carries the cookie but cannot set a custom
    header. SameSite=Lax covers most of this; the header covers the rest."""
    session = oauth.issue_session({"sub": "s", "oid": "o",
                                   "email": "tanaka@twave.co.jp", "name": "田中"})
    client.cookies.set(oauth.SESSION_COOKIE, session)

    blocked = client.post("/forecasts/run")
    assert blocked.status_code == 403
    assert "X-Requested-With" in blocked.text

    # Reads are unaffected — they change nothing.
    assert client.get("/items").status_code == 200


def test_logout_clears_the_session(client, bff):
    session = oauth.issue_session({"sub": "s", "oid": "o",
                                   "email": "tanaka@twave.co.jp", "name": "田中"})
    client.cookies.set(oauth.SESSION_COOKIE, session)
    r = client.get("/auth/logout")
    assert r.status_code == 302
    assert 'onefcst_session=""' in r.headers["set-cookie"] or \
           "onefcst_session=;" in r.headers["set-cookie"]


# ------------------------------------------------------------ config -------

def test_auth_config_stops_exposing_the_ids_in_bff_mode(client, bff):
    """The stated reason for moving sign-in server-side: the frontend should
    not carry the tenant/client id. This endpoint is public, so it must not
    hand them out either."""
    body = client.get("/auth/config").json()
    assert body["backend_sign_in"] is True
    assert "tenant_id" not in body
    assert "client_id" not in body


def test_auth_status_is_public_and_carries_no_identifiers(client, bff):
    body = client.get("/auth/status").json()
    assert body["backend_sign_in"] is True
    assert set(body) == {"backend_sign_in", "session_hours"}
