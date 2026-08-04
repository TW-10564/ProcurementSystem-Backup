"""Automatic recurring forecast generation.

Without this, nothing ever calls forecasting.generate() except a human
clicking "Recalculate" — which means planner_accuracy.py's PlannerSnapshot
archive (built to eventually prove whether this system's per-SKU forecasts
beat the planner's own) never accumulates either, since snapshots are only
written as a side effect of a run. This module is what turns "the archive
will fill in over time" into something actually happening in the background.

Runs in-process alongside the API (no external cron needed at this
deployment's scale). Recovers correctly across restarts: due-ness is
computed from the LAST RUN'S TIMESTAMP IN THE DB, not in-memory state, so
killing/restarting the server (routine in dev — see [[running-portal]] in
project memory) never resets the clock or causes runs to pile up.

Writes ONLY through forecasting.generate(), which itself writes only to
the app's own DB (Phase-1 read-only-against-NAS rule, unchanged).

On the same weekly cadence, this loop also re-ingests from the NAS
(rebuild_preserving.run(), which wraps build.run()'s full drop+rebuild with
translation logic that carries Adjustments/ForecastRun/AccuracyScore/
PlannerSnapshot history across it — see rebuild_preserving.py's docstring
for why a naive build.run() call here would silently erase them every week).

Rebuild attempts (this loop's own AND the "Rebuild from NAS" UI button's)
are recorded through rebuild_status.py, so a scheduled rebuild that fails
(e.g. NAS temporarily unreachable) is visible on the Forecast page, not
only in server stdout. A rebuild failure does NOT abort the cycle —
forecasting.generate() still runs against whatever data already exists
(build.py's own pre-flight check, added 2026-07-15, guarantees a failed
rebuild never leaves the DB in a half-wiped state, so "existing data" here
is always coherent) — a stale-but-working forecast beats no forecast at
all. Note this means is_due() (keyed off the last ForecastRun, not the
last successful rebuild) won't retry a failed rebuild until the next full
interval elapses, not the next poll — a known tradeoff, not a fix for
this session (see FIX_PLAN's existing note on retry/backoff being a
separate, lower-priority concern).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from .config import SCHEDULER_ENABLED, SCHEDULER_INTERVAL_HOURS, SCHEDULER_POLL_SECONDS
from .db import get_session
from .forecasting import generate, latest_run
from . import rebuild_preserving
from . import rebuild_status
from .advisory import order_actions as order_svc


def is_due(session, interval_hours: float = SCHEDULER_INTERVAL_HOURS) -> bool:
    """No prior run, or the last one is older than the configured interval."""
    run = latest_run(session)
    if run is None or run.created_at is None:
        return True
    return datetime.utcnow() - run.created_at >= timedelta(hours=interval_hours)


def run_if_due() -> dict | None:
    """One synchronous check-and-maybe-run cycle. Returns the run summary
    if it ran, None if it wasn't due yet (or a rebuild was already running
    — see rebuild_status.is_running(), avoids two concurrent
    rebuild_preserving.run() calls stepping on each other's DB access).
    Re-ingests from the NAS (history-preserving) before regenerating
    forecasts, so the weekly cycle actually picks up NAS changes instead
    of only recomputing against a stale DB. A rebuild failure is recorded
    (visible via /build/rebuild/status and /history) but does not abort
    forecast generation — see module docstring."""
    session = get_session()
    try:
        due = is_due(session)
    finally:
        session.close()
    if not due or rebuild_status.is_running():
        return None

    entry = rebuild_status.start("scheduled")
    rebuild_report = None
    try:
        rebuild_report = rebuild_preserving.run()
        rebuild_status.finish(entry, rebuild_report)
    except Exception as e:                          # noqa: BLE001
        rebuild_status.fail(entry, repr(e))
        print(f"[scheduler] rebuild failed, regenerating forecast from "
             f"existing data anyway: {e!r}")

    session = get_session()
    try:
        result = generate(session)
    finally:
        session.close()
    result["rebuild"] = rebuild_report
    return result


def check_order_alerts() -> int:
    """Every poll tick (not gated by is_due()'s weekly cadence — order dates
    arrive on their own schedule): alert once per order whose expected
    arrival date has been reached. See order_svc.raise_due_alerts for the
    dedup rule. The API also calls this on every Notifications/Dashboard
    load so alerts still appear when the scheduler is disabled for this
    deployment (ONEFCST_SCHEDULER_ENABLED defaults to 0) — this is the
    proactive half, so an alert can land even if nobody opens the app that
    day."""
    session = get_session()
    try:
        return order_svc.raise_due_alerts(session)
    finally:
        session.close()


async def loop(poll_seconds: float = SCHEDULER_POLL_SECONDS) -> None:
    print(f"[scheduler] started: interval={SCHEDULER_INTERVAL_HOURS}h "
         f"poll={poll_seconds}s")
    while True:
        try:
            result = await asyncio.to_thread(run_if_due)
            if result:
                overall = result["holdout"]["overall"]
                wape = f"{overall['wape']}%" if overall else "n/a"
                print(f"[scheduler] ran forecast #{result['run_id']}: "
                     f"{result['items_forecasted']} items, overall WAPE {wape}, "
                     f"{result['planner_snapshots_archived']} planner snapshots archived")
        except Exception as e:                      # noqa: BLE001
            print(f"[scheduler] run failed: {e!r}")
        try:
            n = await asyncio.to_thread(check_order_alerts)
            if n:
                print(f"[scheduler] raised {n} order-arrival alert(s)")
        except Exception as e:                      # noqa: BLE001
            print(f"[scheduler] order-alert check failed: {e!r}")
        await asyncio.sleep(poll_seconds)


def start() -> asyncio.Task | None:
    """Launch the background loop, or None if disabled via config."""
    if not SCHEDULER_ENABLED:
        print("[scheduler] disabled (ONEFCST_SCHEDULER_ENABLED=0)")
        return None
    return asyncio.create_task(loop())
