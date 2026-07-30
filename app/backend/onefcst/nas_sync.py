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
import os
import subprocess
import sys
from pathlib import Path

from .config import (NAS_PLAN_CACHE, NAS_PLAN_UNC, NAS_ZM13_CACHE,
                     NAS_ZM13_UNC, NAS_ZP21_CACHE, NAS_ZP21_UNC, ONE_DIR)

# The NAS root, as seen from the Windows side — Z:\ itself already IS the
# ...\ONE folder (confirmed 2026-07-17: ONE計画.xlsm sits directly at
# Z:\ONE計画.xlsm, not in a subfolder), matching ONE_DIR's own file layout.
NAS_SOURCE_WIN = "Z:\\"

# 内田さん's weekly SAP drop folders (agreed 2026-07-27) — addressed by UNC
# directly, NOT via Z:, because they sit under 購買本部\【通常業務】\ONE on
# the share. Each is mirrored into its SAP_DIR cache subfolder; ingestion's
# content-based discovery (sap_stock_history / sap_zp21 rglob SAP_DIR) picks
# the files up from there, and both parsers de-dupe by date/week, so overlap
# with older manually-placed copies is harmless.
# "plan" is 企画部's ①②③④ drop (agreed 2026-07-30) — same mechanism, but its
# destination is PLANNING_DIR itself rather than a subfolder, since
# config.planning_file() globs that directory by circled-number prefix.
# robocopy /XO never purges, so the hand-placed files already there survive
# until the NAS copy is newer, and a folder that does not exist on the share
# yet simply copies nothing.
SUBFOLDER_SYNC_SOURCES = [
    ("zm13", NAS_ZM13_UNC, NAS_ZM13_CACHE),
    ("zp21", NAS_ZP21_UNC, NAS_ZP21_CACHE),
    ("plan", NAS_PLAN_UNC, NAS_PLAN_CACHE),
]

# Kept as an alias: the name shipped in the 2026-07-27 sync and reads as
# SAP-only, which the planning entry above no longer is.
SAP_SYNC_SOURCES = SUBFOLDER_SYNC_SOURCES


def _to_windows_path(p: Path) -> str:
    """WSL path -> Windows path, via wslpath (no extra dependency)."""
    result = subprocess.run(["wslpath", "-w", str(p)], capture_output=True,
                            text=True, check=True)
    return result.stdout.strip()


def _mount_mode_result() -> dict:
    """Result for deployments where the NAS is mounted by the OS (the DGX:
    a systemd .mount unit doing cifs, bind-mounted read-only into the
    container) rather than mirrored to a local cache.

    There is nothing to copy in that setup — ONE_SOURCE_DIR already points
    straight at the live share — so this is a legitimate no-op. It exists as
    an EXPLICIT branch because the alternative is the silent
    "wslpath unavailable" skip below, which is indistinguishable from a
    genuinely broken sync and would send someone hunting a non-problem.

    Still reports whether the mount actually has files: an empty or absent
    mount point is the realistic failure here (share not mounted, credentials
    expired, host rebooted before the unit came up), and it should be visible
    in the rebuild report rather than only surfacing later as a confusing
    "missing source file" from _check_required_sources().
    """
    present = ONE_DIR.is_dir()
    n = len(list(ONE_DIR.glob("*"))) if present else 0
    if not present or n == 0:
        return {"status": "error", "mode": "mount", "one_dir": str(ONE_DIR),
                "files_in_cache": n,
                "reason": (f"NAS mount {ONE_DIR} is "
                           f"{'missing' if not present else 'empty'} — check the "
                           f"cifs mount on the host (systemctl status "
                           f"'mnt-nas-*.mount') and that credentials are valid")}
    return {"status": "ok", "mode": "mount", "one_dir": str(ONE_DIR),
            "files_in_cache": n,
            "reason": "NAS mounted directly by the OS — no sync needed"}


def refresh_from_nas(timeout_seconds: int = 180) -> dict:
    """Refresh the ONE source files, by whichever mechanism this host has.

    Two supported modes:
      * ONEFCST_NAS_MODE=mount — the DGX/container deployment. The share is
        mounted by the OS and ONE_SOURCE_DIR points at it; nothing to copy.
      * default — the WSL dev box. Robocopy-mirrors NAS_SOURCE_WIN onto
        ONE_DIR through Windows interop, because WSL cannot mount a
        net-use'd drive letter (see the module docstring).

    Returns {"status": "ok"|"skipped"|"error", ...} — never raises, so a
    caller can decide whether to proceed with whatever's already cached
    locally (build.py's own required-source check is the real safety net
    either way)."""
    if os.environ.get("ONEFCST_NAS_MODE", "").lower() == "mount":
        return _mount_mode_result()

    try:
        dest_win = _to_windows_path(ONE_DIR)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        # Reached on any non-WSL Linux host that forgot ONEFCST_NAS_MODE.
        # Name that explicitly — the old message just said "wslpath
        # unavailable", which reads like a broken dev box rather than a
        # missing deployment setting.
        if not sys.platform.startswith("win") and not Path("/proc/sys/fs/binfmt_misc/WSLInterop").exists():
            return {"status": "skipped",
                    "reason": ("no Windows interop on this host. If the NAS is "
                               "mounted by the OS (DGX/container), set "
                               "ONEFCST_NAS_MODE=mount so this is a deliberate "
                               "no-op instead of a silent skip.")}
        return {"status": "skipped", "reason": f"wslpath unavailable: {e!r}"}

    sap_dests = {}
    for tag, _unc, cache in SAP_SYNC_SOURCES:
        try:
            sap_dests[tag] = _to_windows_path(cache)
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

    # /XO: only copy files newer than what's already at the destination —
    # cheap on repeat runs, and never deletes local files Z: doesn't have
    # (a stale-but-present file beats a hole in the required set).
    # $LASTEXITCODE is captured per robocopy and reported back. Without it this
    # function returned {"status": "ok"} whether or not anything was actually
    # copied — powershell.exe exits 0 as long as the SCRIPT ran, so an
    # unreachable share, expired credentials or a permission change all looked
    # like a clean sync over stale files. Found 2026-07-30 while verifying a
    # rebuild by hand against the share; the DGX path (mount mode) already
    # errored on a missing/empty mount, this is the dev path catching up.
    ps_script = (
        f'$dest = "{dest_win}"\n'
        f'New-Item -ItemType Directory -Force -Path $dest | Out-Null\n'
        f'$before = (Get-ChildItem $dest -File | Measure-Object).Count\n'
        f'robocopy "{NAS_SOURCE_WIN}" $dest /XO /R:1 /W:1 /NDL /NJH | Out-Null\n'
        f'Write-Output "ONE_RC=$LASTEXITCODE"\n'
        f'$after = (Get-ChildItem $dest -File | Measure-Object).Count\n'
        f'Write-Output "FILES_AFTER=$after"\n'
    )
    for tag, unc, _cache in SUBFOLDER_SYNC_SOURCES:
        dw = sap_dests.get(tag)
        if dw is None:
            continue
        ps_script += (
            f'$d = "{dw}"\n'
            f'New-Item -ItemType Directory -Force -Path $d | Out-Null\n'
            # /S — recurse into subfolders, skipping empty ones. Added
            # 2026-07-30 the moment 企画部 created their structure:
            # ★販売計画（企画部）/{デスクPlan,ノートPlan,Others/{Daily,Monthly,Weekly}}.
            # Without it robocopy copies only the files sitting in the folder
            # ROOT, so their entire upload would have synced as nothing at all,
            # with exit code 0 and a healthy-looking report. The ZM13/ZP21
            # folders are flat, so this changes nothing for them.
            f'robocopy "{unc}" $d /S /XO /R:1 /W:1 /NDL /NJH | Out-Null\n'
            f'Write-Output "{tag.upper()}_RC=$LASTEXITCODE"\n'
            # -Recurse so the count reflects the whole tree, not just the root:
            # 企画部's folder is subfoldered, and a flat count reported "9"
            # while their upload sat one level down, unseen.
            f'Write-Output "{tag.upper()}_AFTER=$((Get-ChildItem $d -File -Recurse | Measure-Object).Count)"\n'
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
    sap_counts: dict[str, int] = {}
    exit_codes: dict[str, int] = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("FILES_AFTER="):
            try:
                files_after = int(line.split("=", 1)[1])
            except ValueError:
                pass
        elif line.startswith("ONE_RC="):
            try:
                exit_codes["one"] = int(line.split("=", 1)[1])
            except ValueError:
                pass
        else:
            for tag, _unc, _cache in SUBFOLDER_SYNC_SOURCES:
                if line.startswith(f"{tag.upper()}_AFTER="):
                    try:
                        sap_counts[tag] = int(line.split("=", 1)[1])
                    except ValueError:
                        pass
                elif line.startswith(f"{tag.upper()}_RC="):
                    try:
                        exit_codes[tag] = int(line.split("=", 1)[1])
                    except ValueError:
                        pass

    # robocopy's exit code is a BITMASK, not a severity: 0 nothing to copy,
    # 1 files copied, 2 extras present, 4 mismatches, and only >= 8 means a
    # real failure. So "0" is a perfectly healthy weekly run where the NAS has
    # nothing newer — treating a non-zero code as failure would cry wolf on
    # every second rebuild.
    failed = {k: rc for k, rc in exit_codes.items() if rc >= 8}

    # "sap_folders" keeps its name so existing rebuild reports stay comparable,
    # even though it now also carries the "plan" count.
    out = {"status": "error" if failed else "ok",
           "one_dir": str(ONE_DIR), "files_in_cache": files_after,
           "sap_folders": sap_counts,
           "synced_folders": sap_counts,
           "robocopy_exit_codes": exit_codes}
    if failed:
        # Still NOT an exception: the build can legitimately proceed on the
        # existing cache (build._check_required_sources is the real gate). This
        # only ensures the rebuild report says so out loud.
        out["reason"] = (
            "robocopy failed for " + ", ".join(f"{k} (exit {rc})" for k, rc in sorted(failed.items()))
            + " — the share may be disconnected, credentials expired, or the "
              "folder renamed. Sources below are whatever was ALREADY cached, "
              "so treat their dates as unverified.")
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(refresh_from_nas(), indent=2, ensure_ascii=False))
