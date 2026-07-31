"""Monthly audit-log archive (onefcst/audit_archive.py).

This is the only code in the system that deletes audit records, so the tests
are weighted almost entirely towards NOT deleting: a dry run must not delete,
an unverifiable export must not delete, the current month must not be deleted
by default, and rows outside the month must never be touched.

The one positive test checks the whole point — that after a committed archive
the month is gone from the feed and a marker row explains where it went.
"""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from onefcst import audit_archive
from onefcst.models import AuditLog, Base


@pytest.fixture()
def db(monkeypatch, tmp_path):
    """A throwaway database that both the archive module and the exporter use.

    Both call get_session() on their own module, so both are patched — a test
    that deletes rows must never be able to reach the real database.
    """
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(audit_archive, "get_session", Session)
    from onefcst.export import excel as excel_mod
    monkeypatch.setattr(excel_mod, "get_session", Session)
    return Session()


def seed(session, when: datetime, n: int = 3, who="tanaka@twave.co.jp"):
    for i in range(n):
        session.add(AuditLog(who=who, what=f"event {i} at {when:%Y-%m}", at=when))
    session.commit()


def test_parse_month_accepts_what_an_operator_types():
    for text in ("2026-07", "2026/07", "202607"):
        assert audit_archive.parse_month(text) == (2026, 7)


def test_parse_month_rejects_an_impossible_month():
    with pytest.raises(ValueError):
        audit_archive.parse_month("2026-13")


def test_dry_run_exports_but_deletes_nothing(db, tmp_path):
    seed(db, datetime(2026, 3, 10), n=5)
    r = audit_archive.archive(2026, 3, commit=False, out_dir=tmp_path)
    assert r["status"] == "dry-run"
    assert r["rows"] == 5
    assert r["purged"] == 0
    assert db.query(AuditLog).count() == 5           # untouched
    assert (tmp_path / "変更履歴_202603.xlsx").exists()


def test_commit_exports_then_clears_the_month(db, tmp_path):
    seed(db, datetime(2026, 3, 10), n=4)
    r = audit_archive.archive(2026, 3, commit=True, out_dir=tmp_path)
    assert r["status"] == "ok"
    assert r["purged"] == 4

    remaining = db.query(AuditLog).all()
    # The month's own rows are gone; exactly one marker row remains, so a
    # reader can tell an archive from a deletion.
    assert len(remaining) == 1
    assert "アーカイブ" in remaining[0].what
    assert "変更履歴_202603.xlsx" in remaining[0].what


def test_other_months_are_never_touched(db, tmp_path):
    seed(db, datetime(2026, 2, 20), n=2)
    seed(db, datetime(2026, 3, 10), n=3)
    seed(db, datetime(2026, 4, 1), n=2)

    audit_archive.archive(2026, 3, commit=True, out_dir=tmp_path)

    kept = [r for r in db.query(AuditLog).all() if "アーカイブ" not in r.what]
    months = sorted({r.at.month for r in kept})
    assert months == [2, 4]
    assert len(kept) == 4


def test_month_boundaries_are_inclusive_start_exclusive_end(db, tmp_path):
    """A row at 03-01 00:00:00 belongs to March; one at 04-01 00:00:00 does not."""
    seed(db, datetime(2026, 3, 1, 0, 0, 0), n=1)
    seed(db, datetime(2026, 3, 31, 23, 59, 59), n=1)
    seed(db, datetime(2026, 4, 1, 0, 0, 0), n=1)

    r = audit_archive.archive(2026, 3, commit=True, out_dir=tmp_path)
    assert r["purged"] == 2
    kept = [x for x in db.query(AuditLog).all() if "アーカイブ" not in x.what]
    assert len(kept) == 1 and kept[0].at.month == 4


def test_december_rolls_into_the_next_year(db, tmp_path):
    seed(db, datetime(2026, 12, 15), n=2)
    seed(db, datetime(2027, 1, 5), n=1)
    r = audit_archive.archive(2026, 12, commit=True, out_dir=tmp_path)
    assert r["purged"] == 2
    kept = [x for x in db.query(AuditLog).all() if "アーカイブ" not in x.what]
    assert len(kept) == 1 and kept[0].at.year == 2027


def test_the_current_month_is_not_purged_without_force(db, tmp_path):
    """"Restart next month" means archiving a month that has FINISHED. Purging
    a live month would delete entries people still need on screen."""
    now = datetime.now()
    seed(db, now.replace(day=1, hour=9), n=3)
    r = audit_archive.archive(now.year, now.month, commit=True, out_dir=tmp_path)
    assert r["status"] == "exported-only"
    assert r["purged"] == 0
    assert db.query(AuditLog).count() == 3
    assert r["exported"]                              # still exported for review


def test_force_allows_the_current_month(db, tmp_path):
    now = datetime.now()
    seed(db, now.replace(day=1, hour=9), n=2)
    r = audit_archive.archive(now.year, now.month, commit=True, force=True,
                              out_dir=tmp_path)
    assert r["purged"] == 2


def test_a_failed_verification_deletes_nothing(db, tmp_path, monkeypatch):
    """The failure this whole design exists to prevent: a half-written file
    next to a wiped table."""
    seed(db, datetime(2026, 3, 10), n=3)
    monkeypatch.setattr(audit_archive, "_verify",
                        lambda path, expected: (False, "simulated bad export"))
    r = audit_archive.archive(2026, 3, commit=True, out_dir=tmp_path)
    assert r["status"] == "verification-failed"
    assert r["purged"] == 0
    assert db.query(AuditLog).count() == 3


def test_verification_catches_a_short_export(db, tmp_path):
    """_verify counts the rows actually in the workbook, so a truncated file
    is caught rather than trusted."""
    seed(db, datetime(2026, 3, 10), n=3)
    from onefcst.export.excel import export_audit_month
    path, count = export_audit_month(2026, 3, tmp_path)
    assert count == 3
    ok, detail = audit_archive._verify(path, expected=3)
    assert ok, detail
    bad, detail = audit_archive._verify(path, expected=99)
    assert not bad and "99 were selected" in detail


def test_an_empty_month_is_a_no_op(db, tmp_path):
    r = audit_archive.archive(2026, 3, commit=True, out_dir=tmp_path)
    assert r["status"] == "empty"
    assert r["purged"] == 0


def test_the_export_resolves_names_and_departments(db, tmp_path):
    """A report read a year later must say who the person was, not their alias."""
    from openpyxl import load_workbook

    from onefcst.models import UserProfile
    db.add(UserProfile(email="tanaka@twave.co.jp", display_name="田中 太郎",
                       department="購買本部"))
    db.commit()
    seed(db, datetime(2026, 3, 10), n=1, who="tanaka@twave.co.jp")

    from onefcst.export.excel import export_audit_month
    path, _ = export_audit_month(2026, 3, tmp_path)
    ws = load_workbook(path).active
    header = [c.value for c in ws[4]]
    assert header[:4] == ["日時", "担当者", "部署", "メールアドレス"]
    row = [c.value for c in ws[5]]
    assert row[1] == "田中 太郎"
    assert row[2] == "購買本部"
    assert row[3] == "tanaka@twave.co.jp"      # the key is still recorded
