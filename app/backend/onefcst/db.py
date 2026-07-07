"""DB session factory. SQLite for POC; schema is PostgreSQL-portable."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from .config import DB_PATH
from .models import Base

# NullPool: SQLite connections are cheap and the API's report endpoints can
# hold a session for minutes — a capped QueuePool exhausts under the SPA's
# concurrent requests and every call then times out (empty dashboard).
engine = create_engine(f"sqlite:///{DB_PATH}", future=True, poolclass=NullPool)
SessionLocal = sessionmaker(bind=engine, future=True)


def init_db() -> None:
    Base.metadata.create_all(engine)


def get_session():
    return SessionLocal()
