from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .models import Base

logger = logging.getLogger(__name__)

# Incremental schema changes that `create_all` cannot handle (adding columns to
# existing tables).  Each entry is idempotent — safe to run on every startup.
_MIGRATIONS: list[str] = [
    "ALTER TABLE trend_posts ADD COLUMN IF NOT EXISTS video_url TEXT",
]


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(bind=engine)
    _run_migrations(engine)


def _run_migrations(engine: Engine) -> None:
    with engine.begin() as conn:
        for sql in _MIGRATIONS:
            try:
                conn.execute(text(sql))
                logger.debug("Migration applied: %.60s", sql)
            except Exception as exc:
                # Column already exists or table doesn't exist yet — both are fine.
                logger.debug("Migration skipped (%s): %.60s", type(exc).__name__, sql)
