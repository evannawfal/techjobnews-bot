from __future__ import annotations

import logging

from src.config import Config
from src.db.init_db import init_db
from src.db.session import make_engine, make_session_factory
from src.jobs.trend_research_job import run_trend_research_job
from src.logging_setup import setup_logging
from src.posting.twitter_client import TwitterClient

logger = logging.getLogger(__name__)


def main() -> None:
    """
    One-shot runner for the agentic trend-research pipeline.

    Railway cron schedule: 0 * * * *  (every 60 minutes)
    Command: python -m src.trend_main
    """
    setup_logging(Config.LOG_LEVEL)
    logger.info("Starting trend-research job (dry_run=%s)", Config.DRY_RUN)

    Config.validate()

    engine = make_engine(Config.DATABASE_URL)  # type: ignore[arg-type]
    init_db(engine)
    SessionFactory = make_session_factory(engine)

    twitter = TwitterClient(Config)

    try:
        with SessionFactory() as session:
            result = run_trend_research_job(session, twitter)
            logger.info(
                "Trend research: published=%d skipped=%d errors=%d",
                result.published,
                result.skipped,
                result.errors,
            )
    except Exception:
        logger.exception("Trend-research job error")
        raise


if __name__ == "__main__":
    main()
