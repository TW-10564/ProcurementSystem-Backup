"""SQLite -> PostgreSQL cutover script (onefcst/migrate_to_postgres.py).

This runs ONCE, against production data, and the thing it moves — confirmed
compat rules, adjustments, order actions, the audit log — exists in no other
place once the target is rebuilt. So the properties worth pinning are less
about the happy path (which was verified end-to-end against the real
database) and more about the ways a one-shot script can quietly do damage:

  1. a dry run must not write ANYTHING — it is what people run first,
  2. the SQLite source must be opened read-only, so a bug here cannot
     corrupt the rollback copy,
  3. the snapshot must happen BEFORE build.run() drops every table,
  4. a wrong/missing target must abort before any of that,
  5. lost rows must be REPORTED, not silently tolerated — the verification
     re-reads Postgres rather than trusting the restore.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from onefcst import migrate_to_postgres as mig
from onefcst.models import Base, CompatRule, Item

PG_URL = "postgresql+psycopg://u:pw@h:5432/onefcst"


@pytest.fixture
def sqlite_db(tmp_path):
    """A real on-disk SQLite database with a little human data in it."""
    path = tmp_path / "onefcst.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    cpu = Item(canonical_name="CPU_A", kind="part")
    mb = Item(canonical_name="MB_A", kind="part")
    s.add_all([cpu, mb])
    s.flush()
    s.add(CompatRule(part_a_id=cpu.id, part_b_id=mb.id, relation="fits",
                     confidence=0.9, origin="human", status="confirmed",
                     confirmed_by="alice"))
    s.commit()
    s.close()
    engine.dispose()
    return path


@pytest.fixture
def target_pg(monkeypatch):
    """Pretend DATABASE_URL points at Postgres (read at import time)."""
    monkeypatch.setattr(mig, "_PG_URL", PG_URL)


# --------------------------------------------------------------- guard rails

def test_refuses_when_target_is_not_postgres(monkeypatch, sqlite_db):
    monkeypatch.setattr(mig, "_PG_URL", "sqlite:///whatever.db")
    with pytest.raises(SystemExit, match="must point at PostgreSQL"):
        mig.run(commit=True, sqlite_path=sqlite_db)


def test_refuses_when_database_url_is_unset(monkeypatch, sqlite_db):
    monkeypatch.setattr(mig, "_PG_URL", "")
    with pytest.raises(SystemExit, match="must point at PostgreSQL"):
        mig.run(commit=True, sqlite_path=sqlite_db)


def test_refuses_when_the_source_database_is_missing(target_pg, tmp_path):
    with pytest.raises(SystemExit, match="no SQLite database"):
        mig.run(commit=True, sqlite_path=tmp_path / "nope.db")


def test_guards_run_before_anything_destructive(target_pg, tmp_path, monkeypatch):
    """The abort must happen BEFORE build.run(), which drops every table."""
    def exploded():
        raise AssertionError("build.run() reached despite a failed guard")

    monkeypatch.setattr(mig.build_mod, "run", exploded)
    with pytest.raises(SystemExit):
        mig.run(commit=True, sqlite_path=tmp_path / "nope.db")


# ------------------------------------------------------------------ dry run

def test_dry_run_writes_nothing(target_pg, sqlite_db, monkeypatch):
    """A dry run is what people run first — it must not touch the target."""
    def exploded(*a, **k):
        raise AssertionError("dry run performed a write")

    monkeypatch.setattr(mig.build_mod, "run", exploded)
    monkeypatch.setattr(mig, "_restore", exploded)
    monkeypatch.setattr(mig, "get_session", exploded)

    res = mig.run(commit=False, sqlite_path=sqlite_db)

    assert res["status"] == "dry-run"
    assert res["counts"]["compat_rules"] == 1


def test_dry_run_reports_what_it_found(target_pg, sqlite_db, monkeypatch):
    monkeypatch.setattr(mig.build_mod, "run", lambda: {})
    res = mig.run(commit=False, sqlite_path=sqlite_db)
    # every human-data bucket is accounted for, not just the non-empty ones
    assert set(res["counts"]) == set(mig.HUMAN_DATA)


# ------------------------------------------------------- source is read-only

def test_source_sqlite_is_opened_read_only(sqlite_db):
    """The SQLite file is the rollback. A bug in this script must not be
    able to modify it."""
    s = mig._sqlite_session(sqlite_db)
    s.add(Item(canonical_name="SHOULD_NOT_PERSIST", kind="part"))
    with pytest.raises(OperationalError, match="readonly database"):
        s.commit()
    s.rollback()
    s.close()


def test_source_still_intact_after_a_migration(target_pg, sqlite_db, monkeypatch):
    _stub_target(monkeypatch, restored={})
    mig.run(commit=True, sqlite_path=sqlite_db)

    check = create_engine(f"sqlite:///{sqlite_db}")
    s = sessionmaker(bind=check)()
    assert s.query(CompatRule).count() == 1, "source database was modified"
    s.close()


# ----------------------------------------------------------------- ordering

def test_snapshot_happens_before_the_target_is_rebuilt(target_pg, sqlite_db, monkeypatch):
    """build.run() drops every table. A snapshot taken after it would be
    empty — the same ordering property rebuild_preserving pins."""
    order = []
    real_snapshot = mig._snapshot
    calls = {"n": 0}

    def tracked_snapshot(session):
        calls["n"] += 1
        if calls["n"] == 1:
            order.append("snapshot")
            return real_snapshot(session)
        # second call is the post-migration verification re-read, which runs
        # against the (stubbed) target — not part of the ordering under test
        return {k: [] for k in mig.HUMAN_DATA}

    monkeypatch.setattr(mig, "_snapshot", tracked_snapshot)
    monkeypatch.setattr(mig.build_mod, "run",
                        lambda: (order.append("build"), {})[1])
    monkeypatch.setattr(mig, "_restore",
                        lambda s, snap: (order.append("restore"), ({}, {}))[1])
    monkeypatch.setattr(mig, "get_session", lambda: _NullSession())

    mig.run(commit=True, sqlite_path=sqlite_db)

    assert order.index("snapshot") < order.index("build") < order.index("restore")


# -------------------------------------------------------------- verification

def _stub_target(monkeypatch, restored: dict):
    """Stub the Postgres side so `restored` is what a post-migration
    _snapshot() sees. Bucket order matters: the FIRST _snapshot call reads
    SQLite for real, the SECOND re-reads the target for verification."""
    monkeypatch.setattr(mig.build_mod, "run", lambda: {"gate_a": {}})
    monkeypatch.setattr(mig, "_restore", lambda s, snap: ({}, {}))
    monkeypatch.setattr(mig, "get_session", lambda: _NullSession())

    real_snapshot = mig._snapshot
    calls = {"n": 0}

    def fake_snapshot(session):
        calls["n"] += 1
        if calls["n"] == 1:
            return real_snapshot(session)          # the real SQLite read
        return {k: [None] * restored.get(k, 0) for k in mig.HUMAN_DATA}

    monkeypatch.setattr(mig, "_snapshot", fake_snapshot)


def test_reports_ok_when_every_bucket_round_trips(target_pg, sqlite_db, monkeypatch):
    _stub_target(monkeypatch, restored={"compat_rules": 1})
    res = mig.run(commit=True, sqlite_path=sqlite_db)
    assert res["status"] == "ok"


def test_lost_rows_are_reported_not_silently_accepted(target_pg, sqlite_db, monkeypatch):
    """The whole point of re-reading the target: if the restore dropped the
    one confirmed compat rule, the operator must be told BEFORE they delete
    the SQLite file."""
    _stub_target(monkeypatch, restored={"compat_rules": 0})
    res = mig.run(commit=True, sqlite_path=sqlite_db)

    assert res["status"] == "discrepancies"
    assert res["before"]["compat_rules"] == 1
    assert res["after"]["compat_rules"] == 0


def test_unexpected_extra_rows_are_also_a_discrepancy(target_pg, sqlite_db, monkeypatch):
    """More rows than we put in means something else wrote to the target —
    equally worth stopping for."""
    _stub_target(monkeypatch, restored={"compat_rules": 5})
    res = mig.run(commit=True, sqlite_path=sqlite_db)
    assert res["status"] == "discrepancies"


def test_verification_rereads_the_target_rather_than_trusting_restore(
        target_pg, sqlite_db, monkeypatch):
    """_restore returning cleanly is not evidence the rows are there."""
    _stub_target(monkeypatch, restored={"compat_rules": 0})
    monkeypatch.setattr(mig, "_restore", lambda s, snap: ({}, {}))  # "success"
    assert mig.run(commit=True, sqlite_path=sqlite_db)["status"] == "discrepancies"


# ----------------------------------------------------------------- exit code

# Two separate tests rather than two _stub_target calls in one: the second
# stub would capture the FIRST stub as its "real" _snapshot and chain them.

def test_cli_exits_nonzero_on_discrepancies(target_pg, sqlite_db, monkeypatch):
    """A CI/cron caller must be able to tell a bad migration from a good one."""
    _stub_target(monkeypatch, restored={"compat_rules": 0})
    monkeypatch.setattr("sys.argv", ["migrate", "--commit",
                                     "--sqlite-path", str(sqlite_db)])
    assert mig.main() == 1


def test_cli_exits_zero_on_a_clean_migration(target_pg, sqlite_db, monkeypatch):
    _stub_target(monkeypatch, restored={"compat_rules": 1})
    monkeypatch.setattr("sys.argv", ["migrate", "--commit",
                                     "--sqlite-path", str(sqlite_db)])
    assert mig.main() == 0


def test_cli_exits_zero_on_a_dry_run(target_pg, sqlite_db, monkeypatch):
    monkeypatch.setattr(mig.build_mod, "run",
                        lambda: (_ for _ in ()).throw(AssertionError("dry run built")))
    monkeypatch.setattr("sys.argv", ["migrate", "--sqlite-path", str(sqlite_db)])
    assert mig.main() == 0


class _NullSession:
    def close(self):
        pass
