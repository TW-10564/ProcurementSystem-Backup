"""Minimal shared-key gate for the API.

Before this, onefcst had ZERO auth — anyone reaching the port could confirm
compat rules, record adjustments, or trigger a forecast regeneration. This
is a floor, not a ceiling: a single shared secret checked on every request,
enough to stop an open port from being an open door. Multi-user auth (who
did what — already tracked via the `author` field on adjustments/compat
confirmations) is a separate, later upgrade.

Disabled by default (ONEFCST_API_KEY unset) so local dev isn't blocked —
set the env var to require every request to carry the same value in
X-API-Key. /health is always exempt (standard liveness-probe convention;
it exposes nothing sensitive).
"""
from __future__ import annotations

import hmac
import logging

from fastapi import Header, HTTPException, Request

from .. import config
from ..config import API_KEY
from . import entra

log = logging.getLogger(__name__)


def _is_public(request: Request) -> bool:
    """Endpoints that must answer without any credential.

    /health so liveness probes work, and GET /maintenance so the frontend can
    still explain an outage when the key is wrong or a token has expired —
    otherwise users get a bare 401 in place of the reason.

    /auth/config likewise: it is what the SPA needs in order to sign in at
    all, so requiring a sign-in to read it would be circular. It carries only
    the tenant/client ids, which are visible in the browser's URL bar during
    any OIDC redirect, plus booleans — never a key or a secret.
    """
    if request.url.path in ("/health", "/auth/config"):
        return True
    return request.url.path == "/maintenance" and request.method == "GET"


def _api_key_ok(x_api_key: str | None) -> bool:
    # compare as bytes: compare_digest(str, str) raises TypeError (→ 500, not
    # 401) if either side contains non-ASCII characters
    return bool(x_api_key) and hmac.compare_digest(
        x_api_key.encode(), API_KEY.encode())


async def require_identity(
    request: Request,
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    """The single auth gate, applied app-wide in main.py.

    Three modes, chosen by configuration rather than by the request, so there
    is no way for a caller to select the weakest one:

      * Entra configured  -> a signed Microsoft token is required. The shared
        key is also accepted iff ONEFCST_ENTRA_ALLOW_API_KEY is on, for
        scripted callers that cannot do an interactive sign-in. That is not a
        browser bypass: with SSO on, the SPA is built without VITE_API_KEY, so
        no browser is given a key to begin with.
      * only ONEFCST_API_KEY set -> the pre-SSO shared-key gate, unchanged.
      * neither set        -> open, the local-dev default.

    On success the caller is attached to `request.state.identity` (an
    entra.Identity, or None for key auth) so endpoints can attribute writes
    without re-parsing anything.
    """
    request.state.identity = None

    entra_on = bool(config.ENTRA_TENANT_ID and config.ENTRA_CLIENT_ID)
    if not entra_on:
        return await require_api_key(request, x_api_key)

    if _is_public(request):
        return

    token = ""
    if authorization and authorization[:7].lower() == "bearer ":
        token = authorization[7:].strip()

    if token:
        try:
            request.state.identity = entra.verify_token(token)
            return
        except entra.TokenError as e:
            # Logged in full, reported as a bare 401: the reason distinguishes
            # "expired" from "forged" from "wrong tenant", which is exactly
            # the reconnaissance an unauthenticated caller should not get.
            log.warning("rejected bearer token for %s %s: %s",
                        request.method, request.url.path, e)
            raise HTTPException(401, "invalid or expired sign-in") from e

    if config.ENTRA_ALLOW_API_KEY and API_KEY and _api_key_ok(x_api_key):
        return

    raise HTTPException(
        401, "sign-in required",
        # Tells a browser/HTTP client which scheme to use, and is what makes
        # this a well-behaved 401 rather than an opaque one.
        headers={"WWW-Authenticate": "Bearer"},
    )


def effective_author(request: Request, supplied: str | None) -> str:
    """Who to record against a write — the verified identity when we have one.

    Until SSO, `author` was whatever the browser sent: the UI kept a name in
    localStorage for the user to type, and one call site defaulted to the
    literal string "bala". So the audit log recorded a claim the client made
    about itself, which is worth almost nothing the moment it matters — a
    confirmed compat rule or an order action is exactly the record you would
    want to trust in a dispute.

    With a validated token present, that token wins and the client's value is
    IGNORED rather than merged. Preferring the supplied name "when it looks
    filled in" would leave the same hole open: anyone could still attribute a
    write to a colleague.

    Falls back to the supplied value when SSO is off, so local dev and the
    pre-SSO deployment behave exactly as before.
    """
    ident = getattr(request.state, "identity", None)
    if ident is not None:
        return ident.audit_author
    return (supplied or "").strip() or "unknown"


async def require_api_key(request: Request, x_api_key: str | None = Header(default=None)):
    if not API_KEY:                      # auth disabled — the dev default
        return
    # /health, GET /maintenance and /auth/config are exempt — see _is_public.
    if _is_public(request):
        return
    if not _api_key_ok(x_api_key):
        raise HTTPException(401, "missing or invalid X-API-Key")
