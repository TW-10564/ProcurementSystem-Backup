"""Shared test setup.

The developer's real app/backend/.env (auto-loaded by config.py) may set
ONEFCST_API_KEY / rate limiting for the deployed server — which would make
every TestClient request without a header 401 and turn the suite red for
environment reasons, not code reasons (happened 2026-07-23 when the key was
first added to .env). Neutralize both for every test; tests that exercise
auth explicitly (test_auth.py) monkeypatch their own key on top.
"""
import pytest

from onefcst import config
from onefcst.api import auth as auth_mod
from onefcst.api import rate_limit as rate_limit_mod


@pytest.fixture(autouse=True)
def _no_env_auth(monkeypatch):
    monkeypatch.setattr(auth_mod, "API_KEY", "")
    monkeypatch.setattr(config, "API_KEY", "")
    monkeypatch.setattr(rate_limit_mod, "RATE_LIMIT_PER_MINUTE", 0)
