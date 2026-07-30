"""Is a REAL, populated onefcst database available to this test session?

Several test modules are integration tests against the actual built database
(`test_forecast_integration`, `test_category_*`, `test_tier2_forecasting`).
They can only run where a NAS rebuild has happened, so they carry a skip
guard. That guard used to be `config.DB_PATH.exists()`, which is wrong twice:

* An empty SQLite file counts. Any earlier test that touches
  `onefcst.db.engine` CREATES `app/data/onefcst.db` as a side effect of
  connecting, so on a fresh checkout (CI) the guard passes and 24 tests then
  fail on `no such table: items` or on empty result sets — a red suite for
  environment reasons, not code reasons.
* It ignores `DATABASE_URL` entirely. On the Postgres deployment the SQLite
  path is irrelevant, and its absence would skip tests that could in fact run.

So ask the database instead: does `items` exist, and does it hold rows? This
is one connection per session and works on either dialect.
"""
from sqlalchemy import inspect, text

from onefcst.db import engine


def _probe() -> bool:
    try:
        if not inspect(engine).has_table("items"):
            return False
        with engine.connect() as conn:
            return conn.execute(text("SELECT 1 FROM items LIMIT 1")).first() is not None
    except Exception:
        # Unreachable DB, missing driver, permissions — all mean "cannot run
        # the integration tests here", which is a skip, never an error.
        return False


HAS_BUILT_DB = _probe()

SKIP_REASON = "no populated onefcst database (needs a NAS rebuild)"
