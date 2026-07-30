"""ONEFCST_NAS_MODE=mount — the DGX/container path.

nas_sync's original design is WSL-only: it shells out to wslpath /
powershell.exe / robocopy against a hardcoded `Z:\`. On a Linux host all of
that is absent, and the function returned a bare
{"status": "skipped", "reason": "wslpath unavailable: ..."} — indistinguishable
from a genuinely broken sync, so a missing NAS mount would only surface much
later as a confusing "missing source file" from _check_required_sources().

These pin the explicit branch: mounted-by-the-OS is a deliberate no-op, and
an unmounted/empty mount point is reported as an ERROR at the point it can
still be acted on.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from onefcst import nas_sync


@pytest.fixture
def mount_mode(monkeypatch):
    monkeypatch.setenv("ONEFCST_NAS_MODE", "mount")


def test_populated_mount_is_a_deliberate_no_op(mount_mode, monkeypatch, tmp_path):
    (tmp_path / "ONE計画.xlsm").write_bytes(b"x")
    monkeypatch.setattr(nas_sync, "ONE_DIR", tmp_path)

    res = nas_sync.refresh_from_nas()

    assert res["status"] == "ok"
    assert res["mode"] == "mount"
    assert res["files_in_cache"] == 1
    assert "no sync needed" in res["reason"]


def test_missing_mount_point_is_an_error_not_a_silent_skip(mount_mode, monkeypatch, tmp_path):
    monkeypatch.setattr(nas_sync, "ONE_DIR", tmp_path / "never-mounted")

    res = nas_sync.refresh_from_nas()

    assert res["status"] == "error"
    assert "missing" in res["reason"]


def test_empty_mount_point_is_an_error(mount_mode, monkeypatch, tmp_path):
    """The share mounts but has no files — expired credentials, or the unit
    came up before the network. Must not read as success."""
    monkeypatch.setattr(nas_sync, "ONE_DIR", tmp_path)

    res = nas_sync.refresh_from_nas()

    assert res["status"] == "error"
    assert "empty" in res["reason"]


def test_mount_mode_never_shells_out_to_windows(mount_mode, monkeypatch, tmp_path):
    """The whole point: no wslpath/powershell on a Linux host."""
    (tmp_path / "ONE計画.xlsm").write_bytes(b"x")
    monkeypatch.setattr(nas_sync, "ONE_DIR", tmp_path)

    def explode(*a, **k):
        raise AssertionError(f"subprocess called in mount mode: {a}")

    monkeypatch.setattr(nas_sync.subprocess, "run", explode)
    assert nas_sync.refresh_from_nas()["status"] == "ok"


def test_linux_without_the_flag_says_what_to_set(monkeypatch, tmp_path):
    """Forgetting ONEFCST_NAS_MODE on a Linux host should name the fix, not
    just report a missing Windows tool."""
    monkeypatch.delenv("ONEFCST_NAS_MODE", raising=False)
    monkeypatch.setattr(nas_sync, "ONE_DIR", tmp_path)
    monkeypatch.setattr(Path, "exists", lambda self: False)

    def no_wslpath(*a, **k):
        raise FileNotFoundError("wslpath")

    monkeypatch.setattr(nas_sync.subprocess, "run", no_wslpath)

    res = nas_sync.refresh_from_nas()
    assert res["status"] == "skipped"
    assert "ONEFCST_NAS_MODE=mount" in res["reason"]
