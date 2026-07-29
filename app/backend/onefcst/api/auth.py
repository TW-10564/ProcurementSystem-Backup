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

from fastapi import Header, HTTPException, Request

from ..config import API_KEY


async def require_api_key(request: Request, x_api_key: str | None = Header(default=None)):
    if not API_KEY:                      # auth disabled — the dev default
        return
    # /maintenance (read) is exempt for the same reason as /health: the
    # frontend must be able to learn "we are down for maintenance" even
    # if the key is wrong/rotated, otherwise users get a bare 401 instead
    # of the explanation. Writing it still requires the key (see main.py).
    if request.url.path == "/health" or (
            request.url.path == "/maintenance" and request.method == "GET"):
        return
    # compare as bytes: compare_digest(str, str) raises TypeError (→ 500,
    # not 401) if either side contains non-ASCII characters
    if x_api_key is None or not hmac.compare_digest(
            x_api_key.encode(), API_KEY.encode()):
        raise HTTPException(401, "missing or invalid X-API-Key")
