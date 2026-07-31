"""Sign-in driven by the BACKEND — authorization code flow, confidential client.

    browser ──► GET /auth/login ──► Microsoft ──► GET /auth/callback ──► SPA
                                                        │
                              backend exchanges code + CLIENT SECRET for tokens,
                              verifies the id_token, and sets its own signed,
                              HttpOnly session cookie.

WHAT THIS CHANGES vs THE MSAL/PKCE DESIGN
    * The `VITE_ENTRA_*` values leave the JS bundle — the browser no longer
      needs to know the tenant or client id to start a sign-in.
    * No Microsoft token is ever stored in the browser. The session cookie is
      HttpOnly, so script cannot read it, which is the one thing PKCE-in-the-
      browser could not defend: XSS can no longer exfiltrate a bearer token.
    * The app becomes a confidential client, so there is now a SECRET to store
      and rotate. It expires; put the date in a diary.

    Worth being precise about one thing, because it motivated the change: the
    tenant and client ids are NOT hidden by this. The redirect to
    login.microsoftonline.com carries both in the address bar on every
    sign-in. They are identifiers, not credentials. What is protected is the
    secret and the token.

SECURITY DECISIONS, AND WHY
    * **PKCE is still used**, even though a secret is present. It costs one
      hash and removes the value of a stolen authorization code entirely.
    * **`state` is bound to the browser**, not just remembered server-side: it
      is carried in a short-lived signed cookie, so a callback replayed in a
      different browser cannot complete. This is the CSRF defence for the
      login endpoint.
    * **`nonce` is checked against the id_token**, which is what stops a token
      obtained elsewhere being injected into our callback.
    * **The id_token is verified with the existing `entra.verify_token`** —
      signature against the tenant JWKS, plus aud/iss/tid/exp. Trusting the
      token merely because it arrived over TLS from the token endpoint would
      skip the check that makes any of this meaningful.
    * **The session cookie is `SameSite=Lax`**, which stops another site
      driving state-changing requests with the user's cookie attached. Writes
      additionally require a custom header (see `auth.py`), which a
      cross-origin form post cannot set.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

import jwt
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from .. import config
from . import entra

router = APIRouter()

SESSION_COOKIE = "onefcst_session"
STATE_COOKIE = "onefcst_oauth_state"
STATE_TTL_SECONDS = 600          # a sign-in that takes >10 min has been abandoned


def _authority() -> str:
    return f"https://login.microsoftonline.com/{config.ENTRA_TENANT_ID}"


def _session_key() -> str:
    """The key the session cookie is signed with.

    Falling back to a random per-process value is deliberate: an unset secret
    then means "sessions do not survive a restart", which is visible and
    annoying, rather than "everyone shares a predictable key", which is not
    visible at all.
    """
    return config.SESSION_SECRET or _EPHEMERAL_KEY


_EPHEMERAL_KEY = secrets.token_urlsafe(48)


# ------------------------------------------------------------- session -----

def issue_session(claims: dict) -> str:
    """Mint the signed session cookie from verified id_token claims.

    Only the fields the app actually uses are copied in. The Microsoft tokens
    themselves are deliberately NOT stored: this app never calls Graph on the
    user's behalf server-side, so keeping an access token would be holding a
    credential with no purpose — the definition of unnecessary exposure.
    """
    now = int(time.time())
    payload = {
        "sub": claims.get("sub", ""),
        "oid": claims.get("oid", ""),
        "email": (claims.get("email") or claims.get("preferred_username")
                  or claims.get("upn") or ""),
        "name": claims.get("name", ""),
        "groups": claims.get("groups", []) or [],
        "roles": claims.get("roles", []) or [],
        "iat": now,
        "exp": now + int(config.SESSION_HOURS * 3600),
    }
    return jwt.encode(payload, _session_key(), algorithm="HS256")


def read_session(token: str) -> entra.Identity | None:
    """The Identity behind a session cookie, or None if it is not usable.

    Never raises: a tampered, expired or unparseable cookie is simply "not
    signed in", and the caller turns that into a redirect to sign in again.
    """
    if not token:
        return None
    try:
        claims = jwt.decode(token, _session_key(), algorithms=["HS256"],
                            options={"require": ["exp", "sub"]})
    except jwt.InvalidTokenError:
        return None
    return entra.Identity(
        subject=claims.get("sub", ""), oid=claims.get("oid", ""),
        email=(claims.get("email") or "").strip(), name=claims.get("name", ""),
        groups=tuple(claims.get("groups") or ()),
        roles=tuple(claims.get("roles") or ()), claims=claims)


def _set_session_cookie(response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, token,
        max_age=int(config.SESSION_HOURS * 3600),
        httponly=True,                       # script cannot read it — the point
        secure=config.SESSION_COOKIE_SECURE,
        samesite="lax",                      # blocks cross-site state changes
        path="/",
    )


# --------------------------------------------------------------- flow ------

def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


@router.get("/auth/login")
def login(request: Request, next: str = "/app"):
    """Start sign-in: remember state+nonce+verifier in a signed cookie, then
    hand the browser to Microsoft."""
    if not config.OAUTH_BFF_ENABLED:
        raise HTTPException(503, "backend sign-in is not configured")

    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)

    # Only same-site paths: an open redirect here would let someone craft a
    # "sign in to the portal" link that lands the user on their site.
    target = next if next.startswith("/") and not next.startswith("//") else "/app"

    params = {
        "client_id": config.ENTRA_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": config.OAUTH_REDIRECT_URI,
        "response_mode": "query",
        "scope": "openid profile email",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    url = f"{_authority()}/oauth2/v2.0/authorize?{urllib.parse.urlencode(params)}"

    response = RedirectResponse(url, status_code=302)
    pending = jwt.encode({"state": state, "nonce": nonce, "verifier": verifier,
                          "next": target, "exp": int(time.time()) + STATE_TTL_SECONDS},
                         _session_key(), algorithm="HS256")
    response.set_cookie(STATE_COOKIE, pending, max_age=STATE_TTL_SECONDS,
                        httponly=True, secure=config.SESSION_COOKIE_SECURE,
                        samesite="lax", path="/")
    return response


def _exchange_code(code: str, verifier: str) -> dict:
    """Swap the authorization code for tokens, authenticating with the secret.

    urllib rather than a new HTTP dependency, matching entra.py. A failure here
    is reported as 502: the caller did nothing wrong, we could not reach or
    satisfy the identity provider.
    """
    body = urllib.parse.urlencode({
        "client_id": config.ENTRA_CLIENT_ID,
        "client_secret": config.ENTRA_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.OAUTH_REDIRECT_URI,
        "code_verifier": verifier,
        "scope": "openid profile email",
    }).encode()

    request_ = urllib.request.Request(
        f"{_authority()}/oauth2/v2.0/token", data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request_, timeout=15) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:300]
        # Logged in full, summarised to the client: the response can name which
        # of client_id/secret/redirect_uri Entra objected to, and that is
        # reconnaissance for anyone who can reach this endpoint.
        print(f"[oauth] token exchange failed: {exc.code} {detail}")
        raise HTTPException(502, "sign-in failed at the identity provider")
    except Exception as exc:                                   # noqa: BLE001
        print(f"[oauth] token exchange error: {exc!r}")
        raise HTTPException(502, "could not reach the identity provider")


@router.get("/auth/callback")
def callback(request: Request, code: str | None = None, state: str | None = None,
             error: str | None = None, error_description: str | None = None):
    if not config.OAUTH_BFF_ENABLED:
        raise HTTPException(503, "backend sign-in is not configured")

    if error:
        # Entra's own code is worth surfacing: AADSTS50105 ("not assigned")
        # and a misconfigured redirect URI look identical to the user
        # otherwise, and only one of them is IT's to fix.
        print(f"[oauth] provider returned {error}: {error_description}")
        raise HTTPException(403, f"sign-in refused by Microsoft ({error})")
    if not code or not state:
        raise HTTPException(400, "missing code or state")

    pending_cookie = request.cookies.get(STATE_COOKIE, "")
    try:
        pending = jwt.decode(pending_cookie, _session_key(), algorithms=["HS256"])
    except jwt.InvalidTokenError:
        # Expired, tampered, or a callback replayed in a different browser.
        raise HTTPException(400, "sign-in expired or was started elsewhere — "
                                 "please try again")

    if not secrets.compare_digest(str(pending.get("state", "")), state):
        raise HTTPException(400, "state mismatch")

    tokens = _exchange_code(code, pending.get("verifier", ""))
    id_token = tokens.get("id_token")
    if not id_token:
        raise HTTPException(502, "identity provider returned no id_token")

    # The whole point of the verification: signature against the tenant JWKS
    # plus aud/iss/tid/exp. Arriving over TLS from the token endpoint is not
    # by itself evidence of anything.
    try:
        identity = entra.verify_token(id_token)
    except entra.TokenError as exc:
        print(f"[oauth] id_token rejected: {exc}")
        raise HTTPException(403, "sign-in token could not be verified")

    claims = dict(identity.claims)
    if claims.get("nonce") and claims["nonce"] != pending.get("nonce"):
        # Stops a token minted for some other sign-in being injected here.
        raise HTTPException(400, "nonce mismatch")

    response = RedirectResponse(pending.get("next", "/app"), status_code=302)
    _set_session_cookie(response, issue_session(claims))
    response.delete_cookie(STATE_COOKIE, path="/")
    return response


@router.post("/auth/logout")
@router.get("/auth/logout")
def logout(request: Request, federated: bool = False):
    """Drop our session. `federated=true` also ends the Microsoft session.

    Local-only by default, matching the behaviour chosen for the SPA: the fast
    exit people actually wanted. On a shared PC use federated=true, or close
    the browser.
    """
    target = "/"
    if federated and config.ENTRA_TENANT_ID:
        origin = str(request.base_url).rstrip("/")
        target = (f"{_authority()}/oauth2/v2.0/logout"
                  f"?post_logout_redirect_uri={urllib.parse.quote(origin + '/', safe='')}")
    response = RedirectResponse(target, status_code=302)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/auth/status")
def status():
    """Whether the backend runs sign-in, so the SPA knows to redirect here.

    Deliberately booleans only — no tenant id, no client id. With the BFF flow
    the browser never needs them, and this endpoint is public.
    """
    return {"backend_sign_in": config.OAUTH_BFF_ENABLED,
            "session_hours": config.SESSION_HOURS}
