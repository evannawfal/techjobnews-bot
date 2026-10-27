from __future__ import annotations

"""
Opinion Hunter — finds recent, debate-worthy headlines for the opinion post pipeline.

Reuses the shared fetchers from trend_hunter (direct RSS, Reddit, HN Algolia)
and adds controversy-focused Google News queries. Uses a wider time window
(5 days) since opinion-worthy debates remain relevant longer than news stories.
"""

import datetime as dt
import logging
from urllib.parse import quote_plus  # noqa: F401 (used via string formatting below)

import feedparser

from src.sources.trend_hunter import (
    Headline,
    _DIRECT_RSS_FEEDS,
    _REDDIT_SOURCES,
    HN_QUERIES,
    _search_direct_rss,
    _search_reddit,
    _search_hacker_news,
    _parse_feedparser_time,
    _strip_html,
)

logger = logging.getLogger(__name__)

# Opinion-specific Google News queries — targeting controversial/debate-worthy content
OPINION_GOOGLE_NEWS_QUERIES = [
    "tech CEO statement controversial 2026",
    "return to office RTO tech mandate 2026",
    "AI replace developers engineers jobs",
    "tech layoffs workers reaction statement",
    "big tech strategy controversy pivot",
    "remote work productivity debate tech",
    "tech industry hiring freeze talent",
    "AI automation impact software developers",
]

_GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"

# Opinion debates last longer — use a 5-day window
_OPINION_MAX_AGE_HOURS = 120  # 5 days


def _is_too_old_for_opinion(h: Headline) -> bool:
    """Looser age check than trend_hunter — allows up to 5 days."""
    if h.published_datetime is None:
        return False
    now = dt.datetime.now(dt.timezone.utc)
    pub = h.published_datetime
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=dt.timezone.utc)
    return (now - pub).total_seconds() / 3600 > _OPINION_MAX_AGE_HOURS


def _search_google_news_opinion(query: str, max_results: int = 6) -> list[Headline]:
    url = _GOOGLE_NEWS_RSS.format(q=quote_plus(query))
    try:
        feed = feedparser.parse(url)
    except Exception:
        logger.exception("feedparser failed for opinion query=%r", query)
        return []

    headlines = []
    for entry in feed.entries[:max_results]:
        title = entry.get("title", "")
        link  = entry.get("link", "")
        summary = _strip_html(entry.get("summary", ""))
        src = entry.get("source", {})
        source = src.get("title", "") if isinstance(src, dict) else ""
        pub_dt = _parse_feedparser_time(entry)

        if not title or not link:
            continue
        h = Headline(
            title=title, url=link, snippet=summary[:300],
            source=source, published_date=entry.get("published"),
            published_datetime=pub_dt,
        )
        if not _is_too_old_for_opinion(h):
            headlines.append(h)
    return headlines


def hunt_opinion_topics(max_results: int = 30) -> list[Headline]:
    """
    Find debate-worthy headlines for the opinion post pipeline.

    Pulls from opinion-focused Google News queries, plus the same direct RSS
    feeds and Reddit sources as trend_hunter, with a 5-day age window.
    Returns up to `max_results` deduplicated headlines sorted by combined score.
    """
    seen_hashes: set[str] = set()
    all_headlines: list[Headline] = []

    def _add(h: Headline) -> None:
        if h.content_hash not in seen_hashes:
            seen_hashes.add(h.content_hash)
            all_headlines.append(h)

    # Opinion-specific Google News queries
    for query in OPINION_GOOGLE_NEWS_QUERIES:
        for h in _search_google_news_opinion(query, max_results=6):
            _add(h)

    # Shared direct RSS feeds (TechCrunch, The Verge, etc.)
    for source_name, feed_url in _DIRECT_RSS_FEEDS:
        for h in _search_direct_rss(feed_url, source_name, max_results=5):
            if not _is_too_old_for_opinion(h):
                _add(h)

    # Reddit — great for controversy and worker/dev sentiment
    opinion_reddit = [
        ("cscareerquestions", "hot"),
        ("layoffs",           "new"),
        ("technology",        "hot"),
        ("programming",       "hot"),
        ("ExperiencedDevs",   "hot"),
    ]
    for subreddit, sort in opinion_reddit:
        for h in _search_reddit(subreddit, sort=sort, max_results=5):
            if not _is_too_old_for_opinion(h):
                _add(h)

    # HN for tech worker/dev opinion pieces
    for query in HN_QUERIES:
        for h in _search_hacker_news(query, max_results=4):
            if not _is_too_old_for_opinion(h):
                _add(h)

    all_headlines.sort(key=lambda h: h.combined_score, reverse=True)

    logger.info(
        "hunt_opinion_topics: found %d unique headlines (window=%dh)",
        len(all_headlines), _OPINION_MAX_AGE_HOURS,
    )
    return all_headlines[:max_results]
