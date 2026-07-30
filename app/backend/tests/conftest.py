"""Shared test setup.

The developer's real app/backend/.env (auto-loaded by config.py) may set
ONEFCST_API_KEY / rate limiting for the deployed server — which would make
every TestClient request without a header 401 and turn the suite red for
environment reasons, not code reasons (happened 2026-07-23 when the key was
first added to .env). Neutralize both for every test; tests that exercise
auth explicitly (test_auth.py) monkeypatch their own key on top.

Also provides the dual-dialect switch — see _pg_backed_sqlite_engines below.
"""
import os
import uuid

import pytest
import sqlalchemy

from onefcst import config
from onefcst.api import auth as auth_mod
from onefcst.api import rate_limit as rate_limit_mod

# Set this to run the WHOLE suite against a real PostgreSQL instead of
# SQLite, e.g.
#   ONEFCST_TEST_DATABASE_URL=postgresql+psycopg://onefcst:devpass@localhost:55432/onefcst \
#       python3 -m pytest tests/ -q
TEST_DATABASE_URL = os.environ.get("ONEFCST_TEST_DATABASE_URL", "")


@pytest.fixture(autouse=True)
def _no_env_auth(monkeypatch):
    """Neutralise every auth mechanism the developer's .env might switch on.

    The suite must test the code, not the machine it runs on. Turning on SSO in
    app/backend/.env immediately failed 10 endpoint tests that (correctly) send
    no token — a false alarm that would otherwise land in CI as a mystery, and
    worse, would tempt someone to "fix" it by loosening the gate.

    Tests that want a mechanism ON opt in by setting it themselves
    (tests/test_entra_gate.py's `sso_on`); those fixtures run after this one.
    """
    monkeypatch.setattr(auth_mod, "API_KEY", "")
    monkeypatch.setattr(config, "API_KEY", "")
    monkeypatch.setattr(rate_limit_mod, "RATE_LIMIT_PER_MINUTE", 0)
    monkeypatch.setattr(config, "ENTRA_TENANT_ID", "")
    monkeypatch.setattr(config, "ENTRA_CLIENT_ID", "")
    monkeypatch.setattr(config, "ENTRA_ALLOWED_GROUPS", [])


@pytest.fixture(autouse=True, scope="session")
def _pg_backed_sqlite_engines():
    """Redirect every test's own `create_engine("sqlite://")` at PostgreSQL
    when ONEFCST_TEST_DATABASE_URL is set.

    Why a shim rather than editing the test files: 22 test modules each
    build their own in-memory engine inline. Post-migration that meant the
    suite proved nothing about the database we actually run — including
    test_mismatch.py, which covers the very code path where a SQLite-only
    function (func.julianday) had been hiding. Rewriting 22 fixtures by hand
    is a large mechanical diff with real room for transcription mistakes,
    for no behavioural gain; this keeps them untouched and green on SQLite
    by default, while letting CI run the identical suite on Postgres.

    Isolation: `sqlite://` is in-memory and therefore private per engine.
    A shared Postgres is not, so each redirected engine gets its OWN schema
    via schema_translate_map — the models declare no explicit schema, so
    mapping None is enough to scope every table. NullPool keeps connections
    from being reused across tests. Schemas are dropped at session end.
    """
    if not TEST_DATABASE_URL:
        yield
        return

    real_create_engine = sqlalchemy.create_engine
    created: list[str] = []

    # Only IN-MEMORY sqlite is redirected. A test that asks for a sqlite
    # FILE means it deliberately (test_migrate_to_postgres builds a real
    # SQLite source to migrate FROM; test_db_backup exercises the sqlite
    # backup path) — redirecting those breaks the very thing under test.
    IN_MEMORY = {"sqlite://", "sqlite:///:memory:", "sqlite+pysqlite://"}

    def create_engine(url, *args, **kwargs):
        if str(url) not in IN_MEMORY:
            return real_create_engine(url, *args, **kwargs)
        schema = f"test_{uuid.uuid4().hex[:12]}"
        kwargs.pop("poolclass", None)
        eng = real_create_engine(TEST_DATABASE_URL, *args,
                                 poolclass=sqlalchemy.pool.NullPool, **kwargs)
        with eng.begin() as conn:
            conn.execute(sqlalchemy.text(f'CREATE SCHEMA "{schema}"'))
        created.append(schema)
        return eng.execution_options(schema_translate_map={None: schema})

    sqlalchemy.create_engine = create_engine
    # The 22 modules do `from sqlalchemy import create_engine`, which binds
    # the name at IMPORT time — patching sqlalchemy.create_engine alone does
    # nothing for them. Patch the already-imported test modules too.
    # Matched on "test" (module names are `test_fva`, not `tests.test_fva` —
    # there is no tests/__init__.py), and deliberately NOT onefcst.db, whose
    # own engine must keep following DATABASE_URL rather than this override.
    import sys
    patched = []
    for mod in list(sys.modules.values()):
        if (getattr(mod, "__name__", "").startswith("test")
                and getattr(mod, "create_engine", None) is real_create_engine):
            mod.create_engine = create_engine
            patched.append(mod)

    try:
        yield
    finally:
        sqlalchemy.create_engine = real_create_engine
        for mod in patched:
            mod.create_engine = real_create_engine
        if created:
            cleanup = real_create_engine(TEST_DATABASE_URL,
                                         poolclass=sqlalchemy.pool.NullPool)
            # One transaction PER schema, deliberately. DROP SCHEMA CASCADE
            # takes a lock on every object it removes, so dropping all ~22
            # test schemas (16 tables each) in a single transaction needs
            # 350+ locks and dies with "out of shared memory" once
            # max_locks_per_transaction is exceeded — which surfaces as a
            # confusing teardown ERROR on whichever test happened to run
            # last. Committing between schemas releases the locks.
            for schema in created:
                try:
                    with cleanup.begin() as conn:
                        conn.execute(sqlalchemy.text(
                            f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
                except Exception:              # noqa: BLE001 — cleanup only
                    pass
