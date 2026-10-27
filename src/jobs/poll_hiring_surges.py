from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.content.hiring_templates import render_hiring_surge
from src.content.summary_generator import generate_hiring_summary_and_hook
from src.db.models import HiringSnapshot
from src.posting.twitter_client import TwitterClient
from src.pipeline.hiring_surges import detect_hiring_surges

logger = logging.getLogger(__name__)


class HiringPublishResult:
    def __init__(self, *, published: int, skipped: int, errors: int):
        self.published = published
        self.skipped = skipped
        self.errors = errors


def _sanitize_tweet_text(text: str, max_len: int = 500) -> str:
    """Ensure tweet is non-empty and within length; never return invalid content."""
    if not text or not isinstance(text, str):
        return ""
    t = text.strip()
    if not t:
        return ""
    if len(t) > max_len:
        t = t[:max_len].rstrip()
    return t


def poll_and_publish_hiring_surges(session: Session, twitter: TwitterClient) -> HiringPublishResult:
    """
    Detect hiring surges for configured companies and publish at most ONE
    surge post per cron cycle. Only surges detected today that have not yet
    been posted are eligible. Failures (stale endpoints, DB errors) are
    isolated so the app does not crash and data stays consistent.
    """
    today = dt.date.today()

    try:
        surged_snaps = detect_hiring_surges(session, today=today)
    except Exception:  # noqa: BLE001
        logger.exception("Hiring surge detection failed; skipping publish this cycle")
        return HiringPublishResult(published=0, skipped=0, errors=0)

    if not surged_snaps:
        return HiringPublishResult(published=0, skipped=0, errors=0)

    published = 0
    skipped = 0
    errors = 0
    max_len = 500

    for snap in surged_snaps:
        # Only post surges detected today (defense-in-depth against stale data).
        if snap.snapshot_date != today:
            skipped += 1
            continue

        if snap.surge_posted_at is not None:
            skipped += 1
            continue

        # 7-day cross-day cooldown: prevent reposting the same company's sustained surge daily.
        # A company that spikes from 200→350 jobs stays "surging" until the 21-day baseline
        # catches up (~2-3 weeks). Without this, we'd post about it every single day.
        try:
            cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)
            recent_post_at = session.scalar(
                select(HiringSnapshot.surge_posted_at)
                .where(
                    HiringSnapshot.company == snap.company,
                    HiringSnapshot.surge_posted_at >= cutoff,
                    HiringSnapshot.snapshot_date < today,
                )
                .order_by(HiringSnapshot.surge_posted_at.desc())
                .limit(1)
            )
            if recent_post_at is not None:
                days_since = (dt.datetime.now(dt.timezone.utc) - recent_post_at).days
                logger.info("Cooldown active for %s: last surge posted %dd ago", snap.company, days_since)
                skipped += 1
                continue
        except Exception:  # noqa: BLE001
            logger.debug("Cooldown check failed for %s; proceeding", snap.company)

        try:
            base_text = render_hiring_surge(snap)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to render hiring surge for %s", getattr(snap, "company", "?"))
            errors += 1
            continue

        base_text = _sanitize_tweet_text(base_text, max_len)
        if not base_text:
            logger.warning("Empty hiring surge text for %s; skipping", getattr(snap, "company", "?"))
            errors += 1
            continue

        if len(base_text) >= max_len:
            full_text = base_text
        else:
            remaining = max_len - len(base_text) - 2
            summary = ""
            if remaining > 0:
                try:
                    summary = generate_hiring_summary_and_hook(
                        company=snap.company,
                        total_open_roles=snap.total_open_roles,
                        engineering_open_roles=snap.engineering_open_roles,
                        ai_open_roles=snap.ai_open_roles,
                        max_chars=remaining,
                    )
                except Exception:  # noqa: BLE001
                    logger.debug("Hiring summary generation failed for %s", snap.company)
                if summary:
                    summary = _sanitize_tweet_text(summary, remaining)
            full_text = f"{base_text}\n\n{summary}" if summary else base_text

        full_text = _sanitize_tweet_text(full_text, max_len)
        if not full_text:
            errors += 1
            continue

        try:
            tweet_id = twitter.create_tweet(full_text)
            now_ts = dt.datetime.now(dt.timezone.utc)
            if tweet_id and tweet_id != "dry_run_id":
                snap.surge_posted_at = now_ts
                session.add(snap)
                session.commit()
                published += 1
                logger.info("Hiring surge posted for %s, tweet_id=%s", snap.company, tweet_id)
                break  # One post per cron cycle — remaining surges will be picked up in later cycles
            else:
                skipped += 1
        except Exception:  # noqa: BLE001
            session.rollback()
            errors += 1
            logger.exception("Failed to publish hiring surge for %s", snap.company)

    return HiringPublishResult(published=published, skipped=skipped, errors=errors)

