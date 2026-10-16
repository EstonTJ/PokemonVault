from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


def connect_args_for(url: str, *, for_migrations: bool = False) -> dict:
    if url.startswith("sqlite"):
        return {"check_same_thread": False, "timeout": 30}
    if url.startswith("postgresql"):
        # Without a timeout, an unreachable database makes startup hang silently forever.
        args = {"connect_timeout": settings.db_connect_timeout_s}
        if for_migrations:
            # Fail instead of waiting indefinitely if another instance holds a table lock during a rollout.
            args["options"] = f"-c lock_timeout={settings.db_lock_timeout_s * 1000}"
        return args
    return {}


def make_engine(url: str):
    is_pg = url.startswith("postgresql")
    # Hosted PostgreSQL (Replit/Neon) drops idle connections; pre-ping replaces dead ones transparently.
    eng = create_engine(url, connect_args=connect_args_for(url), future=True, pool_pre_ping=is_pg)
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _pragmas(dbapi_conn, _):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()
    return eng


engine = make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def configure(url: str) -> None:
    """Re-point the module at another database (used by tests and scripts)."""
    global engine
    engine = make_engine(url)
    SessionLocal.configure(bind=engine)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
