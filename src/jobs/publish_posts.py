from __future__ import annotations
import datetime as dt
import logging
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.content.summary_generator import generate_summary_and_hook
from src.content.tweet_templates import render_single_alert
from src.db.models import NormalizedEvent, Post, PostCandidate
from src.posting.twitter_client import TwitterClient

logger = logging.getLogger(__name__)

class PublishResult:
    def __init__(self, *, published: int, errors: int, skipped: int):
        self.published = published
        self.errors = errors
        self.skipped = skipped

def publish_pending_posts(session: Session, twitter: TwitterClient, *, limit: int = 1) -> PublishResult:
    q = (
        select(PostCandidate, NormalizedEvent)
        .join(NormalizedEvent, NormalizedEvent.id == PostCandidate.normalized_event_id)
        .where(PostCandidate.status == "pending")
        .order_by(PostCandidate.created_at.asc())
        .limit(limit)
    )
    rows = session.execute(q).all()
    if not rows:
        return PublishResult(published=0, errors=0, skipped=0)

    published = 0
    errors = 0
    skipped = 0

    for cand, ev in rows:
        # EXPECTATION: render_single_alert now returns (main_text, reply_text)
        main_text, reply_text = render_single_alert(
            company=ev.company,
            employees=ev.employees,
            percentage=ev.percentage,
            source_url=ev.source_url,
            location=getattr(ev, "location", None),
            industry=getattr(ev, "industry", None),
            country=getattr(ev, "country", None),
        )

        # Build main tweet text with optional OpenAI-generated 1–2 sentence
        # summary + engagement hook, hard-capped at 500 characters.
        base_text = main_text
        max_len = 500
        if len(base_text) > max_len:
            base_text = base_text[:max_len].rstrip()
            full_main_text = base_text
        else:
            remaining = max_len - len(base_text) - 2  # for "\n\n"
            summary = ""
            if remaining > 0:
                summary = generate_summary_and_hook(
                    company=ev.company,
                    employees=ev.employees,
                    percentage=ev.percentage,
                    location=getattr(ev, "location", None),
                    industry=getattr(ev, "industry", None),
                    source_url=ev.source_url,
                    max_chars=remaining,
                )
            full_main_text = f"{base_text}\n\n{summary}" if summary else base_text

        try:
            # 1. Post Main Alert (with summary/hook appended if present)
            tweet_id = twitter.create_tweet(full_main_text)
            now = dt.datetime.now(dt.timezone.utc)

            if tweet_id:
                # 2. Post the Source Link in Reply (not subject to 280-char limit)
                twitter.create_tweet(reply_text, in_reply_to_id=tweet_id)
                
                # Update DB for the main post
                post = Post(
                    normalized_event_id=ev.id,
                    platform="x",
                    post_text=full_main_text,
                    status="posted",
                    platform_post_id=tweet_id,
                    posted_at=now
                )
                cand.status = "posted"
                published += 1
                session.add(post)
            else:
                # Handle Dry Run or failure
                cand.status = "posted"
                cand.reason = "dry_run"
                skipped += 1

            session.commit()
            logger.info("Thread Published: ID %s", tweet_id)

        except Exception as e:
            session.rollback()
            logger.exception("Failed thread publish for candidate_id=%s", cand.id)
            cand.status = "error"
            cand.reason = str(e)
            session.commit()
            errors += 1

    return PublishResult(published=published, errors=errors, skipped=skipped)