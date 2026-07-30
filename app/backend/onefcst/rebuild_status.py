"""In-memory record of rebuild attempts — manual "Rebuild from NAS" button
clicks AND scheduler.py's automatic weekly cycle — so a scheduled failure
in production is exactly as visible in the UI as a manual one.

Before this module existed, scheduler.py's own rebuild attempts only ever
printed to stdout (`print(f"[scheduler] run failed: ...")`) — invisible to
anyone not tailing server logs. api/main.py separately tracked ONLY the
latest manually-triggered attempt in a local dict. Both now go through
this one shared tracker.

In-memory only (matches api/main.py's pre-existing `_report_cache`
pattern) — resets on process restart. Acceptable at this deployment's
scale (single process, no external state store); MAX_HISTORY is sized
generously enough that a restart losing older entries isn't costly (each
entry is just a status record, not the rebuild's actual effect — that's
durably reflected in the DB itself either way)."""
from __future__ import annotations

from .timeutil import utc_now

MAX_HISTORY = 20

_history: list[dict] = []
_next_id = 1


def start(trigger: str) -> dict:
    """trigger: "manual" (the UI button) or "scheduled" (scheduler.py's
    weekly loop). Returns the new entry — callers mutate it in place via
    finish()/fail() (it's the same dict object held in _history), so
    there's no separate id-lookup path to keep in sync."""
    global _next_id
    entry = {"id": _next_id, "trigger": trigger, "status": "running",
             "started_at": utc_now().isoformat(), "finished_at": None,
             "report": None, "error": None}
    _next_id += 1
    _history.insert(0, entry)
    del _history[MAX_HISTORY:]
    return entry


def finish(entry: dict, report: dict) -> None:
    entry["status"] = "done"
    entry["finished_at"] = utc_now().isoformat()
    entry["report"] = report


def fail(entry: dict, error: str) -> None:
    entry["status"] = "error"
    entry["finished_at"] = utc_now().isoformat()
    entry["error"] = error


def current() -> dict | None:
    """The most recent attempt (running, done, or errored) across BOTH
    triggers, or None if nothing has run yet this process."""
    return _history[0] if _history else None


def is_running() -> bool:
    """True if a rebuild (either trigger) is in flight right now — used to
    stop a manual click and the scheduler's own cycle from ever running
    rebuild_preserving.run() concurrently with itself (it assumes
    exclusive DB access: drop_all + snapshot/restore)."""
    c = current()
    return c is not None and c["status"] == "running"


def history(limit: int = MAX_HISTORY) -> list[dict]:
    return _history[:limit]
