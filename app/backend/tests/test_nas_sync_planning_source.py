"""企画部's ①②③④ drop folder — ★販売計画（企画部） on the ONE share.

Agreed 2026-07-30: planning stops being a hand-upload and starts arriving on
the NAS like the weekly ZM13/ZP21 drops. Two details are load-bearing and easy
to break later, so they are pinned here:

* the destination is **PLANNING_DIR itself**, not a subfolder of it, because
  `config.planning_file()` globs that directory directly by circled-number
  prefix (`①*.xlsx`). A tidy-looking `PLANNING_DIR / "販売計画"` would sync
  perfectly and still leave the parsers finding nothing.
* the folder may not exist on the share yet. Until 企画部 starts using it the
  files arrive by hand, so an unreachable subfolder must be a no-op that keeps
  whatever is already in PLANNING_DIR — never an error, and never a purge
  (robocopy runs /XO, which only copies files that are newer).
"""
from __future__ import annotations

from onefcst import config, nas_sync


def _source(tag: str):
    return next((s for s in nas_sync.SUBFOLDER_SYNC_SOURCES if s[0] == tag), None)


def test_planning_folder_is_registered_as_a_sync_source():
    assert _source("plan") is not None, "企画部's ①②③④ drop is not synced"


def test_planning_syncs_into_planning_dir_itself_not_a_subfolder():
    """Where planning_file() actually looks — see the module docstring."""
    _, _, cache = _source("plan")
    assert cache == config.PLANNING_DIR


def test_planning_unc_points_at_the_agreed_folder():
    _, unc, _ = _source("plan")
    assert unc.endswith("★販売計画（企画部）")
    assert unc.startswith(r"\\172.25.20.11")


def test_planning_unc_is_overridable_by_env():
    """The share layout is the platform team's to change, not ours to hardcode."""
    import importlib
    import os
    os.environ["ONEFCST_NAS_PLAN_UNC"] = r"\\example\share\plans"
    try:
        reloaded = importlib.reload(config)
        assert reloaded.NAS_PLAN_UNC == r"\\example\share\plans"
    finally:
        del os.environ["ONEFCST_NAS_PLAN_UNC"]
        importlib.reload(config)


def test_all_three_drops_are_synced_and_distinct():
    """zm13 / zp21 / plan must not collide on a destination — one overwriting
    another's cache would look like a partially-refreshed source set."""
    tags = [t for t, _, _ in nas_sync.SUBFOLDER_SYNC_SOURCES]
    assert tags == ["zm13", "zp21", "plan"]
    dests = [str(c) for _, _, c in nas_sync.SUBFOLDER_SYNC_SOURCES]
    assert len(set(dests)) == 3


def test_legacy_alias_still_resolves():
    """`SAP_SYNC_SOURCES` shipped in the 2026-07-27 sync and may be referenced
    elsewhere; it now includes a non-SAP entry, hence the rename plus alias."""
    assert nas_sync.SAP_SYNC_SOURCES is nas_sync.SUBFOLDER_SYNC_SOURCES
