"""One clock for everything the API emits.

The bug this exists to prevent, found 2026-07-30: `/build/rebuild/status`
reported `started_at` from `datetime.utcnow()` (naive UTC) while
`/maintenance` reported `since` from `datetime.now()` (naive LOCAL). Both
described the same rebuild, nine hours apart in the JSON.

Naive is the root cause. A timestamp with no offset is not wrong in Python —
it is wrong in the *browser*, where `new Date("2026-07-30T00:36:33")` is
parsed as LOCAL time. So naive-UTC values rendered nine hours early, and
the frontend had grown `new Date(x + "Z")` hacks at four call sites to patch
it one field at a time — which silently breaks the moment a field starts
carrying a real offset.

Rule: every datetime crossing the API boundary is **timezone-aware UTC**, so
its ISO form ends in `+00:00` and every client parses it unambiguously. Model
columns stay naive-UTC (changing them needs a migration and buys nothing);
`as_utc()` attaches the offset those values always implied.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from pydantic import PlainSerializer


def utc_now() -> datetime:
    """Timezone-aware now. Use instead of the deprecated `utcnow()`."""
    return datetime.now(timezone.utc)


def as_utc(dt: datetime | None) -> datetime | None:
    """Tag a naive datetime as UTC. Aware values pass through converted, so
    this is safe to apply to a column whose type may change later."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def as_utc_iso(dt: datetime | None) -> str | None:
    """ISO-8601 with an explicit `+00:00`, for hand-built dicts (the report
    payloads that are not Pydantic models)."""
    aware = as_utc(dt)
    return aware.isoformat() if aware is not None else None


# For Pydantic response models: `field: UtcDatetime | None = None` serializes
# a naive-UTC column as an offset-aware string without touching the DB.
UtcDatetime = Annotated[datetime, PlainSerializer(as_utc_iso, return_type=str,
                                                 when_used="json")]
