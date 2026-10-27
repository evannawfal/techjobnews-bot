from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.db.models import OpinionPost
from src.pipeline.opinion_post import run_opinion_post
from src.posting.twitter_client import TwitterClient

logger = logging.getLogger(__name__)


@dataclass
class OpinionJobResult:
    published: int = 0
    skipped: int = 0
    errors: int = 0


def run_opinion_job(session: Session, twitter: TwitterClient) -> OpinionJobResult:
    """
    One-shot job: run the opinion pipeline, persist the result,
    and publish to X as a main tweet + reply with source.
    """
    result = OpinionJobResult()

    try:
        opinion = run_opinion_post(session)
    except Exception:
        logger.exception("Opinion pipeline raised an unexpected error")
        result.errors += 1
        return result

    if opinion is None:
        logger.info("No opinion content to post this cycle")
        result.skipped += 1
        return result

    record = OpinionPost(
        content_hash=opinion.content_hash,
        topic_headline=opinion.headline.title,
        topic_url=opinion.headline.url,
        stance=opinion.stance,
        tweet_text=opinion.tweet_text,
        reply_text=opinion.reply_text,
        status="pending",
    )

    try:
        session.add(record)
        session.flush()
    except IntegrityError:
        session.rollback()
        logger.info("OpinionPost already exists for this content hash; skipping")
        result.skipped += 1
        return result
    except Exception:
        session.rollback()
        logger.exception("Failed to persist OpinionPost")
        result.errors += 1
        return result

    try:
        post_id = twitter.create_tweet(opinion.tweet_text)

        record.platform_post_id = post_id
        record.status = "dry_run" if post_id == "dry_run_id" else "posted"
        record.posted_at = dt.datetime.now(dt.timezone.utc)

        if post_id and opinion.reply_text:
            twitter.create_tweet(opinion.reply_text, in_reply_to_id=post_id)

        session.commit()
        logger.info("Opinion post published (stance=%s): %r", opinion.stance, opinion.tweet_text[:80])
        result.published += 1

    except Exception:
        logger.exception("Failed to post opinion tweet")
        record.status = "error"
        record.error = "post failed"
        session.commit()
        result.errors += 1

    return result
