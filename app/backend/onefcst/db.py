"""DB session factory. SQLite by default (local/WSL dev); set DATABASE_URL
to a postgresql+psycopg://... URL for the DGX/production deployment — the
schema (models.py) uses only portable SQLAlchemy column types, verified
2026-07-21 (no raw SQL, no SQLite-specific types anywhere in the app), so
this is the only file that needs to change for that migration."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from .config import DATABASE_URL, DB_PATH
from .models import Base

_url = DATABASE_URL or f"sqlite:///{DB_PATH}"
_is_sqlite = _url.startswith("sqlite")

# NullPool for SQLite: SQLite connections are cheap and the API's report
# endpoints can hold a session for minutes — a capped QueuePool exhausts
# under the SPA's concurrent requests and every call then times out (empty
# dashboard). Postgres wants the opposite — a real pool, not a fresh
# connection per session — so this only applies to the SQLite path;
# omitting poolclass for Postgres lets SQLAlchemy use its own QueuePool
# default.
engine = (create_engine(_url, future=True, poolclass=NullPool) if _is_sqlite
         else create_engine(_url, future=True))
SessionLocal = sessionmaker(bind=engine, future=True)


def init_db() -> None:
    Base.metadata.create_all(engine)


def get_session():
    return SessionLocal()
