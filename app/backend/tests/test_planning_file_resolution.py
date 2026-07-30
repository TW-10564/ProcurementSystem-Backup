"""Resolving 企画部's planning workbooks after they restructured the NAS folder.

On 2026-07-30 企画部 replaced the flat ①②③④-prefixed layout we maintained by
hand with their own structure and their own names:

    ★販売計画（企画部）/デスクPlan/販売計画6月提出分_….xlsx
                       /ノートPlan/                              (empty)
                       /Others/Monthly/☆値のみ【月次】【係数】月次推移20260701.xlsx
                       /Others/Weekly/【日次】ノート集計【2026年6月】_0602 1.xlsx

A prefix glob finds nothing there, which aborted every rebuild on the DGX (where
PLANNING_DIR points straight at that folder). Locally it was worse: stale
prefixed copies still sat at the top level, so the build reported success while
reading June data with July sitting unread on disk.

These tests pin both layouts, the transition state where both exist, and the
staleness trap — that last one is the real bug, because it has no symptom.
"""
from __future__ import annotations

import pytest

from onefcst import config

# The real filenames, copied verbatim from the share. Worth the noise: the
# fragment matching has to survive 【】, ☆★, full-width ~ and a " (1)" suffix
# from a duplicate download, and a paraphrase would not prove that.
DESK = "販売計画6月提出分_2026年6月~2026年11月r2 (1).xlsx"
NOTE_AGG = "【日次】ノート集計【2026年6月】_0602 1.xlsx"
KEISUU_JUL = "☆値のみ【月次】【係数】月次推移20260701.xlsx"
KEISUU_JUN = "③【予測データ】☆値のみ【月次】【係数】月次推移20260615.xlsx"
LEGACY_DESK = "①【販売計画】6月提出分_2026年6月~2026年11月r2.xlsx"
LEGACY_PARTS = "④【予測データ】★値のみ【日次】【ブランド別】部材利用数20260202★ONE.xlsx"


@pytest.fixture
def planning_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PLANNING_DIR", tmp_path)
    return tmp_path


def write(root, relpath: str):
    p = root / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    return p


# ------------------------------------------------ 企画部's new structure ---

def test_finds_files_in_kikakubu_subfolders(planning_dir):
    """The layout that broke the DGX. This is the regression test."""
    write(planning_dir, f"デスクPlan/{DESK}")
    write(planning_dir, f"Others/Weekly/{NOTE_AGG}")
    write(planning_dir, f"Others/Monthly/{KEISUU_JUL}")
    (planning_dir / "ノートPlan").mkdir()

    assert config.find_planning_file("①").name == DESK
    assert config.find_planning_file("②").name == NOTE_AGG
    assert config.find_planning_file("③").name == KEISUU_JUL


def test_required_source_check_passes_on_the_new_layout(planning_dir):
    """planning_file('①') raising here is precisely what aborted the rebuild."""
    write(planning_dir, f"デスクPlan/{DESK}")
    assert config.planning_file("①").name == DESK


def test_absent_workbook_returns_none_rather_than_raising(planning_dir):
    """④ is genuinely not uploaded; that must degrade, not abort."""
    write(planning_dir, f"デスクPlan/{DESK}")
    assert config.find_planning_file("④") is None
    with pytest.raises(FileNotFoundError):
        config.planning_file("④")


def test_the_error_names_the_file_a_human_would_look_for(planning_dir):
    """'no ④ file' sends someone hunting for a filename that does not exist."""
    with pytest.raises(FileNotFoundError, match="部材利用数"):
        config.planning_file("④")


# --------------------------------------------------- the legacy layout ---

def test_legacy_prefixed_flat_layout_still_works(planning_dir):
    """Nothing about the old hand-renamed convention may break."""
    write(planning_dir, LEGACY_DESK)
    write(planning_dir, LEGACY_PARTS)
    assert config.find_planning_file("①").name == LEGACY_DESK
    assert config.find_planning_file("④").name == LEGACY_PARTS


# ------------------------------------------------------ the stale trap ---

def test_newest_data_period_wins_not_the_newest_copy(planning_dir):
    """The silent bug: June at the top level, July nested, June was read.

    Both files legitimately exist during the transition. The one naming the
    later PERIOD must win — and it must win even though the June copy was
    touched more recently, which is exactly what happens when an old file is
    re-synced or archived.
    """
    old = write(planning_dir, KEISUU_JUN)          # 20260615, flat, legacy name
    new = write(planning_dir, f"Others/Monthly/{KEISUU_JUL}")   # 20260701, nested
    import os
    os.utime(new, (1, 1))                          # July file: ancient mtime
    os.utime(old, None)                            # June file: touched now

    assert config.find_planning_file("③").name == KEISUU_JUL, (
        "picked the June workbook — filename period must beat mtime")
    assert old.exists()                            # nothing is deleted


def test_month_only_filenames_are_ordered_too(planning_dir):
    """Not every name carries a yyyymmdd — 【2026年6月】 must still order."""
    write(planning_dir, "【日次】ノート集計【2026年6月】_0602.xlsx")
    write(planning_dir, "【日次】ノート集計【2026年7月】_0702.xlsx")
    assert "2026年7月" in config.find_planning_file("②").name


# ---------------------------------------------------------- exclusions ---

def test_archived_copies_never_win(planning_dir):
    """We archive superseded files; a retired copy must not come back."""
    write(planning_dir, f"_archive_2026-07-30/{KEISUU_JUL}")
    write(planning_dir, KEISUU_JUN)
    assert config.find_planning_file("③").name == KEISUU_JUN


def test_archive_only_means_missing_not_a_stale_fallback(planning_dir):
    """If the ONLY copy is archived, report missing — do not resurrect it."""
    write(planning_dir, f"_archive_2026-07-30/{KEISUU_JUL}")
    assert config.find_planning_file("③") is None


def test_excel_lock_files_are_ignored(planning_dir):
    """A colleague with the workbook open leaves ~$… beside it. Opening that
    as a workbook fails in a way that names neither the file nor the cause."""
    write(planning_dir, f"デスクPlan/~${DESK}")
    assert config.find_planning_file("①") is None
    write(planning_dir, f"デスクPlan/{DESK}")
    assert config.find_planning_file("①").name == DESK


def test_missing_planning_dir_is_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PLANNING_DIR", tmp_path / "does-not-exist")
    assert config.find_planning_file("①") is None


def test_one_workbook_never_satisfies_another_input(planning_dir):
    """Fragments must not overlap: ノート集計 is not the 販売計画 file."""
    write(planning_dir, f"Others/Weekly/{NOTE_AGG}")
    assert config.find_planning_file("①") is None
    assert config.find_planning_file("③") is None
    assert config.find_planning_file("④") is None
    assert config.find_planning_file("②").name == NOTE_AGG
