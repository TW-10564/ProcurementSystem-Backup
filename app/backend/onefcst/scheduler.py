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
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from .config import SCHEDULER_ENABLED, SCHEDULER_INTERVAL_HOURS, SCHEDULER_POLL_SECONDS
from .db import get_session
from .forecasting import generate, latest_run


def is_due(session, interval_hours: float = SCHEDULER_INTERVAL_HOURS) -> bool:
    """No prior run, or the last one is older than the configured interval."""
    run = latest_run(session)
    if run is None or run.created_at is None:
        return True
    return datetime.utcnow() - run.created_at >= timedelta(hours=interval_hours)


def run_if_due() -> dict | None:
    """One synchronous check-and-maybe-run cycle. Returns the run summary
    if it ran, None if it wasn't due yet. Safe to call repeatedly — the
    due-check and the run happen against the same session/transaction."""
    session = get_session()
    try:
        if not is_due(session):
            return None
        return generate(session)
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
        await asyncio.sleep(poll_seconds)


def start() -> asyncio.Task | None:
    """Launch the background loop, or None if disabled via config."""
    if not SCHEDULER_ENABLED:
        print("[scheduler] disabled (ONEFCST_SCHEDULER_ENABLED=0)")
        return None
    return asyncio.create_task(loop())
