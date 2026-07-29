"""Maintenance mode — a site-wide "we're working on this" banner/lock.

Now that the portal is reachable by everyone on the office network, work in
progress is visible to colleagues mid-change: a half-deployed frontend, a
rebuild that has the DB locked, a backend restart. Without a signal they
read that as "the system is broken". This flips one switch that every
browser picks up within ~20s and shows a clear "under development" screen
instead.

Deliberately stored in a FILE, not the database. Maintenance mode is most
needed exactly when the DB is unavailable — mid-rebuild it holds a write
lock and every query 500s, so a DB-backed flag would be unreadable at the
one moment it matters. A small JSON file has no such dependency.

State file: app/data/maintenance.json (gitignored with the rest of data/).
Absent file == maintenance off, so a fresh checkout is never accidentally
locked.

Toggle it from the terminal (no restart needed — the file is read per
request):

    python3 -m onefcst.maintenance on  "部材データの再構築中です"
    python3 -m onefcst.maintenance off
    python3 -m onefcst.maintenance status
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .config import DATA_DIR

STATE_PATH = Path(DATA_DIR) / "maintenance.json"

DEFAULT_MESSAGE_JA = "システム更新中です。まもなく復旧します。"
DEFAULT_MESSAGE_EN = "The system is being updated. It will be back shortly."


def get_state() -> dict:
    """Never raises: a corrupt or unreadable state file must not take the
    whole site down — it degrades to "not in maintenance"."""
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return {"enabled": False, "message": "", "since": None, "by": None}
    return {
        "enabled": bool(raw.get("enabled")),
        "message": raw.get("message") or "",
        "since": raw.get("since"),
        "by": raw.get("by"),
    }


def set_state(enabled: bool, message: str = "", by: str | None = None) -> dict:
    state = {
        "enabled": bool(enabled),
        "message": (message or "").strip(),
        # keep the original start time across message edits, so the banner
        # can honestly say how long this has been going on
        "since": (get_state().get("since") if enabled and get_state()["enabled"]
                  else (datetime.now().isoformat() if enabled else None)),
        "by": by,
    }
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)          # atomic: readers never see a half file
    return state


def _cli() -> int:
    import sys
    args = sys.argv[1:]
    cmd = (args[0].lower() if args else "status")
    if cmd in ("on", "enable"):
        msg = args[1] if len(args) > 1 else ""
        s = set_state(True, msg, by="cli")
        print(f"maintenance ON  message={s['message'] or '(default)'}  since={s['since']}")
    elif cmd in ("off", "disable"):
        set_state(False, "", by="cli")
        print("maintenance OFF — the site is live again")
    elif cmd == "status":
        s = get_state()
        print(f"maintenance {'ON' if s['enabled'] else 'OFF'}"
              + (f"  message={s['message'] or '(default)'}  since={s['since']}"
                 if s["enabled"] else ""))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
