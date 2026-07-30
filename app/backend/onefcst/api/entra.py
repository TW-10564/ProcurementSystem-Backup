"""Verify Microsoft Entra ID tokens presented by the SPA.

WHY THIS FILE EXISTS, AND WHY THERE IS NO CLIENT SECRET
-------------------------------------------------------
The frontend signs in as a PUBLIC client: MSAL in the browser runs the
authorization-code + PKCE flow and gets a token directly from Entra. Nothing
confidential is involved, so there is no client secret to store, rotate, or
have expire on a date nobody wrote down. (The earlier oauth2-proxy design put
the exchange server-side and therefore *required* a secret; it refuses to
start without one.)

The consequence is that this backend can no longer treat "the request reached
me" as evidence of anything. A browser-held token is attacker-supplied input.
So every request carries `Authorization: Bearer <jwt>` and this module checks,
cryptographically:

  1. SIGNATURE  — RS256, against the tenant's published signing keys. This is
     the whole ballgame: without it a caller could mint any claims they like.
  2. `alg`      — pinned to RS256. Never trust the token's own header to pick
     the algorithm: accepting `none` lets anyone in, and accepting an HMAC
     alg lets them sign with the (public) modulus. PyJWT enforces the
     allowlist we pass; it must never be widened to include a symmetric alg.
  3. `aud`      — this app, not some other app in the tenant. Skipping this is
     the classic confused-deputy: a token minted for any other application
     the user can sign into would otherwise be accepted here.
  4. `iss`+`tid`— our tenant. Entra's issuer is per-tenant, so pinning it stops
     a token from a *different* Microsoft tenant being replayed at us.
  5. `exp`/`nbf`— lifetime, with a small leeway for clock skew.

Only public information is needed for all of that, which is the point: this
process holds no credential belonging to the identity provider.

WHAT THIS DOES NOT DO
    Authorization beyond an optional group allowlist. A validated token says
    *who* the caller is, not what they may do. Per-endpoint role rules are
    follow-on work (docs/SSO_TO_GOLIVE_TASKS.md phase 4); `Identity.groups`
    and `.roles` are populated here so that work needs no protocol change.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

import jwt
from jwt import PyJWKClient

# Imported as a MODULE, not as `from ..config import ENTRA_TENANT_ID`. A name
# bound at import time cannot be monkeypatched by the tests and would freeze
# the config at first import; reading `config.X` at call time keeps both
# honest.
from .. import config

# Entra publishes v2.0 tokens with this issuer. Tokens minted through the v1
# endpoint carry the sts.windows.net form instead; both are legitimately ours
# and both are pinned to OUR tenant id, which is what actually matters.
def _issuers(tenant: str) -> tuple[str, ...]:
    return (
        f"https://login.microsoftonline.com/{tenant}/v2.0",
        f"https://sts.windows.net/{tenant}/",
    )


def _jwks_url(tenant: str) -> str:
    return f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys"


class TokenError(Exception):
    """Token rejected. The message is for the log, not for the client.

    Deliberately not an HTTPException: this module says *why* a token failed,
    and the caller decides what to tell the outside world. Echoing these
    reasons back to an unauthenticated caller would confirm which of the
    checks it tripped, which is free reconnaissance.
    """


@dataclass(frozen=True)
class Identity:
    """The signed-in employee, as Entra describes them."""

    subject: str                      # stable per user+app; safe for logs
    oid: str                          # tenant-wide user object id
    email: str
    name: str
    groups: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    claims: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def audit_author(self) -> str:
        """What to record in the audit log / `author` fields.

        Email when present, else the object id — never the display name,
        which is neither unique nor stable.
        """
        return self.email or self.oid or self.subject


class _SigningKeys:
    """The tenant's public signing keys, cached, with rollover handling.

    Entra rotates these. A cache that only expires on a timer means every
    rotation causes a window of blanket 401s, so an unrecognised `kid` also
    forces a refetch — rate-limited, because that path is reachable by an
    unauthenticated caller and would otherwise be a free way to make this
    server hammer Microsoft (and to stall it while doing so).
    """

    TTL = 3600.0                # ordinary refresh
    MIN_REFETCH_INTERVAL = 60.0  # floor between kid-miss refetches

    def __init__(self, tenant: str, timeout: float = 10.0):
        self._url = _jwks_url(tenant)
        self._timeout = timeout
        self._lock = threading.Lock()
        self._client: PyJWKClient | None = None
        self._fetched_at = 0.0
        self._last_forced = 0.0

    def _new_client(self) -> PyJWKClient:
        # PyJWKClient does its own fetching and per-kid caching; we wrap it
        # only to control WHEN it is thrown away.
        return PyJWKClient(self._url, cache_keys=True, timeout=self._timeout)

    def key_for(self, token: str):
        now = time.monotonic()
        with self._lock:
            if self._client is None or now - self._fetched_at > self.TTL:
                self._client = self._new_client()
                self._fetched_at = now
            client = self._client

        try:
            return client.get_signing_key_from_jwt(token).key
        except Exception as first:                       # noqa: BLE001
            # Unknown kid, or the cached document is stale/corrupt. Refetch
            # once, if we have not just done so.
            with self._lock:
                if time.monotonic() - self._last_forced < self.MIN_REFETCH_INTERVAL:
                    raise TokenError(f"no signing key for this token: {first}") from first
                self._last_forced = time.monotonic()
                self._client = self._new_client()
                self._fetched_at = time.monotonic()
                client = self._client
            try:
                return client.get_signing_key_from_jwt(token).key
            except (urllib.error.URLError, TimeoutError) as e:
                # Distinguish "we cannot reach Microsoft" from "this token is
                # forged" — they look identical from the client side but mean
                # completely different things to whoever is on call.
                raise TokenError(f"signing keys unreachable: {e}") from e
            except Exception as e:                       # noqa: BLE001
                raise TokenError(f"no signing key for this token: {e}") from e


_keys: _SigningKeys | None = None
_keys_lock = threading.Lock()


def _signing_keys() -> _SigningKeys:
    global _keys
    with _keys_lock:
        if _keys is None:
            _keys = _SigningKeys(config.ENTRA_TENANT_ID)
        return _keys


def reset_signing_keys(keys: _SigningKeys | None = None) -> None:
    """Swap or clear the key cache. For tests, and for a config reload."""
    global _keys
    with _keys_lock:
        _keys = keys


def _audiences() -> tuple[str, ...]:
    """Accepted `aud` values.

    Both forms are accepted because which one arrives depends on a choice made
    in Entra, not in this code: an ID token from a single-registration setup
    carries the bare client id, while an access token for an exposed API scope
    carries `api://<client-id>`. Accepting both means enabling the API scope
    later needs no backend change — and neither form is another application,
    so nothing is loosened.
    """
    out = [config.ENTRA_AUDIENCE]
    if config.ENTRA_CLIENT_ID:
        for extra in (config.ENTRA_CLIENT_ID, f"api://{config.ENTRA_CLIENT_ID}"):
            if extra not in out:
                out.append(extra)
    return tuple(a for a in out if a)


def verify_token(token: str, *, leeway: float = 60.0) -> Identity:
    """Validate a bearer token and return who it belongs to.

    Raises TokenError on anything short of a fully valid token for this app
    and this tenant.
    """
    if not config.ENTRA_TENANT_ID or not config.ENTRA_CLIENT_ID:
        raise TokenError("Entra is not configured on this server")
    if not token or token.count(".") != 2:
        raise TokenError("not a JWT")

    key = _signing_keys().key_for(token)
    try:
        claims = jwt.decode(
            token,
            key=key,
            # Asymmetric only, and only the one algorithm Entra actually uses.
            algorithms=["RS256"],
            audience=list(_audiences()),
            issuer=list(_issuers(config.ENTRA_TENANT_ID)),
            leeway=leeway,
            options={
                "require": ["exp", "iat", "aud", "iss", "sub"],
                "verify_signature": True,
                "verify_exp": True,
                "verify_nbf": True,
                "verify_aud": True,
                "verify_iss": True,
            },
        )
    except jwt.ExpiredSignatureError as e:
        raise TokenError("token expired") from e
    except jwt.InvalidAudienceError as e:
        raise TokenError("token was not issued for this application") from e
    except jwt.InvalidIssuerError as e:
        raise TokenError("token was not issued by this tenant") from e
    except jwt.InvalidTokenError as e:
        raise TokenError(f"invalid token: {e}") from e

    # `iss` is already pinned, but a tenant-id mismatch would mean Entra
    # returned something we fundamentally do not understand; fail loudly
    # rather than infer.
    tid = claims.get("tid")
    if tid and tid != config.ENTRA_TENANT_ID:
        raise TokenError("token belongs to a different tenant")

    groups = tuple(str(g) for g in (claims.get("groups") or ()))
    roles = tuple(str(r) for r in (claims.get("roles") or ()))

    if config.ENTRA_ALLOWED_GROUPS:
        allowed = set(config.ENTRA_ALLOWED_GROUPS)
        if not (allowed & set(groups)) and not (allowed & set(roles)):
            # A real, fully-signed identity that is simply not permitted here.
            raise TokenError("identity is not in an allowed group")

    return Identity(
        subject=str(claims.get("sub", "")),
        oid=str(claims.get("oid", "")),
        # `email` is only present when the tenant populates it; `preferred_
        # username` is the reliable fallback for a work account and is what
        # users recognise as "their" address.
        email=str(claims.get("email") or claims.get("preferred_username") or ""),
        name=str(claims.get("name") or ""),
        groups=groups,
        roles=roles,
        claims=claims,
    )


def describe_config() -> dict[str, Any]:
    """Non-secret view of the SSO config, for /health-style introspection."""
    return {
        "enabled": bool(config.ENTRA_TENANT_ID and config.ENTRA_CLIENT_ID),
        "tenant_id": config.ENTRA_TENANT_ID,
        "client_id": config.ENTRA_CLIENT_ID,
        "audiences": list(_audiences()),
        "issuers": list(_issuers(config.ENTRA_TENANT_ID)) if config.ENTRA_TENANT_ID else [],
        "restricted_to_groups": list(config.ENTRA_ALLOWED_GROUPS),
    }


__all__ = [
    "Identity",
    "TokenError",
    "describe_config",
    "reset_signing_keys",
    "verify_token",
]


# Kept out of the public surface: only used by the test suite to build a fake
# key set without touching the network.
def _test_signing_keys(jwks: dict) -> _SigningKeys:      # pragma: no cover
    keys = _SigningKeys(config.ENTRA_TENANT_ID or "test-tenant")

    class _Static(PyJWKClient):
        def __init__(self):
            super().__init__("https://example.invalid/keys")

        def fetch_data(self):
            return json.loads(json.dumps(jwks))

    keys._new_client = lambda: _Static()                 # type: ignore[method-assign]
    return keys
