"""Unit tests for the minimal shared-key API gate (api/auth.py)."""
import pytest
from fastapi.testclient import TestClient

from onefcst.api import auth as auth_mod
from onefcst.api.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_disabled_by_default_allows_requests(client, monkeypatch):
    monkeypatch.setattr(auth_mod, "API_KEY", "")
    r = client.get("/health")
    assert r.status_code == 200


def test_enabled_rejects_missing_or_wrong_key(client, monkeypatch):
    monkeypatch.setattr(auth_mod, "API_KEY", "secret123")
    r = client.get("/items")
    assert r.status_code == 401
    r = client.get("/items", headers={"X-API-Key": "wrong"})
    assert r.status_code == 401


def test_enabled_accepts_correct_key(client, monkeypatch):
    monkeypatch.setattr(auth_mod, "API_KEY", "secret123")
    r = client.get("/items", headers={"X-API-Key": "secret123"})
    assert r.status_code == 200


def test_health_always_exempt_even_when_enabled(client, monkeypatch):
    monkeypatch.setattr(auth_mod, "API_KEY", "secret123")
    r = client.get("/health")
    assert r.status_code == 200
