"""robocopy's exit code decides the sync status, not powershell's.

Found 2026-07-30 while verifying a manual rebuild against the share by hand:
refresh_from_nas() returned {"status": "ok"} regardless of whether robocopy
actually copied anything, because each robocopy was piped to Out-Null and
powershell.exe exits 0 as long as the SCRIPT ran. An unreachable share, expired
AD credentials or a renamed folder therefore all looked like a clean sync — over
stale files, with no signal anywhere. Same silent-staleness shape as the
SAP_ACCDB_PATH bug found the same morning.

The subtlety worth a test: robocopy's exit code is a BITMASK, not a severity.
0 = nothing needed copying (the normal case for a weekly source mid-week),
1 = files copied, 2 = extra files at the destination, 4 = mismatches, and only
>= 8 is a genuine failure. Reporting any non-zero code as an error would fire on
most rebuilds and get ignored, which is worse than not reporting at all.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from onefcst import nas_sync


@pytest.fixture
def fake_interop(monkeypatch, tmp_path):
    """Pretend to be WSL-with-interop; hand the parser a scripted stdout."""
    monkeypatch.delenv("ONEFCST_NAS_MODE", raising=False)
    monkeypatch.setattr(nas_sync, "ONE_DIR", tmp_path)
    monkeypatch.setattr(nas_sync, "_to_windows_path", lambda p: "C:\\fake")

    def _run(stdout: str):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
        monkeypatch.setattr(nas_sync.subprocess, "run", fake_run)
    return _run


def _stdout(one_rc=0, zm13_rc=0, zp21_rc=0, plan_rc=0):
    return "\n".join([
        f"ONE_RC={one_rc}", "FILES_AFTER=36",
        f"ZM13_RC={zm13_rc}", "ZM13_AFTER=10",
        f"ZP21_RC={zp21_rc}", "ZP21_AFTER=8",
        f"PLAN_RC={plan_rc}", "PLAN_AFTER=9",
    ])


def test_exit_code_zero_is_a_healthy_run(fake_interop):
    """Nothing newer on the share is the NORMAL mid-week state."""
    fake_interop(_stdout())
    res = nas_sync.refresh_from_nas()
    assert res["status"] == "ok"
    assert res["robocopy_exit_codes"] == {"one": 0, "zm13": 0, "zp21": 0, "plan": 0}


@pytest.mark.parametrize("rc", [1, 2, 3, 7])
def test_bitmask_codes_below_eight_are_still_success(fake_interop, rc):
    """1=copied, 2=extras, 4=mismatches — none of these are failures."""
    fake_interop(_stdout(one_rc=rc, zm13_rc=rc))
    assert nas_sync.refresh_from_nas()["status"] == "ok"


@pytest.mark.parametrize("rc", [8, 16])
def test_eight_or_above_makes_the_whole_sync_an_error(fake_interop, rc):
    fake_interop(_stdout(one_rc=rc))
    res = nas_sync.refresh_from_nas()
    assert res["status"] == "error"
    assert "one" in res["reason"] and str(rc) in res["reason"]


def test_a_single_failed_subfolder_is_reported_by_name(fake_interop):
    """The ONE share can be fine while one subfolder is renamed or locked."""
    fake_interop(_stdout(zp21_rc=16))
    res = nas_sync.refresh_from_nas()
    assert res["status"] == "error"
    assert "zp21" in res["reason"]
    # and the counts still come back, since the build may proceed on the cache
    assert res["sap_folders"]["zm13"] == 10


def test_failure_does_not_raise(fake_interop):
    """build.py treats the refresh as advisory — _check_required_sources is the
    real gate. A sync failure must not abort a rebuild that can run on cache."""
    fake_interop(_stdout(one_rc=16, zm13_rc=16, zp21_rc=16, plan_rc=16))
    res = nas_sync.refresh_from_nas()          # must not raise
    assert res["status"] == "error"
    assert res["files_in_cache"] == 36


def test_reason_says_the_dates_are_unverified(fake_interop):
    """The actionable part: whatever is cached may be older than it looks."""
    fake_interop(_stdout(plan_rc=8))
    assert "unverified" in nas_sync.refresh_from_nas()["reason"]
