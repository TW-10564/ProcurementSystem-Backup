"""Basic per-client rate limiting — a fixed 60-second window, in-memory.

Not a general-purpose rate limiter: this app runs as a single uvicorn
process (see db.py's NullPool note — no multi-worker/shared-store setup),
so an in-memory counter is enough; no Redis or similar needed. Disabled by
default (RATE_LIMIT_PER_MINUTE=0) — the dev default, same stance as
API_KEY in auth.py, so local work isn't blocked.

Client identity: the X-API-Key header value if present, else the request's
source IP. Keying by API key (not just IP) matters here because this app
is gated by a SINGLE SHARED key (see auth.py) — everyone behind that key
looks like "the same client" from an IP-based scheme only if they also
share an IP, which isn't guaranteed (VPN, office NAT, etc. can differ).
Keying by the key itself catches abuse regardless of source IP; falling
back to IP only matters for the pre-auth case (no key sent at all, e.g.
brute-forcing the key blind) so those attempts are still capped.

/health is exempt — same reasoning as auth.py: a liveness-probe endpoint
must never be the thing that trips a rate limit."""
from __future__ import annotations

import time

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from ..config import RATE_LIMIT_PER_MINUTE

WINDOW_SECONDS = 60
# Above this many tracked clients, prune expired windows on the next
# request — bounds memory if many distinct IPs hit the API without ever
# sending a valid key (each one is its own "client_id" until pruned).
PRUNE_THRESHOLD = 10_000


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self._windows: dict[str, tuple[float, int]] = {}

    async def dispatch(self, request: Request, call_next):
        if not RATE_LIMIT_PER_MINUTE or request.url.path == "/health":
            return await call_next(request)

        client_id = request.headers.get("x-api-key") or (
            request.client.host if request.client else "unknown")
        now = time.monotonic()
        if len(self._windows) > PRUNE_THRESHOLD:
            self._windows = {k: v for k, v in self._windows.items()
                             if now - v[0] < WINDOW_SECONDS}
        window_start, count = self._windows.get(client_id, (now, 0))
        if now - window_start >= WINDOW_SECONDS:
            window_start, count = now, 0
        count += 1
        self._windows[client_id] = (window_start, count)

        if count > RATE_LIMIT_PER_MINUTE:
            retry_after = max(0, int(WINDOW_SECONDS - (now - window_start)))
            return JSONResponse(
                {"detail": "rate limit exceeded, try again shortly"},
                status_code=429, headers={"Retry-After": str(retry_after)})

        return await call_next(request)
