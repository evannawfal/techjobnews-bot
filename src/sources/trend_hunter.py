from __future__ import annotations

import datetime as dt
import hashlib
import logging
from dataclasses import dataclass, field
from urllib.parse import quote_plus

import feedparser
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Keywords that boost a headline's priority score
PRIORITY_KEYWORDS = {
    "hiring", "headcount", "workforce", "office", "pivot",
    "restructuring", "funding", "layoff", "layoffs", "jobs",
    "positions", "roles", "staff", "employees",
}

# Google News RSS — broad queries (no when:Nd; recency handled by scoring)
GOOGLE_NEWS_QUERIES = [
    "tech layoffs hiring news",
    "Nvidia Apple Microsoft OpenAI restructuring",
    "emerging tech trends jobs 2026",
    "AI workforce impact tech companies",
    "big tech hiring freeze cuts 2026",
]

# Hacker News Algolia queries
HN_QUERIES = [
    "tech layoffs",
    "AI hiring",
    "software engineer jobs",
    "tech company restructuring",
]

# Direct RSS feeds — updated frequently, no API key needed
_DIRECT_RSS_FEEDS: list[tuple[str, str]] = [
    ("TechCrunch",          "https://techcrunch.com/feed/"),
    ("The Verge",           "https://www.theverge.com/rss/index.xml"),
    ("Ars Technica",        "https://feeds.arstechnica.com/arstechnica/index"),
    ("VentureBeat",         "https://venturebeat.com/feed/"),
    ("Wired",               "https://www.wired.com/feed/rss"),
    ("MIT Tech Review",     "https://www.technologyreview.com/feed/"),
]

# Reddit subreddits — free JSON API, no auth required
_REDDIT_SOURCES: list[tuple[str, str]] = [
    ("layoffs",             "new"),
    ("technology",          "hot"),
    ("cscareerquestions",   "hot"),
    ("programming",         "hot"),
]

_GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
_HN_ALGOLIA     = "https://hn.algolia.com/api/v1/search_by_date"

# Drop articles older than this (recency score hits 0.0 beyond this point)
_MAX_AGE_HOURS = 72  # 3 days


@dataclass
class Headline:
    title: str
    url: str
    snippet: str
    source: str
    published_date: str | None
    published_datetime: dt.datetime | None = field(default=None, compare=False)
    video_url: str | None = field(default=None, compare=False)
    priority_score: float = field(default=0.0, init=False)
    recency_score: float = field(default=0.3, init=False)
    combined_score: float = field(default=0.0, init=False)
    content_hash: str = field(default="", init=False)

    def __post_init__(self) -> None:
        self.content_hash = hashlib.sha256(
            f"{self.title}|{self.url}".encode()
        ).hexdigest()
        self.priority_score = _compute_priority_score(self.title + " " + self.snippet)
        self.recency_score = _compute_recency_score(self.published_datetime)
        self.combined_score = 0.4 * self.priority_score + 0.6 * self.recency_score


def _compute_priority_score(text: str) -> float:
    text_lower = text.lower()
    matched = sum(1 for kw in PRIORITY_KEYWORDS if kw in text_lower)
    return matched / len(PRIORITY_KEYWORDS)


def _compute_recency_score(pub: dt.datetime | None) -> float:
    """0-1 score based on article age. Penalises older articles; 0.0 = too old to use."""
    if pub is None:
        return 0.3  # Unknown age — include but deprioritise
    now = dt.datetime.now(dt.timezone.utc)
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=dt.timezone.utc)
    age_hours = (now - pub).total_seconds() / 3600
    if age_hours < 0:
        return 0.5   # Clock skew / tz glitch
    if age_hours <= 2:
        return 1.0
    if age_hours <= 6:
        return 0.85
    if age_hours <= 12:
        return 0.65
    if age_hours <= 24:
        return 0.45
    if age_hours <= 48:
        return 0.25
    if age_hours <= _MAX_AGE_HOURS:
        return 0.10
    return 0.0  # Filter out


def _is_too_old(h: Headline) -> bool:
    return h.published_datetime is not None and h.recency_score == 0.0


def _strip_html(text: str) -> str:
    return BeautifulSoup(text, "lxml").get_text(" ", strip=True)


def _parse_feedparser_time(entry) -> dt.datetime | None:
    """Convert feedparser's published_parsed (time.struct_time) to a UTC datetime."""
    parsed = getattr(entry, "published_parsed", None)
    if parsed is None:
        return None
    try:
        return dt.datetime(*parsed[:6], tzinfo=dt.timezone.utc)
    except Exception:
        return None


# ── Source fetchers ───────────────────────────────────────────────────────────

def _search_google_news(query: str, max_results: int = 6) -> list[Headline]:
    url = _GOOGLE_NEWS_RSS.format(q=quote_plus(query))
    try:
        feed = feedparser.parse(url)
    except Exception:
        logger.exception("feedparser failed for Google News query=%r", query)
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
        if not _is_too_old(h):
            headlines.append(h)
    return headlines


def _search_direct_rss(feed_url: str, source_name: str, max_results: int = 6) -> list[Headline]:
    """Fetch from a direct outlet RSS feed (TechCrunch, The Verge, etc.)."""
    try:
        feed = feedparser.parse(feed_url)
    except Exception:
        logger.warning("feedparser failed for %s (%s)", source_name, feed_url)
        return []

    headlines = []
    for entry in feed.entries[:max_results]:
        title = entry.get("title", "")
        link  = entry.get("link", "")
        summary = _strip_html(entry.get("summary", "") or entry.get("content", [{}])[0].get("value", ""))
        pub_dt = _parse_feedparser_time(entry)

        if not title or not link:
            continue
        h = Headline(
            title=title, url=link, snippet=summary[:300],
            source=source_name, published_date=entry.get("published"),
            published_datetime=pub_dt,
        )
        if not _is_too_old(h):
            headlines.append(h)
    return headlines


def _search_reddit(subreddit: str, sort: str = "new", max_results: int = 6) -> list[Headline]:
    """Fetch from Reddit's free JSON API — no auth needed."""
    url = f"https://www.reddit.com/r/{subreddit}/{sort}.json?limit={max_results}"
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "techjobnews-bot/1.0"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        logger.warning("Reddit fetch failed for r/%s", subreddit)
        return []

    headlines = []
    for child in data.get("data", {}).get("children", []):
        post = child.get("data", {})
        title = post.get("title", "")
        if not title:
            continue

        external_url = post.get("url", "")
        permalink    = "https://www.reddit.com" + post.get("permalink", "")
        # For self-posts use the Reddit thread; for link-posts use the external URL
        link = external_url if (
            external_url and not external_url.startswith("https://www.reddit.com")
        ) else permalink

        created_utc = post.get("created_utc")
        pub_dt = (
            dt.datetime.fromtimestamp(created_utc, tz=dt.timezone.utc)
            if created_utc else None
        )
        snippet = post.get("selftext", "")[:300] if post.get("is_self") else ""

        h = Headline(
            title=title, url=link, snippet=snippet,
            source=f"Reddit r/{subreddit}", published_date=None,
            published_datetime=pub_dt,
        )
        if not _is_too_old(h):
            headlines.append(h)
    return headlines


def _search_hacker_news(query: str, max_results: int = 5) -> list[Headline]:
    cutoff = int(
        (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=_MAX_AGE_HOURS)).timestamp()
    )
    try:
        resp = requests.get(
            _HN_ALGOLIA,
            params={
                "query": query, "tags": "story",
                "numericFilters": f"created_at_i>{cutoff}",
                "hitsPerPage": max_results,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        logger.exception("HN Algolia failed for query=%r", query)
        return []

    headlines = []
    for hit in data.get("hits", []):
        external_url = hit.get("url", "")
        if not external_url:
            continue
        title = hit.get("title", "")
        if not title:
            continue

        pub_dt: dt.datetime | None = None
        try:
            pub_dt = dt.datetime.fromisoformat(
                hit.get("created_at", "").replace("Z", "+00:00")
            )
        except Exception:
            pass

        headlines.append(Headline(
            title=title, url=external_url,
            snippet=hit.get("story_text", "") or "",
            source="Hacker News", published_date=hit.get("created_at"),
            published_datetime=pub_dt,
        ))
    return headlines


# ── Main entry point ──────────────────────────────────────────────────────────

def hunt_headlines(max_results: int = 30) -> list[Headline]:
    """
    Stage A -- Discovery.

    Pulls from Google News RSS, direct outlet RSS feeds (TechCrunch, The Verge,
    Ars Technica, VentureBeat, Wired, MIT Tech Review), Reddit, and Hacker News.
    All free, no API keys required.

    Deduplicates by content_hash, drops articles older than 72h, then returns
    up to `max_results` headlines sorted by combined score
    (60% recency + 40% keyword priority).
    """
    seen_hashes: set[str] = set()
    all_headlines: list[Headline] = []

    def _add(h: Headline) -> None:
        if h.content_hash not in seen_hashes:
            seen_hashes.add(h.content_hash)
            all_headlines.append(h)

    for query in GOOGLE_NEWS_QUERIES:
        for h in _search_google_news(query, max_results=6):
            _add(h)

    for source_name, feed_url in _DIRECT_RSS_FEEDS:
        for h in _search_direct_rss(feed_url, source_name, max_results=6):
            _add(h)

    for subreddit, sort in _REDDIT_SOURCES:
        for h in _search_reddit(subreddit, sort=sort, max_results=5):
            _add(h)

    for query in HN_QUERIES:
        for h in _search_hacker_news(query, max_results=4):
            _add(h)

    all_headlines.sort(key=lambda h: h.combined_score, reverse=True)

    logger.info(
        "hunt_headlines: found %d unique headlines (window=%dh)",
        len(all_headlines), _MAX_AGE_HOURS,
    )
    return all_headlines[:max_results]
