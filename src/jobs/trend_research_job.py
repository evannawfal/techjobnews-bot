from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.db.models import TrendPost
from src.pipeline.trend_research import run_trend_research
from src.posting.twitter_client import TwitterClient

logger = logging.getLogger(__name__)


@dataclass
class TrendJobResult:
    published: int = 0
    skipped: int = 0
    errors: int = 0


def run_trend_research_job(session: Session, twitter: TwitterClient) -> TrendJobResult:
    """
    One-shot job: run the 3-stage trend-research pipeline, persist the result,
    and publish to X as a main tweet + reply with source.
    """
    result = TrendJobResult()

    try:
        research = run_trend_research(session)
    except Exception:
        logger.exception("Trend research pipeline raised an unexpected error")
        result.errors += 1
        return result

    if research is None:
        logger.info("No trend content to post this cycle")
        result.skipped += 1
        return result

    trend_post = TrendPost(
        content_hash=research.headline.content_hash,
        headline_title=research.headline.title,
        headline_url=research.headline.url,
        headline_source=research.headline.source,
        synthesis=research.synthesis,
        tweet_text=research.tweet_text,
        reply_text=research.reply_text,
        video_url=research.video_url,
        status="pending",
    )

    try:
        session.add(trend_post)
        session.flush()  # Catch unique-constraint violations before posting
    except IntegrityError:
        session.rollback()
        logger.info("TrendPost already exists for this headline; skipping")
        result.skipped += 1
        return result
    except Exception:
        session.rollback()
        logger.exception("Failed to persist TrendPost")
        result.errors += 1
        return result

    # Post to X
    try:
        post_id = twitter.create_tweet(research.tweet_text)

        trend_post.platform_post_id = post_id
        trend_post.status = "dry_run" if post_id == "dry_run_id" else "posted"
        trend_post.posted_at = dt.datetime.now(dt.timezone.utc)

        # Reply with source attribution
        if post_id and research.reply_text:
            twitter.create_tweet(research.reply_text, in_reply_to_id=post_id)

        session.commit()
        logger.info("Trend post published: %r", research.tweet_text[:80])
        result.published += 1

    except Exception:
        logger.exception("Failed to post trend tweet")
        trend_post.status = "error"
        trend_post.error = "post failed"
        session.commit()
        result.errors += 1

    return result
