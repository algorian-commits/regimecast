"""App database (M6): connection and schema migrations.

The API reads DATABASE_URL (Railway sets it from the Postgres service). Without it the
account endpoints answer 503 not_ready and the rest of the API works as before.

Migrations live in regimecast/db/migrations (Alembic) and run automatically the first
time the API touches the database, so a fresh Postgres needs no manual step:

    python -m regimecast.db upgrade      # the same by hand (uses DATABASE_URL)
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

import sqlalchemy as sa

MIGRATIONS = Path(__file__).with_name("migrations")

_lock = threading.Lock()
_engine: sa.Engine | None = None


def normalise_url(url: str) -> str:
    """Railway gives postgresql://...; SQLAlchemy needs to be told to use psycopg 3."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def make_engine(url: str) -> sa.Engine:
    url = normalise_url(url)
    if url.startswith("sqlite"):
        from sqlalchemy.pool import StaticPool

        eng = sa.create_engine(url, connect_args={"check_same_thread": False},
                               poolclass=StaticPool if ":memory:" in url or url == "sqlite://" else None)

        @sa.event.listens_for(eng, "connect")
        def _fk_on(dbapi_conn, _):   # SQLite ignores foreign keys (and ON DELETE CASCADE) unless asked
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
        return eng
    return sa.create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)


def upgrade(engine: sa.Engine) -> None:
    """Bring the schema to the newest migration (safe to call on every start)."""
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")


def get_engine() -> sa.Engine | None:
    """The migrated engine for DATABASE_URL, or None when no database is configured."""
    global _engine
    if _engine is None:
        url = os.environ.get("DATABASE_URL", "").strip()
        if not url:
            return None
        with _lock:
            if _engine is None:
                eng = make_engine(url)
                upgrade(eng)
                _engine = eng
    return _engine


def set_engine(engine: sa.Engine | None) -> None:
    """Use a given (already migrated) engine, e.g. a test database."""
    global _engine
    _engine = engine
