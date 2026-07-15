"""POST /build/rebuild + GET /build/rebuild/status + GET /build/rebuild/history
— the UI's "rebuild from NAS" trigger, added 2026-07-15, later unified
with scheduler.py's automatic weekly rebuilds (both go through
rebuild_status.py — see that module's docstring for why). Runs
rebuild_preserving.run() (never bare build.run()) in a background thread
so a real multi-minute rebuild doesn't block the request; the frontend
polls status instead.

rebuild_preserving.run() is ALWAYS monkeypatched in these tests — it calls
build.run(), which drops every table in whatever DB api.main's `db`
dependency points at before trying to read the NAS. Never let that run for
real against a developer's live onefcst.db in a test."""
import asyncio

import pytest
from fastapi.testclient import TestClient

import onefcst.rebuild_preserving as rebuild_preserving_mod
import onefcst.rebuild_status as rebuild_status_mod
from onefcst.api.main import app, _run_rebuild_in_background


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def reset_state():
    rebuild_status_mod._history.clear()
    rebuild_status_mod._next_id = 1
    yield
    rebuild_status_mod._history.clear()
    rebuild_status_mod._next_id = 1


def test_trigger_returns_started_and_records_a_manual_attempt(client, monkeypatch):
    """TestClient's event loop may run the background task to completion
    before the response even returns (observed: state can already be
    "done" by the time control comes back) — so this only asserts what's
    guaranteed: the immediate response, and that an attempt was recorded.
    test_background_run_updates_state_to_done_with_the_real_report_on_success
    covers the "running" -> "done" transition directly and deterministically."""
    monkeypatch.setattr(rebuild_preserving_mod, "run", lambda: {"ok": True})
    r = client.post("/build/rebuild")
    assert r.status_code == 200
    assert r.json() == {"status": "started"}
    current = rebuild_status_mod.current()
    assert current is not None
    assert current["trigger"] == "manual"
    assert current["status"] in ("running", "done")
    assert current["started_at"] is not None


def test_concurrent_trigger_is_rejected_with_409(client, monkeypatch):
    monkeypatch.setattr(rebuild_preserving_mod, "run", lambda: {"ok": True})
    rebuild_status_mod.start("manual")   # simulates one already in flight
    r = client.post("/build/rebuild")
    assert r.status_code == 409


def test_status_endpoint_reflects_current_state(client):
    entry = rebuild_status_mod.start("manual")
    rebuild_status_mod.finish(entry, {"history_preserved": {"actuals": 5}})
    r = client.get("/build/rebuild/status")
    assert r.status_code == 200
    assert r.json()["status"] == "done"
    assert r.json()["report"]["history_preserved"]["actuals"] == 5


def test_status_endpoint_is_idle_when_nothing_has_ever_run(client):
    r = client.get("/build/rebuild/status")
    assert r.status_code == 200
    assert r.json()["status"] == "idle"


def test_history_endpoint_returns_both_manual_and_scheduled_attempts(client):
    e1 = rebuild_status_mod.start("scheduled")
    rebuild_status_mod.finish(e1, {"ok": True})
    e2 = rebuild_status_mod.start("manual")
    rebuild_status_mod.fail(e2, "NAS unreachable")

    r = client.get("/build/rebuild/history")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 2
    assert body[0]["trigger"] == "manual"      # most recent first
    assert body[0]["status"] == "error"
    assert body[1]["trigger"] == "scheduled"
    assert body[1]["status"] == "done"


def test_background_run_updates_state_to_done_with_the_real_report_on_success(monkeypatch):
    fake_report = {"history_preserved": {"actuals": 12, "adjustments": 3}}
    monkeypatch.setattr(rebuild_preserving_mod, "run", lambda: fake_report)

    entry = rebuild_status_mod.start("manual")
    asyncio.run(_run_rebuild_in_background(entry))

    assert entry["status"] == "done"
    assert entry["report"] == fake_report
    assert entry["error"] is None
    assert entry["finished_at"] is not None


def test_background_run_updates_state_to_error_without_crashing_on_failure(monkeypatch):
    def _boom():
        raise FileNotFoundError("NAS not mounted")
    monkeypatch.setattr(rebuild_preserving_mod, "run", _boom)

    entry = rebuild_status_mod.start("manual")
    asyncio.run(_run_rebuild_in_background(entry))   # must not raise

    assert entry["status"] == "error"
    assert "NAS not mounted" in entry["error"]
    assert entry["report"] is None
