from __future__ import annotations

import logging

from src.config import Config
from src.db.init_db import init_db
from src.db.session import make_engine, make_session_factory
from src.jobs.opinion_job import run_opinion_job
from src.logging_setup import setup_logging
from src.posting.twitter_client import TwitterClient

logger = logging.getLogger(__name__)


def main() -> None:
    """
    One-shot runner for the opinion post pipeline.

    Railway cron schedule suggestion: 0 */4 * * *  (every 4 hours)
    Command: python -m src.opinion_main

    Opinion posts run less frequently than trend posts to avoid flooding
    the feed with takes — one every 4-6 hours is the right cadence.
    """
    setup_logging(Config.LOG_LEVEL)
    logger.info("Starting opinion-post job (dry_run=%s)", Config.DRY_RUN)

    Config.validate()

    engine = make_engine(Config.DATABASE_URL)  # type: ignore[arg-type]
    init_db(engine)
    SessionFactory = make_session_factory(engine)

    twitter = TwitterClient(Config)

    try:
        with SessionFactory() as session:
            result = run_opinion_job(session, twitter)
            logger.info(
                "Opinion post: published=%d skipped=%d errors=%d",
                result.published,
                result.skipped,
                result.errors,
            )
    except Exception:
        logger.exception("Opinion-post job error")
        raise


if __name__ == "__main__":
    main()
