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
