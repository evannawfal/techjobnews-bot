from __future__ import annotations

import logging

# Load the .env file into the environment before loading your custom config
from dotenv import load_dotenv
load_dotenv()

from src.config import Config
from src.db.init_db import init_db
from src.db.session import make_engine, make_session_factory
from src.jobs.poll_layoffs import poll_layoffs
from src.jobs.publish_posts import publish_pending_posts
from src.logging_setup import setup_logging
from src.posting.twitter_client import TwitterClient

logger = logging.getLogger(__name__)

def main() -> None:
    setup_logging(Config.LOG_LEVEL)
    logger.info("Starting x-bot (single-run mode, dry_run=%s)", Config.DRY_RUN)

    Config.validate()

    engine = make_engine(Config.DATABASE_URL)  # type: ignore[arg-type]
    init_db(engine)
    SessionFactory = make_session_factory(engine)

    twitter = TwitterClient(Config)

    # REMOVED: while True loop and time.sleep
    try:
        # Step 1: Check for new layoffs
        with SessionFactory() as session:
            poll_result = poll_layoffs(session)
            logger.info(
                "Poll done: fetched=%d inserted_normalized=%d inserted_candidates=%d",
                poll_result.fetched,
                poll_result.inserted_normalized,
                poll_result.inserted_candidates,
            )

        # Step 2: Publish one (or more) pending layoff posts
        with SessionFactory() as session:
            pub_result = publish_pending_posts(session, twitter)
            if pub_result.published or pub_result.errors or pub_result.skipped:
                logger.info(
                    "Publish done: published=%d skipped=%d errors=%d",
                    pub_result.published,
                    pub_result.skipped,
                    pub_result.errors,
                )

    except Exception:
        logger.exception("Single-run execution error")
        # Optional: you could raise the exception here to let Railway know the job failed
        raise

    logger.info("Cycle complete. Exiting to save Railway credits.")

if __name__ == "__main__":
    main()