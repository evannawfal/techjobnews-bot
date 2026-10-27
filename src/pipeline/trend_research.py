from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote_plus

import requests
from openai import OpenAI
from sqlalchemy.orm import Session

from src.config import Config
from src.db.models import HiringSnapshot, NormalizedEvent, TrendPost
from src.sources.trend_hunter import Headline, hunt_headlines

logger = logging.getLogger(__name__)

_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "in", "on", "at", "to",
    "for", "of", "and", "or", "that", "this", "with", "its", "has", "have",
    "from", "by", "as", "it", "be", "will", "not", "but", "new",
}


@dataclass
class TrendResearchResult:
    headline: Headline
    synthesis: str            # Stage B output
    tweet_text: str           # Stage C main tweet (no URL)
    reply_text: str           # Stage C reply with source attribution
    is_confirmed: bool        # Stage C truth-check result
    video_url: str | None = field(default=None)  # YouTube video if found


def _search_youtube(query: str, timeout: int = 8) -> str | None:
    """
    Search YouTube for a video related to `query` using the public search page.
    No API key required. Returns the first video URL found, or None.
    """
    try:
        resp = requests.get(
            "https://www.youtube.com/results",
            params={"search_query": query},
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                )
            },
            timeout=timeout,
        )
        resp.raise_for_status()
    except Exception:
        logger.warning("YouTube search failed for query=%r", query)
        return None

    # YouTube embeds a JSON blob containing videoId values in the page source
    match = re.search(r'"videoId":"([a-zA-Z0-9_-]{11})"', resp.text)
    if match:
        return f"https://www.youtube.com/watch?v={match.group(1)}"
    return None


def _headline_keywords(title: str) -> set[str]:
    return {
        w.lower()
        for w in re.findall(r"\w+", title)
        if len(w) > 3 and w.lower() not in _STOPWORDS
    }


def _topic_overlap(title1: str, title2: str) -> float:
    """Jaccard-style overlap of significant keywords between two headlines."""
    kw1, kw2 = _headline_keywords(title1), _headline_keywords(title2)
    if not kw1 or not kw2:
        return 0.0
    return len(kw1 & kw2) / min(len(kw1), len(kw2))


def _is_duplicate_topic(
    headline: Headline,
    recent_titles: list[str],
    threshold: float = 0.5,
) -> bool:
    """Return True if the headline covers a topic already posted recently."""
    return any(_topic_overlap(headline.title, t) >= threshold for t in recent_titles)


def _get_openai() -> Optional[OpenAI]:
    api_key = Config.OPENAI_API_KEY
    if not api_key:
        logger.warning("OPENAI_API_KEY not set; trend research unavailable")
        return None
    return OpenAI(api_key=api_key)


def _get_recent_bot_context(session: Session) -> str:
    """Pull last 7 days of layoff + hiring-surge data to ground Stage B synthesis."""
    cutoff = dt.date.today() - dt.timedelta(days=7)

    layoffs = (
        session.query(NormalizedEvent)
        .filter(NormalizedEvent.event_date >= cutoff)
        .order_by(NormalizedEvent.event_date.desc())
        .limit(5)
        .all()
    )

    surges = (
        session.query(HiringSnapshot)
        .filter(
            HiringSnapshot.snapshot_date >= cutoff,
            HiringSnapshot.surge_flag == True,  # noqa: E712
        )
        .order_by(HiringSnapshot.snapshot_date.desc())
        .limit(5)
        .all()
    )

    lines = ["Recent bot data (last 7 days):"]

    if layoffs:
        lines.append("\nLayoffs tracked:")
        for ev in layoffs:
            emp = f"{ev.employees:,}" if ev.employees else "undisclosed"
            lines.append(f"  - {ev.company}: {emp} employees, {ev.event_date}")

    if surges:
        lines.append("\nHiring surges detected:")
        for s in surges:
            lines.append(f"  - {s.company}: {s.total_open_roles} open roles, {s.snapshot_date}")

    if not layoffs and not surges:
        lines.append("  (no data in the last 7 days)")

    return "\n".join(lines)


def _run_stage_b(
    client: OpenAI,
    headlines: list[Headline],
    bot_context: str,
    model: str,
) -> tuple[Headline, str]:
    """
    Stage B — Synthesis & Correlation.

    Picks the single most newsworthy headline and produces 2-3 sentences
    of analysis tying the trend to the tech job market.
    Returns (selected_headline, synthesis_text).
    """
    headline_list = "\n".join(
        f"{i + 1}. [{h.source}] {h.title} — {h.snippet[:200]}"
        for i, h in enumerate(headlines)
    )

    prompt = (
        "You are a data-driven analyst covering the tech job market. "
        "You have access to real-time headlines and live layoff/hiring data from an automated tracking bot.\n\n"
        f"HEADLINES (sorted by keyword relevance):\n{headline_list}\n\n"
        f"{bot_context}\n\n"
        "Your tasks:\n"
        "1. Select the SINGLE most newsworthy headline that best connects a tech trend to the job market.\n"
        "2. Answer: Why does this trend matter for tech workers?\n"
        "3. Answer: Is this a signal of a hiring surge in a specific sub-niche "
        "(e.g. Energy AI, Edge Computing, Defense Tech, Autonomous Vehicles)?\n"
        "4. Synthesize into 2-3 tight sentences of analysis.\n\n"
        "Output format (exactly, no extra text):\n"
        "HEADLINE_INDEX: <number 1-N>\n"
        "SYNTHESIS: <2-3 sentences of analysis>\n"
    )

    try:
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a data-driven tech industry analyst."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=350,
            temperature=0.4,
        )
        raw = (completion.choices[0].message.content or "").strip()
    except Exception:
        logger.exception("Stage B OpenAI call failed")
        return headlines[0], ""

    idx = 0
    synthesis = ""
    for line in raw.splitlines():
        if line.startswith("HEADLINE_INDEX:"):
            try:
                idx = int(line.split(":", 1)[1].strip()) - 1
                idx = max(0, min(idx, len(headlines) - 1))
            except ValueError:
                idx = 0
        elif line.startswith("SYNTHESIS:"):
            synthesis = line.split(":", 1)[1].strip()

    return headlines[idx], synthesis


_VOICE_SYSTEM_PROMPT = """\
You write for an X account that tracks the tech job market: layoffs, hiring surges, and the industry shifts driving them.

YOUR VOICE:
Sharp industry insider. You cut through corporate PR and tell people what the numbers actually mean for workers. \
Informed, direct, slightly editorial, but never sensational. You are NOT a press release. You are NOT a bot reciting facts.

STRUCTURE (follow this every time):
Line 1 -- THE HOOK. The key insight or pattern in one punchy line. Under 130 characters. \
Standalone -- someone who only reads this line should walk away knowing something.
[blank line]
Lines 2-3 -- THE BODY. 1-2 short lines of context or supporting detail. Max 8-10 words per line.
[blank line]
Last line -- THE CLOSER. An open question that invites a reply. Natural, not forced. \
Under 60 characters. (Replies are the highest-value engagement signal on X.)

STYLE RULES:
- Front-load the insight. Key point in line 1, always. Never build up to it.
- Do NOT open with the company name. Open with what happened or what it means.
- Active voice. Short sentences. No padding.
- Be specific with numbers. "700 roles" beats "significant workforce reduction."
- Connect micro to macro when possible: "Third round of cuts this quarter."
- Slightly editorial is fine: "the signal is hard to ignore", "worth watching."

FORMAT RULES:
- Total tweet: max 260 characters including line breaks.
- URL goes in the REPLY, never the main tweet.
- 0 hashtags, or 1 at the very end. Only #TechJobs or #TechLayoffs if used.
- BANNED emojis: ✨ 🚀 🤖 🌟 💫 🔥 -- do not use these, ever.
- Allowed emojis: 📊 📉 📈 only if they genuinely add clarity. Sparingly.
- Do NOT start with "Breaking:", "Just in:", or "THREAD:".
- NEVER use em dashes (--) or the character (—). Use a period, comma, or colon instead.

GOOD EXAMPLES (copy this structure exactly):

Example 1:
Salesforce cut 700 more roles. Mostly sales ops and customer success, not AI.

The pattern is consistent: headcount contracts everywhere except AI product teams.

How long before that changes?

Example 2:
OpenAI just posted 80+ new engineering roles. Almost all in infra and safety.

Not a hiring freeze. A company betting everything on compute and alignment.

What does that tell you about their priorities?

Example 3:
Three Fortune 500s restructured their tech orgs this week. Each press release said "AI transformation."

The jobs are not disappearing. The descriptions are changing. Fast.

Are you reskilling, or waiting?

BAD EXAMPLES (never write like this):
- "Meta has announced a reduction in workforce affecting 200 employees." -- passive, press release
- "Breaking: Big tech is in trouble! 🔥🚀" -- banned emojis, sensational
- "This is a significant development in the tech industry landscape." -- filler, says nothing
- All text on one unbroken line with no spacing -- hard to read, kills engagement
- "Salesforce cut 700 roles — a significant move..." -- banned em dash
"""


def _run_stage_c(
    client: OpenAI,
    headline: Headline,
    synthesis: str,
    model: str,
) -> tuple[str, str, bool]:
    """
    Stage C -- Validation & Formatting.

    Validates whether the headline is confirmed or rumor, then formats
    the main tweet and reply with source attribution.
    If headline.video_url is set, the reply uses the video as the primary link.
    Returns (tweet_text, reply_text, is_confirmed).
    """
    if headline.video_url:
        reply_instruction = (
            f"REPLY: Include both links. "
            f"Video (preferred, put first): {headline.video_url} | "
            f"Article: {headline.url} — keep the REPLY under 200 chars total."
        )
    else:
        reply_instruction = (
            f"REPLY: <source attribution, max 200 chars, MUST include: {headline.url}>"
        )

    prompt = (
        "TRUTH-CHECK FIRST:\n"
        "- If this headline is unconfirmed or sourced from a rumor, set IS_CONFIRMED to 'no' "
        "and prepend 'Reported:' to the first line of your tweet.\n"
        "- If confirmed by an official statement or multiple outlets, set IS_CONFIRMED to 'yes'.\n\n"
        f"Headline: {headline.title}\n"
        f"Source outlet: {headline.source}\n"
        f"Snippet: {headline.snippet[:400]}\n"
        f"Analysis to incorporate: {synthesis}\n\n"
        "Now write the tweet using the voice, structure, and rules in your system prompt.\n\n"
        "Output format (copy exactly -- the tweet is multi-line):\n"
        "IS_CONFIRMED: yes|no\n"
        "TWEET_START\n"
        "[hook line]\n"
        "\n"
        "[body line(s)]\n"
        "\n"
        "[closing question]\n"
        "TWEET_END\n"
        f"{reply_instruction}\n"
    )

    try:
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _VOICE_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=400,
            temperature=0.7,
        )
        raw = (completion.choices[0].message.content or "").strip()
    except Exception:
        logger.exception("Stage C OpenAI call failed")
        return "", "", False

    # Parse multi-line tweet between TWEET_START / TWEET_END markers
    is_confirmed = True
    tweet_text = ""
    reply_text = ""

    in_tweet = False
    tweet_lines: list[str] = []

    for line in raw.splitlines():
        if line.startswith("IS_CONFIRMED:"):
            is_confirmed = line.split(":", 1)[1].strip().lower() == "yes"
        elif line.strip() == "TWEET_START":
            in_tweet = True
        elif line.strip() == "TWEET_END":
            in_tweet = False
        elif line.startswith("REPLY:"):
            reply_text = line.split(":", 1)[1].strip()
        elif in_tweet:
            tweet_lines.append(line)

    tweet_text = "\n".join(tweet_lines).strip()

    # Hard cap — truncate gracefully if the model over-generates
    if len(tweet_text) > 260:
        tweet_text = tweet_text[:260].rsplit("\n", 1)[0].strip()

    # Guarantee at least the article URL appears in the reply
    if headline.url and headline.url not in reply_text:
        if headline.video_url:
            reply_text = f"Video: {headline.video_url} | Article: {headline.url}"
        else:
            reply_text = f"Source: {headline.url}"

    return tweet_text, reply_text, is_confirmed


def run_trend_research(session: Session) -> TrendResearchResult | None:
    """
    Execute the full 3-stage agentic pipeline:
      A. Discovery  — hunt_headlines()
      B. Synthesis  — _run_stage_b()
      C. Validation — _run_stage_c()

    Returns None if no publishable content is found.
    """
    client = _get_openai()
    if client is None:
        return None

    model = Config.OPENAI_MODEL or "gpt-4.1-mini"

    # ── Stage A: Discovery ────────────────────────────────────────────────────
    logger.info("Stage A: hunting headlines...")
    headlines = hunt_headlines(max_results=10)
    if not headlines:
        logger.warning("Stage A: no headlines found")
        return None

    # Pull recent posts for deduplication (URL hash + topic overlap)
    cutoff_7d = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)
    recent_posts = (
        session.query(TrendPost.content_hash, TrendPost.headline_title)
        .filter(
            TrendPost.status.in_(["posted", "dry_run"]),
            TrendPost.posted_at >= cutoff_7d,
        )
        .all()
    )
    posted_hashes: set[str] = {row[0] for row in recent_posts}
    recent_titles: list[str] = [row[1] for row in recent_posts if row[1]]

    fresh = []
    for h in headlines:
        if h.content_hash in posted_hashes:
            logger.debug("Skipping (URL dedup): %r", h.title)
            continue
        if _is_duplicate_topic(h, recent_titles):
            logger.info("Skipping (topic dedup): %r", h.title)
            continue
        fresh.append(h)

    if not fresh:
        logger.info("Stage A: all headlines already covered; skipping this cycle")
        return None

    logger.info("Stage A: %d fresh headlines after dedup", len(fresh))

    # ── Stage B: Synthesis & Correlation ─────────────────────────────────────
    logger.info("Stage B: synthesizing with bot context...")
    bot_context = _get_recent_bot_context(session)
    selected_headline, synthesis = _run_stage_b(client, fresh, bot_context, model)

    if not synthesis:
        logger.warning("Stage B: no synthesis produced; aborting")
        return None

    logger.info("Stage B: selected headline=%r", selected_headline.title)

    # Optional: find a related YouTube video for the selected headline
    video_query = " ".join(list(_headline_keywords(selected_headline.title))[:5])
    logger.info("Searching YouTube for: %r", video_query)
    video_url = _search_youtube(video_query)
    if video_url:
        selected_headline.video_url = video_url
        logger.info("Found video: %s", video_url)
    else:
        logger.info("No YouTube video found; reply will use article link only")

    # ── Stage C: Validation & Formatting ─────────────────────────────────────
    logger.info("Stage C: validating and formatting...")
    tweet_text, reply_text, is_confirmed = _run_stage_c(
        client, selected_headline, synthesis, model
    )

    if not tweet_text:
        logger.warning("Stage C: no tweet text produced; aborting")
        return None

    logger.info(
        "Stage C: is_confirmed=%s tweet=%r",
        is_confirmed,
        tweet_text[:80],
    )

    return TrendResearchResult(
        headline=selected_headline,
        synthesis=synthesis,
        tweet_text=tweet_text,
        reply_text=reply_text,
        is_confirmed=is_confirmed,
        video_url=video_url,
    )
