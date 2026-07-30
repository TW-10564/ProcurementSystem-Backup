"""Pre-rebuild backup: rebuild_preserving.backup_db().

rebuild_preserving.run() holds the only other copy of the irreplaceable
human data (confirmed compat rules, adjustments, order actions, audit log,
accuracy history) in a Python dict while it drops and rebuilds every table.
An interrupted rebuild used to mean permanent loss. These tests pin the
three properties that make the backup actually protective:

  1. it runs BEFORE anything is dropped,
  2. the copy is complete and readable (not a truncated file),
  3. a failed backup ABORTS the rebuild rather than proceeding to the drop.

(3) is the one worth stating twice: a backup that silently no-ops on
failure is worse than none, because it makes people believe they're covered.
"""
from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

from onefcst import rebuild_preserving as rp


def _make_db(path, rows=5):
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE compat_rules (id INTEGER PRIMARY KEY, note TEXT)")
    con.executemany("INSERT INTO compat_rules (note) VALUES (?)",
                    [(f"human-confirmed-{i}",) for i in range(rows)])
    con.commit()
    con.close()


@pytest.fixture
def fake_db(tmp_path, monkeypatch):
    db = tmp_path / "onefcst.db"
    _make_db(db)
    import onefcst.config as cfg
    monkeypatch.setattr(cfg, "DB_PATH", db, raising=False)
    monkeypatch.setattr(cfg, "DATABASE_URL", None, raising=False)
    return db


def test_backup_creates_a_complete_readable_copy(fake_db):
    res = rp.backup_db()
    assert res["status"] == "ok"
    copy = sqlite3.connect(res["path"])
    got = [r[0] for r in copy.execute("SELECT note FROM compat_rules ORDER BY id")]
    copy.close()
    assert got == [f"human-confirmed-{i}" for i in range(5)]


def test_backup_lands_in_a_backups_subfolder_beside_the_db(fake_db):
    res = rp.backup_db()
    from pathlib import Path
    assert Path(res["path"]).parent == fake_db.parent / "backups"
    assert Path(res["path"]).exists()


def test_failed_backup_raises_so_the_rebuild_never_reaches_the_drop(fake_db, monkeypatch):
    """The safety property. If this ever regresses to a warning, an
    interrupted rebuild silently destroys data again."""
    def boom(*a, **k):
        raise OSError("No space left on device")
    monkeypatch.setattr(sqlite3, "connect", boom)
    with pytest.raises(RuntimeError, match="backup failed"):
        rp.backup_db()


def test_a_failed_backup_leaves_no_truncated_file_behind(fake_db, monkeypatch):
    real_connect = sqlite3.connect

    def half_broken(target, *a, **k):
        con = real_connect(target, *a, **k)
        if str(target).endswith(".db") and "backups" in str(target):
            con.backup = lambda *_a, **_k: (_ for _ in ()).throw(OSError("disk full"))
        return con

    monkeypatch.setattr(sqlite3, "connect", half_broken)
    backups = fake_db.parent / "backups"
    with pytest.raises(RuntimeError):
        rp.backup_db()
    leftovers = list(backups.glob("*.db")) if backups.exists() else []
    assert leftovers == [], f"truncated backup left behind: {leftovers}"


def test_retention_prunes_oldest_and_keeps_the_configured_number(fake_db, monkeypatch):
    monkeypatch.setenv("ONEFCST_DB_BACKUP_KEEP", "3")
    backups = fake_db.parent / "backups"
    backups.mkdir(exist_ok=True)
    for stamp in ["20260101-000001", "20260101-000002", "20260101-000003",
                  "20260101-000004"]:
        (backups / f"onefcst-{stamp}.db").write_bytes(b"old")
    res = rp.backup_db()
    assert res["status"] == "ok"
    remaining = sorted(p.name for p in backups.glob("onefcst-*.db"))
    assert len(remaining) == 3, remaining
    # the newest (just written) survives, the oldest are gone
    assert "onefcst-20260101-000001.db" not in remaining
    assert any(p == res["path"].split("/")[-1] for p in remaining)


def test_postgres_is_actually_dumped_not_skipped(fake_db, monkeypatch, tmp_path):
    """Postgres must get a REAL backup, not a "skipped" that lets the rebuild
    proceed to drop_all with nothing saved.

    This test previously asserted the opposite — that "skipped" was correct
    behaviour on a non-SQLite URL. That was the bug: run() carries straight
    on to build.run()'s drop_all(), so pointing DATABASE_URL at Postgres
    silently removed the only protection for data that, per this module's
    own docstring, "exists in no other place".
    """
    import onefcst.config as cfg
    monkeypatch.setattr(cfg, "DATABASE_URL",
                        "postgresql+psycopg://u:pw@h:5432/db", raising=False)

    calls = {}

    def fake_run(cmd, **kw):
        calls["cmd"] = cmd
        calls["env"] = kw.get("env", {})
        Path(cmd[cmd.index("--file") + 1]).write_bytes(b"PGDMP-fake")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(rp.subprocess, "run", fake_run)
    res = rp.backup_db()

    assert res["status"] == "ok"
    assert res["format"] == "pg_dump-custom"
    assert res["path"].endswith(".dump")
    assert Path(res["path"]).exists()
    assert calls["cmd"][0] == "pg_dump"
    # password must travel in the environment, never in argv (/proc is
    # world-readable on a shared host)
    assert "pw" not in " ".join(calls["cmd"])
    assert calls["env"]["PGPASSWORD"] == "pw"


def test_failed_pg_dump_raises_so_the_rebuild_aborts(fake_db, monkeypatch):
    """Same contract as the SQLite path: could-not-protect must never
    silently proceed to the drop."""
    import onefcst.config as cfg
    monkeypatch.setattr(cfg, "DATABASE_URL",
                        "postgresql+psycopg://u:pw@h:5432/db", raising=False)
    monkeypatch.setattr(
        rp.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "connection refused"))
    with pytest.raises(RuntimeError, match="rebuild aborted"):
        rp.backup_db()


def test_missing_pg_dump_binary_raises_rather_than_skipping(fake_db, monkeypatch):
    """A container built without postgresql-client must fail loudly, not
    quietly drop every table."""
    import onefcst.config as cfg
    monkeypatch.setattr(cfg, "DATABASE_URL",
                        "postgresql+psycopg://u:pw@h:5432/db", raising=False)

    def missing(*a, **k):
        raise FileNotFoundError("pg_dump")

    monkeypatch.setattr(rp.subprocess, "run", missing)
    with pytest.raises(RuntimeError, match="postgresql-client"):
        rp.backup_db()


def test_missing_database_is_skipped_not_an_error(tmp_path, monkeypatch):
    """First-ever build on a clean machine: nothing to lose, must not block."""
    import onefcst.config as cfg
    monkeypatch.setattr(cfg, "DB_PATH", tmp_path / "does-not-exist.db", raising=False)
    monkeypatch.setattr(cfg, "DATABASE_URL", None, raising=False)
    assert rp.backup_db()["status"] == "skipped"


def test_run_backs_up_before_it_snapshots_or_builds(fake_db, monkeypatch):
    """Ordering is the whole point: a backup taken after the drop is useless."""
    order = []
    monkeypatch.setattr(rp, "backup_db", lambda: (order.append("backup"), {"status": "ok"})[1])
    monkeypatch.setattr(rp.nas_sync, "refresh_from_nas", lambda: (order.append("nas"), {"status": "skipped"})[1])
    # run() reports len() over every snapshot bucket, so hand it something
    # that answers for any key rather than enumerating them here
    monkeypatch.setattr(rp, "_snapshot", lambda s: (order.append("snapshot"), _AnyKeys())[1])
    monkeypatch.setattr(rp.build_mod, "run", lambda: (order.append("build"), {})[1])
    monkeypatch.setattr(rp, "_restore", lambda s, snap: (order.append("restore"), ({}, {}))[1])
    monkeypatch.setattr(rp, "get_session", lambda: _NullSession())
    rp.run()
    assert order.index("backup") < order.index("snapshot") < order.index("build")


def _stub_rebuild(monkeypatch, build_fn):
    """Wire run() so only the maintenance-flag behaviour is under test."""
    monkeypatch.setattr(rp, "backup_db", lambda: {"status": "ok"})
    monkeypatch.setattr(rp.nas_sync, "refresh_from_nas", lambda: {"status": "skipped"})
    monkeypatch.setattr(rp, "_snapshot", lambda s: _AnyKeys())
    monkeypatch.setattr(rp, "_restore", lambda s, snap: ({}, {}))
    monkeypatch.setattr(rp, "get_session", lambda: _NullSession())
    monkeypatch.setattr(rp.build_mod, "run", build_fn)


def test_rebuild_enables_maintenance_during_the_destructive_window(fake_db, monkeypatch, tmp_path):
    """The drop..restore window serves a half-built database. Users must be
    told, and on Postgres holding new readers off is also what stops them
    queueing behind the DROP's ACCESS EXCLUSIVE lock."""
    monkeypatch.setattr(rp.maintenance, "STATE_PATH", tmp_path / "maintenance.json")
    seen = {}
    _stub_rebuild(monkeypatch, lambda: (seen.update(
        during=rp.maintenance.get_state()["enabled"]), {})[1])

    rp.run()

    assert seen["during"] is True, "maintenance was not on while tables were dropped"
    assert rp.maintenance.get_state()["enabled"] is False, "left in maintenance mode"


def test_failed_rebuild_does_not_strand_the_app_in_maintenance_mode(fake_db, monkeypatch, tmp_path):
    """A crash mid-build must not leave every user locked out until someone
    notices and clears the flag by hand."""
    monkeypatch.setattr(rp.maintenance, "STATE_PATH", tmp_path / "maintenance.json")

    def boom():
        raise RuntimeError("ingest exploded")

    _stub_rebuild(monkeypatch, boom)

    with pytest.raises(RuntimeError, match="ingest exploded"):
        rp.run()
    assert rp.maintenance.get_state()["enabled"] is False


def test_rebuild_leaves_a_pre_existing_maintenance_mode_on(fake_db, monkeypatch, tmp_path):
    """If an operator put the site into maintenance deliberately, finishing a
    rebuild must not silently open it back up."""
    monkeypatch.setattr(rp.maintenance, "STATE_PATH", tmp_path / "maintenance.json")
    rp.maintenance.set_state(True, "planned outage", by="operator")
    _stub_rebuild(monkeypatch, lambda: {})

    rp.run()

    state = rp.maintenance.get_state()
    assert state["enabled"] is True
    assert state["message"] == "planned outage"


class _AnyKeys(dict):
    """Empty bucket for any snapshot key run() reports a len() for."""
    def __missing__(self, key):
        return []


class _NullSession:
    def close(self): pass
    def query(self, *a, **k): raise AssertionError("not used in this test")
