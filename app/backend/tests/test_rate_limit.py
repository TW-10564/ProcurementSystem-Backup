"""api/rate_limit.py — basic per-client (API-key-or-IP) request cap.
Disabled by default (RATE_LIMIT_PER_MINUTE=0, same dev-friendly stance as
API_KEY in auth.py); enabled here per-test via monkeypatching the module's
own config reference, since RateLimitMiddleware reads it once via the
imported name — see conftest-free approach below."""
import pytest
from fastapi.testclient import TestClient

from onefcst.api import rate_limit as rate_limit_mod
from onefcst.api.main import app


@pytest.fixture
def client():
    return TestClient(app)


# The RateLimitMiddleware instance (and its _windows dict) is built once
# for the whole app and reused across every test in this file — tests
# below each use a distinct client_id (a unique X-API-Key value) so one
# test's request count never bleeds into another's.


def _set_limit(monkeypatch, value):
    monkeypatch.setattr(rate_limit_mod, "RATE_LIMIT_PER_MINUTE", value)


def test_disabled_by_default_allows_unlimited_requests(client, monkeypatch):
    _set_limit(monkeypatch, 0)
    for _ in range(20):
        r = client.get("/health")
        assert r.status_code == 200


def test_health_is_always_exempt_even_when_enabled(client, monkeypatch):
    _set_limit(monkeypatch, 1)
    for _ in range(10):
        r = client.get("/health")
        assert r.status_code == 200


def test_exceeding_the_limit_returns_429_with_retry_after(client, monkeypatch):
    _set_limit(monkeypatch, 3)
    for _ in range(3):
        r = client.get("/items", headers={"X-API-Key": "test-client-a"})
        assert r.status_code != 429
    r = client.get("/items", headers={"X-API-Key": "test-client-a"})
    assert r.status_code == 429
    assert "Retry-After" in r.headers


def test_limit_is_tracked_independently_per_api_key(client, monkeypatch):
    _set_limit(monkeypatch, 2)
    for _ in range(2):
        assert client.get("/items", headers={"X-API-Key": "client-a"}).status_code != 429
    # a different key must not be affected by client-a's usage
    r = client.get("/items", headers={"X-API-Key": "client-b"})
    assert r.status_code != 429


def test_falls_back_to_source_ip_when_no_api_key_sent(client, monkeypatch):
    _set_limit(monkeypatch, 2)
    for _ in range(2):
        client.get("/items")
    r = client.get("/items")
    assert r.status_code == 429
