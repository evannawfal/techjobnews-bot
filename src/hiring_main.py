from __future__ import annotations

import logging

from src.config import Config
from src.db.init_db import init_db
from src.db.session import make_engine, make_session_factory
from src.jobs.poll_hiring_surges import poll_and_publish_hiring_surges
from src.logging_setup import setup_logging
from src.posting.twitter_client import TwitterClient


logger = logging.getLogger(__name__)


def main() -> None:
    """
    One-shot runner for hiring surges.

    Intended to be used as a separate Railway cron job, e.g.:
        python -m src.hiring_main
    """
    setup_logging(Config.LOG_LEVEL)
    logger.info("Starting hiring-surges job (dry_run=%s)", Config.DRY_RUN)

    Config.validate()

    engine = make_engine(Config.DATABASE_URL)  # type: ignore[arg-type]
    init_db(engine)
    SessionFactory = make_session_factory(engine)

    twitter = TwitterClient(Config)

    try:
        with SessionFactory() as session:
            result = poll_and_publish_hiring_surges(session, twitter)
            logger.info(
                "Hiring surges: published=%d skipped=%d errors=%d",
                result.published,
                result.skipped,
                result.errors,
            )
    except Exception:  # noqa: BLE001
        logger.exception("Hiring-surges job error")
        raise


if __name__ == "__main__":
    main()

