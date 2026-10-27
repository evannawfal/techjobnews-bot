from __future__ import annotations

"""
Opinion Post Pipeline — two-stage agentic pipeline for generating bold,
debate-worthy takes on tech market topics.

Stage 1 (Pick): LLM selects the most debate-worthy headline and declares a stance.
Stage 2 (Write): LLM writes the opinion post in the defined @techjobnews opinion voice.

Distinct from the trend research pipeline (analytical/observational).
Opinion posts take a clear side and invite pushback.
"""

import datetime as dt
import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from openai import OpenAI
from sqlalchemy.orm import Session

from src.config import Config
from src.db.models import OpinionPost
from src.sources.opinion_hunter import Headline, hunt_opinion_topics

logger = logging.getLogger(__name__)

_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "in", "on", "at", "to",
    "for", "of", "and", "or", "that", "this", "with", "its", "has", "have",
    "from", "by", "as", "it", "be", "will", "not", "but", "new",
}

# ------------------------------------------------------------------
# Voice prompt
# ------------------------------------------------------------------

_OPINION_SYSTEM_PROMPT = """\
You write opinion posts for @techjobnews, an X account that tracks the tech job market.

YOUR OPINION VOICE:
You are a credible, opinionated industry observer who says what others won't. \
You make bold, specific claims backed by observable trends. You are NOT a journalist \
reporting neutrally. You have a point of view and you own it. You are willing to be \
wrong, and you explicitly invite people to challenge you.

Think: someone who has followed tech hiring for years and calls patterns before they \
become consensus. Not contrarian for the sake of it. Contrarian because the data supports it.

STRUCTURE (follow every time):
Line 1 -- THE CLAIM. One bold, specific, debatable statement. Under 120 characters.
           It must be something a reasonable person could disagree with.
[blank line]
Lines 2-3 -- THE REASONING. 1-2 short lines explaining the "why." Max 8-10 words per line.
[blank line]
Last line -- THE CHALLENGE. A direct, natural invitation to debate.
             Options: "Agree or disagree?", "Change my mind.", "Am I wrong?", \
"Hot take or obvious?" Keep it under 40 characters.

STYLE RULES:
- NEVER open with a company name. The claim comes first, the company is context.
  BAD: "Meta's layoffs are a smokescreen for..."
  GOOD: "These layoffs are not cost-cutting. They are talent reallocation."
- State the claim directly. Never open with "I think" or "In my opinion."
- Make it specific. "AI is eating junior dev roles" beats "AI is changing everything."
- Ground it in something real and recent. No generic talking points.
- Slightly skeptical of corporate language is fine. Accusatory is not.
  BAD words (do not use): smokescreen, cover-up, lie, scam, scheme, disguise, facade, con.
- Short lines, active voice, no padding.

STANCE DEFINITIONS:
- "for": Defend or endorse the action/decision, even if unpopular.
- "against": Challenge the decision or the dominant narrative. Be the skeptic.
- "reframe": Offer a NEW lens. Show what the event actually signals.
  A reframe is NOT calling something a lie or a cover-up.
  A reframe is: "Everyone sees X. The real story is Y."
  GOOD reframe: "These aren't cost cuts. The budget is moving to AI infra."
  BAD reframe: "This is a smokescreen for shifting priorities." (accusatory, not a lens)

FORMAT RULES:
- Total: max 260 characters including line breaks.
- NEVER use em dashes (--) or the character (—). Use a period, comma, or colon instead.
- BANNED emojis: ✨ 🚀 🤖 🌟 💫 🔥 -- do not use.
- Allowed emojis: 📊 📉 📈 only if genuinely helpful. Usually none needed.
- 0 hashtags. Opinion posts do not use hashtags.
- Do NOT start with "Breaking:", "Just in:", or "Hot take:".

GOOD EXAMPLES (copy this structure exactly):

Example 1 (against conventional wisdom):
The best thing for junior devs right now is more layoffs.

Forced reskilling beats a comfortable job AI will automate in two years.

Change my mind.

Example 2 (reframe -- notice: no company name opener, no accusations):
These are not layoffs. They are budget transfers.

The headcount dropping in content ops is funding 3x more AI infra roles.

Is that progress or a trade-off?

Example 3 (prediction):
RTO mandates will cost big tech more than remote ever did.

Senior engineers leave quietly. Replacing them takes 6 months and costs 2x salary.

Am I wrong?

Example 4 (counterintuitive):
The companies hiring most aggressively right now are the ones to watch carefully.

Rapid headcount growth without revenue to match is the 2021 playbook, not 2026.

Hot take or obvious?

BAD EXAMPLES:
- "Meta's layoffs are a smokescreen for shifting talent pools." -- company name opener + banned word
- "AI is changing the tech landscape significantly." -- too vague, not debatable
- "Breaking: Big tech is struggling! 🔥" -- banned emoji, sensational
- "I think remote work is good for employees." -- "I think" opener, not bold enough
"""


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

@dataclass
class OpinionResult:
    headline: Headline
    stance: str          # "for" | "against" | "reframe"
    tweet_text: str      # Formatted opinion post (no URL)
    reply_text: str      # Source attribution for reply tweet
    content_hash: str = field(default="", init=False)

    def __post_init__(self) -> None:
        raw = f"{self.headline.url}|{self.tweet_text[:80]}"
        self.content_hash = hashlib.sha256(raw.encode()).hexdigest()


def _get_openai() -> Optional[OpenAI]:
    api_key = Config.OPENAI_API_KEY
    if not api_key:
        logger.warning("OPENAI_API_KEY not set; opinion post unavailable")
        return None
    return OpenAI(api_key=api_key)


def _headline_keywords(title: str) -> set[str]:
    return {
        w.lower()
        for w in re.findall(r"\w+", title)
        if len(w) > 3 and w.lower() not in _STOPWORDS
    }


def _topic_overlap(title1: str, title2: str) -> float:
    kw1, kw2 = _headline_keywords(title1), _headline_keywords(title2)
    if not kw1 or not kw2:
        return 0.0
    return len(kw1 & kw2) / min(len(kw1), len(kw2))


def _is_duplicate_topic(
    headline: Headline,
    recent_topics: list[str],
    threshold: float = 0.5,
) -> bool:
    return any(_topic_overlap(headline.title, t) >= threshold for t in recent_topics)


_BANNED_WORDS = {
    "smokescreen", "cover-up", "coverup", "lie", "lying", "scam",
    "scheme", "disguise", "facade", "con", "deception", "deceptive",
}


def _validate_opinion_tweet(tweet_text: str) -> tuple[bool, str]:
    """
    Quality gate applied after Stage 2. Returns (is_valid, rejection_reason).

    Catches the most common failure modes:
    - Opening with a company name (e.g. "Meta's layoffs are...")
    - Using banned accusatory/sensational words
    - Missing the 3-part structure
    - Contains an em dash
    """
    if not tweet_text:
        return False, "empty tweet"

    lines = [ln for ln in tweet_text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return False, "fewer than 2 non-empty lines (missing structure)"

    first_line = lines[0].strip()
    first_word = first_line.split()[0] if first_line.split() else ""

    # Reject company-name opener: CapWord + "'s" (e.g. "Meta's", "Google's")
    if first_word and first_word[0].isupper() and (
        first_word.endswith("'s") or first_word.endswith("\u2019s")
    ):
        return False, f"opens with company possessive: {first_word!r}"

    lower = tweet_text.lower()
    for word in _BANNED_WORDS:
        if word in lower:
            return False, f"contains banned word: {word!r}"

    if "\u2014" in tweet_text or " -- " in tweet_text:
        return False, "contains em dash"

    return True, ""


def _claim_similarity(tweet1: str, tweet2: str) -> float:
    """
    Compare the first lines (the claim) of two opinion tweets.
    Returns a 0-1 overlap score based on significant keywords.
    """
    def claim_keywords(t: str) -> set[str]:
        first = t.split("\n")[0].strip().lower()
        return {
            w for w in re.findall(r"\w+", first)
            if len(w) > 3 and w not in _STOPWORDS
        }

    kw1, kw2 = claim_keywords(tweet1), claim_keywords(tweet2)
    if not kw1 or not kw2:
        return 0.0
    return len(kw1 & kw2) / min(len(kw1), len(kw2))


# ------------------------------------------------------------------
# Stage 1: Pick the most debate-worthy topic and decide a stance
# ------------------------------------------------------------------

def _run_stage_pick(
    client: OpenAI,
    headlines: list[Headline],
    model: str,
) -> list[tuple[Headline, str]]:
    """
    Select the top 3 most debate-worthy headlines and assign each a stance.
    Returns a list of (headline, stance) pairs in ranked order.
    Having 3 candidates lets the pipeline retry if the first one fails validation.
    """
    n = len(headlines)
    headline_list = "\n".join(
        f"{i + 1}. [{h.source}] {h.title}"
        for i, h in enumerate(headlines)
    )

    prompt = (
        "You are choosing topics for bold opinion posts about the tech job market.\n\n"
        f"HEADLINES:\n{headline_list}\n\n"
        "Select the TOP 3 most debate-worthy headlines and assign each a stance.\n"
        "Prioritise: CEO statements, RTO policy, AI replacing workers, controversial cuts.\n\n"
        "Stance options:\n"
        "  - 'against': challenge the conventional take or defend the underdog\n"
        "  - 'for': defend an unpopular position or endorse something controversial\n"
        "  - 'reframe': offer a new lens on the event (NOT calling it a lie or cover-up)\n\n"
        "Output format (exactly 3 lines, no extra text):\n"
        "PICK_1: <headline number> | <stance>\n"
        "PICK_2: <headline number> | <stance>\n"
        "PICK_3: <headline number> | <stance>\n"
    )

    try:
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a sharp tech industry commentator."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=120,
            temperature=0.5,
        )
        raw = (completion.choices[0].message.content or "").strip()
    except Exception:
        logger.exception("Stage Pick OpenAI call failed")
        return []

    picks: list[tuple[Headline, str]] = []
    seen_hashes: set[str] = set()
    for line in raw.splitlines():
        for prefix in ("PICK_1:", "PICK_2:", "PICK_3:"):
            if line.startswith(prefix):
                parts = line.split(":", 1)[1].strip().split("|")
                if len(parts) == 2:
                    try:
                        idx = int(parts[0].strip()) - 1
                        idx = max(0, min(idx, n - 1))
                        stance = parts[1].strip().lower()
                        if stance not in ("for", "against", "reframe"):
                            stance = "reframe"
                        h = headlines[idx]
                        if h.content_hash not in seen_hashes:
                            seen_hashes.add(h.content_hash)
                            picks.append((h, stance))
                    except (ValueError, IndexError):
                        pass

    return picks[:3]


# ------------------------------------------------------------------
# Stage 2: Write the opinion post
# ------------------------------------------------------------------

def _run_stage_write(
    client: OpenAI,
    headline: Headline,
    stance: str,
    model: str,
    hint: str = "",
) -> tuple[str, str]:
    """
    Write the opinion tweet and reply.
    `hint` is prepended to the prompt when retrying after a validation failure.
    Returns (tweet_text, reply_text).
    """
    stance_instruction = {
        "for": (
            "Take the side that defends or endorses the action/decision described, "
            "even if it is unpopular. Make the strongest possible case for it."
        ),
        "against": (
            "Take the side that challenges or criticizes the conventional take, "
            "the company's decision, or the dominant narrative. Be the skeptic."
        ),
        "reframe": (
            "Do not simply agree or disagree. Reinterpret the event in a "
            "counterintuitive or unexpected way that reveals something others are missing."
        ),
    }[stance]

    correction = f"CORRECTION FROM PREVIOUS ATTEMPT: {hint}\n\n" if hint else ""

    prompt = (
        f"{correction}"
        f"TOPIC:\nHeadline: {headline.title}\n"
        f"Source: {headline.source}\n"
        f"Context: {headline.snippet[:400]}\n\n"
        f"YOUR STANCE: {stance_instruction}\n\n"
        "Write the opinion post using the voice and structure from your system prompt.\n\n"
        "Output format (copy exactly -- the tweet is multi-line):\n"
        "TWEET_START\n"
        "[claim line]\n"
        "\n"
        "[reasoning line(s)]\n"
        "\n"
        "[challenge line]\n"
        "TWEET_END\n"
        f"REPLY: <source attribution, max 180 chars, MUST include: {headline.url}>\n"
    )

    try:
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _OPINION_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=400,
            temperature=0.85,  # Higher temp for more personality
        )
        raw = (completion.choices[0].message.content or "").strip()
    except Exception:
        logger.exception("Stage Write OpenAI call failed")
        return "", ""

    in_tweet = False
    tweet_lines: list[str] = []
    reply_text = ""

    for line in raw.splitlines():
        if line.strip() == "TWEET_START":
            in_tweet = True
        elif line.strip() == "TWEET_END":
            in_tweet = False
        elif line.startswith("REPLY:"):
            reply_text = line.split(":", 1)[1].strip()
        elif in_tweet:
            tweet_lines.append(line)

    tweet_text = "\n".join(tweet_lines).strip()

    if len(tweet_text) > 260:
        tweet_text = tweet_text[:260].rsplit("\n", 1)[0].strip()

    if headline.url and headline.url not in reply_text:
        reply_text = f"Source: {headline.url}"

    return tweet_text, reply_text


# ------------------------------------------------------------------
# Main entry point
# ------------------------------------------------------------------

def run_opinion_post(session: Session) -> OpinionResult | None:
    """
    Execute the two-stage opinion pipeline.

    Stage 1 (Pick): select the most debate-worthy headline + stance.
    Stage 2 (Write): generate the opinion tweet in the defined voice.

    Returns None if no publishable opinion was produced.
    """
    client = _get_openai()
    if client is None:
        return None

    model = Config.OPENAI_MODEL or "gpt-4.1-mini"

    # ── Find topics ───────────────────────────────────────────────
    logger.info("Opinion Stage 1: hunting controversy-worthy topics...")
    headlines = hunt_opinion_topics(max_results=10)
    if not headlines:
        logger.warning("Opinion Stage 1: no topics found")
        return None

    # Dedup against recent opinion posts (URL + topic + claim similarity)
    cutoff_7d = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)
    recent_posts = (
        session.query(
            OpinionPost.topic_url,
            OpinionPost.topic_headline,
            OpinionPost.tweet_text,
        )
        .filter(
            OpinionPost.status.in_(["posted", "dry_run"]),
            OpinionPost.posted_at >= cutoff_7d,
        )
        .all()
    )
    posted_urls: set[str] = {row[0] for row in recent_posts}
    recent_topics: list[str] = [row[1] for row in recent_posts if row[1]]
    recent_tweets: list[str] = [row[2] for row in recent_posts if row[2]]

    fresh = []
    for h in headlines:
        if h.url in posted_urls:
            logger.debug("Skipping (URL dedup): %r", h.title)
            continue
        if _is_duplicate_topic(h, recent_topics):
            logger.info("Skipping (topic dedup): %r", h.title)
            continue
        fresh.append(h)

    if not fresh:
        logger.info("Opinion Stage 1: all topics already covered; skipping")
        return None

    logger.info("Opinion Stage 1: %d fresh topics", len(fresh))

    # ── Stage 1: Pick top-3 candidates ────────────────────────────
    picks = _run_stage_pick(client, fresh, model)
    if not picks:
        logger.warning("Opinion Stage 1: pick failed")
        return None

    logger.info("Opinion Stage 1: %d candidates ranked", len(picks))

    # ── Stage 2: Try each candidate; retry once on validation fail ─
    for attempt, (selected, stance) in enumerate(picks, start=1):
        logger.info(
            "Opinion Stage 2 [candidate %d/%d]: %r (stance=%s)",
            attempt, len(picks), selected.title, stance,
        )

        tweet_text, reply_text = _run_stage_write(client, selected, stance, model)

        if not tweet_text:
            logger.warning("Candidate %d: no tweet produced; trying next", attempt)
            continue

        is_valid, reason = _validate_opinion_tweet(tweet_text)
        if not is_valid:
            logger.warning(
                "Candidate %d failed validation (%s): %r — retrying with hint",
                attempt, reason, tweet_text[:80],
            )
            hint = (
                f"Your previous attempt was rejected because: {reason}. "
                "Fix only this issue and keep everything else the same."
            )
            tweet_text, reply_text = _run_stage_write(
                client, selected, stance, model, hint=hint
            )
            if tweet_text:
                is_valid, reason = _validate_opinion_tweet(tweet_text)

        if not is_valid:
            logger.warning("Candidate %d: still invalid after retry; trying next", attempt)
            continue

        if any(_claim_similarity(tweet_text, t) >= 0.6 for t in recent_tweets):
            logger.info("Candidate %d: claim too similar to a recent post; trying next", attempt)
            continue

        logger.info("Opinion Stage 2: accepted candidate %d tweet=%r", attempt, tweet_text[:80])
        return OpinionResult(
            headline=selected,
            stance=stance,
            tweet_text=tweet_text,
            reply_text=reply_text,
        )

    logger.warning("Opinion: all %d candidates failed; skipping this cycle", len(picks))
    return None
