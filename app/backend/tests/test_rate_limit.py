"""api/rate_limit.py — basic per-client request cap.

Client identity contract (hardened 2026-07-24 pre-deploy audit): only the
VALID shared key gets its own window; any invalid/absent key falls back to
the request's source IP. Keying on the raw header value — the old behavior —
handed a brute-forcer a fresh window per wrong guess, defeating the
module's own purpose.

Disabled by default (RATE_LIMIT_PER_MINUTE=0, same dev-friendly stance as
API_KEY in auth.py); enabled per-test by monkeypatching the module's own
config reference. The middleware instance (and its _windows dict) is built
once for the whole app, so an autouse fixture clears it between tests —
otherwise one test's IP-window usage bleeds into the next.
"""
import pytest
from fastapi.testclient import TestClient

from onefcst import config
from onefcst.api import rate_limit as rate_limit_mod
from onefcst.api.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _fresh_windows(client):
    client.get("/health")            # force lazy middleware-stack build
    stack = app.middleware_stack
    while stack is not None:
        if isinstance(stack, rate_limit_mod.RateLimitMiddleware):
            stack._windows.clear()
            break
        stack = getattr(stack, "app", None)


def _set_limit(monkeypatch, value):
    monkeypatch.setattr(rate_limit_mod, "RATE_LIMIT_PER_MINUTE", value)


def test_disabled_by_default_allows_unlimited_requests(client, monkeypatch):
    _set_limit(monkeypatch, 0)
    for _ in range(20):
        assert client.get("/health").status_code == 200


def test_health_is_always_exempt_even_when_enabled(client, monkeypatch):
    _set_limit(monkeypatch, 1)
    for _ in range(10):
        assert client.get("/health").status_code == 200


def test_exceeding_the_limit_returns_429_with_retry_after(client, monkeypatch):
    _set_limit(monkeypatch, 3)
    for _ in range(3):
        r = client.get("/items")
        assert r.status_code != 429
    r = client.get("/items")
    assert r.status_code == 429
    assert "Retry-After" in r.headers


def test_valid_key_window_is_independent_of_ip_window(client, monkeypatch):
    """The real shared key gets its own window: exhausting the anonymous
    IP window must not throttle a properly-keyed request. (Auth stays
    disabled here — auth.py captured its own API_KEY at import; only the
    rate limiter reads config.API_KEY live.)"""
    _set_limit(monkeypatch, 2)
    monkeypatch.setattr(config, "API_KEY", "real-secret")
    for _ in range(2):
        client.get("/items")                      # no key → IP window
    assert client.get("/items").status_code == 429
    r = client.get("/items", headers={"X-API-Key": "real-secret"})
    assert r.status_code != 429


def test_invalid_keys_do_not_earn_their_own_windows(client, monkeypatch):
    """The brute-force fix: every wrong guess is a DIFFERENT key value, but
    they must all share the guesser's IP window — under the old
    raw-header-as-identity scheme each guess got a fresh window and this
    test's final request would sail through."""
    _set_limit(monkeypatch, 2)
    monkeypatch.setattr(config, "API_KEY", "real-secret")
    for i in range(2):
        client.get("/items", headers={"X-API-Key": f"wrong-guess-{i}"})
    r = client.get("/items", headers={"X-API-Key": "wrong-guess-99"})
    assert r.status_code == 429


def test_falls_back_to_source_ip_when_no_api_key_sent(client, monkeypatch):
    _set_limit(monkeypatch, 2)
    for _ in range(2):
        client.get("/items")
    assert client.get("/items").status_code == 429
