"""Unit + integration tests for the automatic-recurring-forecast scheduler.

is_due() is pure (given a fake run object) so it's tested without a DB;
run_if_due() is tested against the real onefcst.db to prove it respects
that due-ness check end to end (skipped like the other DB-backed tests
when the DB hasn't been built)."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from onefcst.config import DB_PATH
from onefcst.db import get_session
from onefcst import scheduler


class FakeSession:
    def __init__(self, run):
        self._run = run


def test_is_due_true_when_no_prior_run():
    class Session:
        def query(self, *a, **k):
            return self
        def order_by(self, *a, **k):
            return self
        def first(self):
            return None
    assert scheduler.is_due(Session()) is True


def _session_with_run(created_at):
    run = SimpleNamespace(created_at=created_at)
    class Session:
        def query(self, *a, **k):
            return self
        def order_by(self, *a, **k):
            return self
        def first(self):
            return run
    return Session()


def test_is_due_false_within_interval():
    recent = datetime.utcnow() - timedelta(hours=1)
    assert scheduler.is_due(_session_with_run(recent), interval_hours=24) is False


def test_is_due_true_once_interval_elapsed():
    old = datetime.utcnow() - timedelta(hours=25)
    assert scheduler.is_due(_session_with_run(old), interval_hours=24) is True


def test_is_due_boundary_is_inclusive():
    exact = datetime.utcnow() - timedelta(hours=24)
    assert scheduler.is_due(_session_with_run(exact), interval_hours=24) is True


@pytest.mark.skipif(not DB_PATH.exists(), reason="onefcst.db not built")
def test_run_if_due_skips_when_a_recent_run_exists():
    # this session's earlier manual runs are recent (minutes old), well
    # inside the default 24h interval — a real end-to-end "not due" check
    assert scheduler.run_if_due() is None


# ------------------ run_if_due's rebuild_status integration (isolated) ------
# These fully monkeypatch is_due/get_session/rebuild_preserving.run/generate
# so they never touch a real DB or NAS — see rebuild_status.py's docstring
# for why scheduler-triggered rebuilds must be visible the same way manual
# ones are.
import onefcst.rebuild_status as rebuild_status_mod


class _NoopSession:
    def close(self):
        pass


@pytest.fixture(autouse=True)
def reset_rebuild_status():
    rebuild_status_mod._history.clear()
    rebuild_status_mod._next_id = 1
    yield
    rebuild_status_mod._history.clear()
    rebuild_status_mod._next_id = 1


def test_run_if_due_records_a_scheduled_attempt_on_success(monkeypatch):
    monkeypatch.setattr(scheduler, "get_session", lambda: _NoopSession())
    monkeypatch.setattr(scheduler, "is_due", lambda session: True)
    monkeypatch.setattr(scheduler.rebuild_preserving, "run", lambda: {"ok": True})
    monkeypatch.setattr(scheduler, "generate", lambda session: {"run_id": 1})

    result = scheduler.run_if_due()

    assert result == {"run_id": 1, "rebuild": {"ok": True}}
    entry = rebuild_status_mod.current()
    assert entry["trigger"] == "scheduled"
    assert entry["status"] == "done"
    assert entry["report"] == {"ok": True}


def test_run_if_due_still_generates_a_forecast_when_the_rebuild_fails(monkeypatch):
    """A NAS blip must not stop forecasting from running against whatever
    data already exists (build.py's pre-flight check guarantees a failed
    rebuild never leaves the DB half-wiped) — a stale-but-working forecast
    beats none at all. The failure itself must still be recorded."""
    def _boom():
        raise FileNotFoundError("NAS not mounted")
    monkeypatch.setattr(scheduler, "get_session", lambda: _NoopSession())
    monkeypatch.setattr(scheduler, "is_due", lambda session: True)
    monkeypatch.setattr(scheduler.rebuild_preserving, "run", _boom)
    monkeypatch.setattr(scheduler, "generate", lambda session: {"run_id": 2})

    result = scheduler.run_if_due()

    assert result == {"run_id": 2, "rebuild": None}
    entry = rebuild_status_mod.current()
    assert entry["trigger"] == "scheduled"
    assert entry["status"] == "error"
    assert "NAS not mounted" in entry["error"]


def test_run_if_due_skips_when_a_rebuild_is_already_running(monkeypatch):
    monkeypatch.setattr(scheduler, "get_session", lambda: _NoopSession())
    monkeypatch.setattr(scheduler, "is_due", lambda session: True)
    rebuild_status_mod.start("manual")   # simulates the UI button mid-rebuild

    assert scheduler.run_if_due() is None
    assert len(rebuild_status_mod.history()) == 1   # no second attempt started


def test_run_if_due_does_nothing_when_not_due(monkeypatch):
    monkeypatch.setattr(scheduler, "get_session", lambda: _NoopSession())
    monkeypatch.setattr(scheduler, "is_due", lambda session: False)

    assert scheduler.run_if_due() is None
    assert rebuild_status_mod.current() is None
