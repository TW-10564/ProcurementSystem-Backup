"""onefcst/rebuild_status.py — the shared tracker unifying manual and
scheduled rebuild attempts, added 2026-07-15 so a scheduled failure is as
visible in the UI as a manual one."""
import pytest

import onefcst.rebuild_status as rebuild_status_mod
from onefcst.rebuild_status import start, finish, fail, current, is_running, history


@pytest.fixture(autouse=True)
def reset_state():
    rebuild_status_mod._history.clear()
    rebuild_status_mod._next_id = 1
    yield
    rebuild_status_mod._history.clear()
    rebuild_status_mod._next_id = 1


def test_current_is_none_before_anything_ever_runs():
    assert current() is None
    assert is_running() is False


def test_start_records_a_running_entry_with_the_given_trigger():
    entry = start("scheduled")
    assert entry["trigger"] == "scheduled"
    assert entry["status"] == "running"
    assert entry["started_at"] is not None
    assert current() is entry
    assert is_running() is True


def test_finish_marks_done_and_stores_the_report():
    entry = start("manual")
    finish(entry, {"resolved": 42})
    assert entry["status"] == "done"
    assert entry["report"] == {"resolved": 42}
    assert entry["finished_at"] is not None
    assert is_running() is False


def test_fail_marks_error_and_stores_the_message():
    entry = start("manual")
    fail(entry, "NAS unreachable")
    assert entry["status"] == "error"
    assert entry["error"] == "NAS unreachable"
    assert is_running() is False


def test_history_returns_most_recent_first():
    e1 = start("scheduled")
    finish(e1, {})
    e2 = start("manual")
    finish(e2, {})
    hist = history()
    assert hist[0] is e2
    assert hist[1] is e1


def test_history_is_capped_at_max_history():
    for i in range(rebuild_status_mod.MAX_HISTORY + 5):
        finish(start("manual"), {"i": i})
    assert len(history()) == rebuild_status_mod.MAX_HISTORY
    # oldest entries were evicted — most recent MAX_HISTORY survive
    assert history()[0]["report"]["i"] == rebuild_status_mod.MAX_HISTORY + 4


def test_history_limit_param_truncates():
    for _ in range(5):
        finish(start("manual"), {})
    assert len(history(limit=2)) == 2


def test_manual_and_scheduled_attempts_both_tracked_in_one_history():
    finish(start("scheduled"), {})
    finish(start("manual"), {})
    triggers = {e["trigger"] for e in history()}
    assert triggers == {"scheduled", "manual"}
