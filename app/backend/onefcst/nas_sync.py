"""Refreshes ONE_DIR's local file cache from the real NAS (Z:) via Windows
interop, since this WSL session cannot mount Z: directly.

WHY this exists (found 2026-07-17): WSL auto-mounts fixed local Windows
disks (C:, D:, ...) through its drvfs driver, but NOT drive letters
connected via `net use` — Z: is genuinely connected on the Windows side
(confirmed via `net use`, Status OK) but never appears under /mnt/z, and
mounting it manually needs root, which this environment doesn't have.
Windows interop (running powershell.exe/cmd.exe from this WSL shell) DOES
work, though, so the practical fix is: robocopy-mirror Z:'s contents onto
C: (which IS mounted, under /mnt/c) and read from there. See ONE_SOURCE_DIR
in .env — it already points at this local cache directory, not /mnt/z.

This is a SNAPSHOT refresh, not a live mount: the cache is only as fresh
as the last time this ran. Called automatically at the start of
rebuild_preserving.run() (both the manual "rebuild" button and the weekly
scheduler go through that one function), so every rebuild gets a fresh
pull first — no separate step for a human to remember.

Failure here is NON-FATAL to the rebuild: if Windows interop isn't
available in some other environment this code runs in (a real cloud
sandbox, a different WSL setup with no NAS mapped, etc.), refresh_from_nas
returns a clear "skipped" result instead of raising, and
build.py's own _check_required_sources() will fail loud and safe
afterward if the (now-stale-or-missing) local files aren't there —
exactly the same failure mode as before this module existed, not a new
failure surface."""
from __future__ import annotations

import base64
import subprocess
import tempfile
from pathlib import Path

from .config import ONE_DIR

# The NAS root, as seen from the Windows side — Z:\ itself already IS the
# ...\ONE folder (confirmed 2026-07-17: ONE計画.xlsm sits directly at
# Z:\ONE計画.xlsm, not in a subfolder), matching ONE_DIR's own file layout.
NAS_SOURCE_WIN = "Z:\\"


def _to_windows_path(p: Path) -> str:
    """WSL path -> Windows path, via wslpath (no extra dependency)."""
    result = subprocess.run(["wslpath", "-w", str(p)], capture_output=True,
                            text=True, check=True)
    return result.stdout.strip()


def refresh_from_nas(timeout_seconds: int = 180) -> dict:
    """Robocopy-mirrors NAS_SOURCE_WIN onto ONE_DIR via Windows interop.
    Returns {"status": "ok"|"skipped"|"error", ...} — never raises, so a
    caller can decide whether to proceed with whatever's already cached
    locally (build.py's own required-source check is the real safety net
    either way)."""
    try:
        dest_win = _to_windows_path(ONE_DIR)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        return {"status": "skipped", "reason": f"wslpath unavailable: {e!r}"}

    # /XO: only copy files newer than what's already at the destination —
    # cheap on repeat runs, and never deletes local files Z: doesn't have
    # (a stale-but-present file beats a hole in the required set).
    ps_script = (
        f'$dest = "{dest_win}"\n'
        f'New-Item -ItemType Directory -Force -Path $dest | Out-Null\n'
        f'$before = (Get-ChildItem $dest -File | Measure-Object).Count\n'
        f'robocopy "{NAS_SOURCE_WIN}" $dest /XO /R:1 /W:1 /NDL /NJH | Out-Null\n'
        f'$after = (Get-ChildItem $dest -File | Measure-Object).Count\n'
        f'Write-Output "FILES_AFTER=$after"\n'
    )
    encoded = base64.b64encode(ps_script.encode("utf-16-le")).decode("ascii")

    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-EncodedCommand", encoded],
            capture_output=True, text=True, timeout=timeout_seconds)
    except FileNotFoundError:
        return {"status": "skipped",
               "reason": "powershell.exe not available — not a WSL-with-Windows-interop session"}
    except subprocess.TimeoutExpired:
        return {"status": "error", "reason": f"timed out after {timeout_seconds}s"}

    if result.returncode != 0:
        return {"status": "error", "reason": result.stderr.strip()[-2000:] or "unknown error",
               "returncode": result.returncode}

    files_after = None
    for line in result.stdout.splitlines():
        if line.startswith("FILES_AFTER="):
            try:
                files_after = int(line.split("=", 1)[1])
            except ValueError:
                pass

    return {"status": "ok", "one_dir": str(ONE_DIR), "files_in_cache": files_after}


if __name__ == "__main__":
    import json
    print(json.dumps(refresh_from_nas(), indent=2, ensure_ascii=False))
