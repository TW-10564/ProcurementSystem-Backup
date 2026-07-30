"""Entra token validation — the accept path and every reject path.

Runs entirely offline: an RSA keypair is generated here, published as a fake
JWKS, and tokens are minted locally. Nothing contacts Microsoft, so these
tests are meaningful in CI on a bare checkout.

The reject cases are the point. A validator that accepts good tokens but also
accepts an unsigned one, or one minted for a different application, is worse
than no validator at all — it turns "signed in" into a claim the client makes
about itself. Each test below corresponds to a forgery that would otherwise
succeed.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from onefcst import config
from onefcst.api import entra

TENANT = "11111111-2222-3333-4444-555555555555"
CLIENT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
OTHER_TENANT = "99999999-8888-7777-6666-555555555555"
KID = "test-key-1"


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture(scope="module")
def keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key, key.public_key()


@pytest.fixture(scope="module")
def jwks(keypair):
    _, pub = keypair
    # PyJWT can render a public key as a JWK, which keeps this fixture honest:
    # the test does not hand-roll base64url of the modulus.
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(pub))
    jwk.update({"kid": KID, "use": "sig", "alg": "RS256"})
    return {"keys": [jwk]}


@pytest.fixture(autouse=True)
def entra_configured(monkeypatch, jwks):
    """Point the module at our fake tenant and fake key set."""
    monkeypatch.setattr(config, "ENTRA_TENANT_ID", TENANT)
    monkeypatch.setattr(config, "ENTRA_CLIENT_ID", CLIENT)
    monkeypatch.setattr(config, "ENTRA_AUDIENCE", CLIENT)
    monkeypatch.setattr(config, "ENTRA_ALLOWED_GROUPS", [])
    entra.reset_signing_keys(entra._test_signing_keys(jwks))
    yield
    entra.reset_signing_keys(None)


def make_token(keypair, *, aud=CLIENT, iss=None, tid=TENANT, exp_delta=timedelta(hours=1),
               kid=KID, alg="RS256", key=None, extra=None, drop=()):
    priv, _ = keypair
    now = _now()
    claims = {
        "sub": "subject-abc",
        "oid": "user-object-id",
        "tid": tid,
        "aud": aud,
        "iss": iss or f"https://login.microsoftonline.com/{TENANT}/v2.0",
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + exp_delta).timestamp()),
        "name": "田中 太郎",
        "preferred_username": "tanaka@twave.co.jp",
    }
    claims.update(extra or {})
    for k in drop:
        claims.pop(k, None)
    return jwt.encode(claims, key or priv, algorithm=alg, headers={"kid": kid})


# --------------------------------------------------------------- accept ---

def test_valid_token_is_accepted_and_identity_extracted(keypair):
    ident = entra.verify_token(make_token(keypair))
    assert ident.email == "tanaka@twave.co.jp"
    assert ident.name == "田中 太郎"
    assert ident.oid == "user-object-id"
    assert ident.subject == "subject-abc"


def test_email_claim_wins_over_preferred_username(keypair):
    ident = entra.verify_token(make_token(keypair, extra={"email": "real@twave.co.jp"}))
    assert ident.email == "real@twave.co.jp"


def test_audit_author_prefers_email_never_display_name(keypair):
    """The audit log must not be keyed on a display name — not unique, not stable."""
    ident = entra.verify_token(make_token(keypair))
    assert ident.audit_author == "tanaka@twave.co.jp"
    no_mail = entra.verify_token(make_token(keypair, drop=("preferred_username",)))
    assert no_mail.audit_author == "user-object-id"
    assert no_mail.audit_author != no_mail.name


def test_api_scope_audience_accepted_without_config_change(keypair):
    """Switching Entra to an exposed API scope must not need a backend edit."""
    ident = entra.verify_token(make_token(keypair, aud=f"api://{CLIENT}"))
    assert ident.oid == "user-object-id"


def test_v1_issuer_form_accepted(keypair):
    ident = entra.verify_token(
        make_token(keypair, iss=f"https://sts.windows.net/{TENANT}/"))
    assert ident.oid == "user-object-id"


def test_groups_and_roles_are_exposed_for_later_authorization(keypair):
    ident = entra.verify_token(make_token(
        keypair, extra={"groups": ["g-purchasing"], "roles": ["Admin"]}))
    assert ident.groups == ("g-purchasing",)
    assert ident.roles == ("Admin",)


# --------------------------------------------------------------- reject ---

def test_unsigned_token_is_rejected(keypair):
    """alg=none. The single most important rejection in this file."""
    now = _now()
    forged = jwt.encode(
        {"sub": "attacker", "oid": "attacker", "tid": TENANT, "aud": CLIENT,
         "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
         "iat": int(now.timestamp()),
         "exp": int((now + timedelta(hours=1)).timestamp())},
        key="", algorithm="none", headers={"kid": KID})
    with pytest.raises(entra.TokenError):
        entra.verify_token(forged)


def test_token_signed_with_the_wrong_key_is_rejected(keypair):
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(entra.TokenError):
        entra.verify_token(make_token(keypair, key=attacker))


def test_hmac_signed_token_is_rejected(keypair):
    """Algorithm confusion: signing with a symmetric alg must not be accepted."""
    now = _now()
    forged = jwt.encode(
        {"sub": "attacker", "oid": "attacker", "tid": TENANT, "aud": CLIENT,
         "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
         "iat": int(now.timestamp()),
         "exp": int((now + timedelta(hours=1)).timestamp())},
        key="secret", algorithm="HS256", headers={"kid": KID})
    with pytest.raises(entra.TokenError):
        entra.verify_token(forged)


def test_token_for_a_different_application_is_rejected(keypair):
    """A real Microsoft token, real signature, wrong audience — confused deputy."""
    with pytest.raises(entra.TokenError):
        entra.verify_token(make_token(keypair, aud="some-other-app-guid"))


def test_token_from_a_different_tenant_is_rejected(keypair):
    with pytest.raises(entra.TokenError):
        entra.verify_token(make_token(
            keypair, iss=f"https://login.microsoftonline.com/{OTHER_TENANT}/v2.0"))


def test_tenant_id_claim_mismatch_is_rejected(keypair):
    """Right issuer, wrong `tid` — inconsistent, so refuse rather than guess."""
    with pytest.raises(entra.TokenError):
        entra.verify_token(make_token(keypair, tid=OTHER_TENANT))


def test_expired_token_is_rejected(keypair):
    with pytest.raises(entra.TokenError):
        entra.verify_token(make_token(keypair, exp_delta=timedelta(hours=-2)))


def test_token_missing_required_claims_is_rejected(keypair):
    for claim in ("exp", "aud", "iss", "sub"):
        with pytest.raises(entra.TokenError):
            entra.verify_token(make_token(keypair, drop=(claim,)))


def test_unknown_kid_is_rejected(keypair):
    with pytest.raises(entra.TokenError):
        entra.verify_token(make_token(keypair, kid="a-kid-we-never-published"))


def test_garbage_is_rejected(keypair):
    for junk in ("", "not-a-token", "a.b", "a.b.c.d", "....."):
        with pytest.raises(entra.TokenError):
            entra.verify_token(junk)


def test_group_allowlist_rejects_a_valid_identity_outside_it(keypair, monkeypatch):
    monkeypatch.setattr(config, "ENTRA_ALLOWED_GROUPS", ["g-purchasing"])
    with pytest.raises(entra.TokenError):
        entra.verify_token(make_token(keypair, extra={"groups": ["g-sales"]}))
    # and admits one inside it, by group or by app role
    assert entra.verify_token(make_token(keypair, extra={"groups": ["g-purchasing"]}))
    assert entra.verify_token(make_token(keypair, extra={"roles": ["g-purchasing"]}))


def test_unconfigured_server_refuses_to_validate(keypair, monkeypatch):
    """No tenant configured must mean "cannot verify", never "assume valid"."""
    token = make_token(keypair)
    monkeypatch.setattr(config, "ENTRA_TENANT_ID", "")
    with pytest.raises(entra.TokenError):
        entra.verify_token(token)
