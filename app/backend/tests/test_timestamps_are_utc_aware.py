"""Every timestamp the API emits must be timezone-aware UTC.

The bug (2026-07-30, reported from the running portal): a manual rebuild
reported `started_at` "2026-07-30T00:36:33" from `/build/rebuild/status` while
`/maintenance` reported `since` "2026-07-30T09:37:09" for the SAME rebuild —
one naive UTC, one naive LOCAL, nine hours apart. Whichever the UI rendered,
one was wrong to the user.

Naive is the real defect: browsers parse an offset-less ISO string as LOCAL
time, so naive-UTC values displayed nine hours early, and the frontend had
grown four `new Date(x + "Z")` patches to compensate one field at a time.
`test_the_two_endpoints_agree_on_the_clock` is the regression test for the
reported symptom; the rest pin the contract that makes those patches
unnecessary (and wrong — "...+00:00Z" is an Invalid Date).
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import onefcst.rebuild_preserving as rebuild_preserving_mod
import onefcst.rebuild_status as rebuild_status_mod
from onefcst import maintenance
from onefcst.api.main import app
from onefcst.api.schemas import AdjustmentOut
from onefcst.models import AuditLog
from onefcst.timeutil import as_utc, as_utc_iso, utc_now


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Never touch the developer's real maintenance.json or rebuild history —
    flipping the real flag would gate the live portal from a test run."""
    monkeypatch.setattr(maintenance, "STATE_PATH", tmp_path / "maintenance.json")
    rebuild_status_mod._history.clear()
    rebuild_status_mod._next_id = 1
    yield
    rebuild_status_mod._history.clear()
    rebuild_status_mod._next_id = 1


def _parsed(value: str) -> datetime:
    """Parse the way a browser would have to: no manual offset patching."""
    dt = datetime.fromisoformat(value)
    assert dt.tzinfo is not None, f"{value!r} has no timezone offset"
    return dt


# --------------------------------------------------------------- the symptom
def test_the_two_endpoints_agree_on_the_clock(client, monkeypatch):
    """The reported bug: same rebuild, two endpoints, nine hours apart."""
    monkeypatch.setattr(rebuild_preserving_mod, "run", lambda: {"ok": True})
    entry = rebuild_status_mod.start("manual")
    maintenance.set_state(True, "データ再構築中です。", by="rebuild")

    started = _parsed(client.get("/build/rebuild/status").json()["started_at"])
    since = _parsed(client.get("/maintenance").json()["since"])

    assert abs(since - started) < timedelta(minutes=1), (
        f"same rebuild reported {started.isoformat()} and {since.isoformat()}")
    assert entry["started_at"].endswith("+00:00")


# ------------------------------------------------------------- the contract
def test_rebuild_status_timestamps_are_aware_utc():
    entry = rebuild_status_mod.start("scheduled")
    rebuild_status_mod.finish(entry, {"ok": True})
    for field in ("started_at", "finished_at"):
        assert _parsed(entry[field]).utcoffset() == timedelta(0), field


def test_failed_rebuild_also_stamps_aware_utc():
    entry = rebuild_status_mod.start("manual")
    rebuild_status_mod.fail(entry, "boom")
    assert _parsed(entry["finished_at"]).utcoffset() == timedelta(0)


def test_maintenance_since_is_aware_utc():
    state = maintenance.set_state(True, "更新中", by="cli")
    assert _parsed(state["since"]).utcoffset() == timedelta(0)


def test_maintenance_keeps_the_original_since_across_message_edits():
    """Pre-existing behaviour the timezone change must not break: the banner
    says how long this has been going on, so an edited message keeps t0."""
    first = maintenance.set_state(True, "one", by="cli")["since"]
    assert maintenance.set_state(True, "two", by="cli")["since"] == first


def test_health_time_is_aware_utc(client):
    assert _parsed(client.get("/health").json()["time"]).utcoffset() == timedelta(0)


def test_notifications_feed_timestamp_is_aware_utc(client, isolated_api_db):
    """The regression this reported (2026-08-04): the Notifications/audit-log
    page showed adjustment times nine hours early because notifications.py's
    `_row` still emitted `r.at.isoformat()` — naive — while the rest of the
    sweep above had already moved to `as_utc_iso`. The browser parsed the
    offset-less string as local time on top of a value that was already UTC,
    so every event displayed nine hours in the past."""
    isolated_api_db.add(AuditLog(who="tanaka", what="adjustment#1 recorded (sku)",
                                 before="{}", after="{}"))
    isolated_api_db.commit()

    items = client.get("/notifications").json()["items"]
    assert items, "expected the seeded AuditLog row back from the feed"
    assert _parsed(items[0]["at"]).utcoffset() == timedelta(0)


def test_response_models_serialize_naive_columns_with_an_offset():
    """The DB columns stay naive UTC (no migration); the schema attaches the
    offset those values always implied."""
    naive = datetime(2026, 7, 30, 0, 36, 33)
    out = AdjustmentOut(id=1, item_id=2, month="2026-08-01", old_value=1.0,
                        new_value=2.0, scope="month", reason="r", author="a",
                        created_at=naive)
    assert out.model_dump(mode="json")["created_at"] == "2026-07-30T00:36:33+00:00"


# ---------------------------------------------------------------- the helper
def test_as_utc_is_idempotent_on_aware_values():
    """Applied twice (or to a column that later becomes aware) it must not
    shift the instant — otherwise the fix would itself introduce a skew."""
    aware = datetime(2026, 7, 30, 9, 36, 33, tzinfo=timezone(timedelta(hours=9)))
    assert as_utc(as_utc(aware)) == aware
    assert as_utc(aware).utcoffset() == timedelta(0)
    assert as_utc_iso(aware) == "2026-07-30T00:36:33+00:00"


def test_as_utc_passes_none_through():
    assert as_utc(None) is None
    assert as_utc_iso(None) is None


def test_utc_now_is_aware():
    assert utc_now().utcoffset() == timedelta(0)
