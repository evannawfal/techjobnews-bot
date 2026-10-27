from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def _normalize_database_url(url: str) -> str:
    # Railway often provides postgres:// which SQLAlchemy treats differently.
    if url.startswith("postgres://"):
        return "postgresql+psycopg2://" + url.removeprefix("postgres://")
    if url.startswith("postgresql://"):
        return "postgresql+psycopg2://" + url.removeprefix("postgresql://")
    return url


def make_engine(database_url: str):
    return create_engine(_normalize_database_url(database_url), pool_pre_ping=True)


def make_session_factory(engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)

