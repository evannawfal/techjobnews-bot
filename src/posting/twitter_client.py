from __future__ import annotations

import logging
import tweepy
from src.config import Config

logger = logging.getLogger(__name__)

class TwitterClient:
    def __init__(self, cfg: type[Config]):
        self.cfg = cfg
        if cfg.DRY_RUN:
            self._client = None
            return

        self._client = tweepy.Client(
            consumer_key=cfg.X_API_KEY,
            consumer_secret=cfg.X_API_SECRET,
            access_token=cfg.X_ACCESS_TOKEN,
            access_token_secret=cfg.X_ACCESS_TOKEN_SECRET,
        )

    def create_tweet(self, text: str, in_reply_to_id: str | None = None) -> str | None:
        """
        Posts a tweet. If in_reply_to_id is provided, it posts as a reply.
        """
        if self.cfg.DRY_RUN:
            logger.info("DRY_RUN=1; skipping tweet publish. ReplyTo=%s Text=%r", in_reply_to_id, text)
            return "dry_run_id" # Return a dummy ID so the reply logic can trigger in dry run

        assert self._client is not None
        # in_reply_to_tweet_id is the parameter name for API v2
        resp = self._client.create_tweet(text=text, in_reply_to_tweet_id=in_reply_to_id)
        
        tweet_id = getattr(resp, "data", None) and resp.data.get("id")
        return str(tweet_id) if tweet_id else None