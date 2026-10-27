from __future__ import annotations

import logging
import re
from typing import Optional

import requests
from bs4 import BeautifulSoup
from openai import OpenAI

from src.config import Config

logger = logging.getLogger(__name__)


def _get_client() -> Optional[OpenAI]:
    api_key = getattr(Config, "OPENAI_API_KEY", None)
    if not api_key:
        logger.warning("OPENAI_API_KEY not set; skipping summary generation")
        return None
    return OpenAI(api_key=api_key)


def _fetch_source_snippet(url: str, *, timeout: int = 8, max_chars: int = 800) -> str:
    """
    Best-effort: pull a short text snippet from the layoff source article.
    If anything fails, return "" and fall back to generic context.
    """
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "x-bot-layoffs-alert/0.1"})
        resp.raise_for_status()
    except Exception:
        logger.info("Could not fetch source article for summary; url=%s", url)
        return ""

    soup = BeautifulSoup(resp.text, "lxml")
    # Prefer article/main tags, then paragraphs.
    container = soup.find("article") or soup.find("main") or soup.body
    if not container:
        return ""

    texts: list[str] = []
    for p in container.find_all("p"):
        t = p.get_text(" ", strip=True)
        if t:
            texts.append(t)
        if sum(len(x) for x in texts) >= max_chars:
            break

    snippet = " ".join(texts)
    snippet = re.sub(r"\s+", " ", snippet).strip()
    if len(snippet) > max_chars:
        snippet = snippet[:max_chars].rsplit(" ", 1)[0]
    return snippet


def build_summary_prompt(
    *,
    company: str,
    employees: int | None,
    percentage: float | None,
    location: str | None,
    industry: str | None,
    source_snippet: str | None,
) -> str:
    emp = f"{employees:,}" if employees else "an undisclosed number of"
    perc = f" ({percentage:.1f}% of staff)" if percentage else ""
    loc = location or "global"
    ind = industry or "tech"

    return (
        "You are helping write a concise, neutral summary of a tech layoff for Twitter.\n"
        "Write 1–2 sentences that:\n"
        "- Briefly state WHY the layoff happened (or likely reasons if unknown).\n"
        "- Explain WHAT it means / possible implications.\n"
        "- Mention which teams or functions were likely affected if not explicitly known (e.g. 'mainly product and operations').\n"
        "- End with an engagement question hook, such as:\n"
        "  - 'Do you think tech layoffs will increase in 2026?'\n"
        "  - 'Were you affected by this layoff?'\n"
        "\n"
        "Constraints:\n"
        "- Maximum 2 sentences total.\n"
        "- Do NOT include hashtags.\n"
        "- Do NOT repeat the raw numbers exactly; they are already in the main tweet.\n"
        "- Avoid sensational language; keep it measured.\n"
        "\n"
        f"Context: {company} just laid off {emp} employees{perc} in the {ind} sector, location {loc}.\n"
        f"Source article excerpt (primary basis for the summary, if coherent):\n{(source_snippet or 'N/A')}.\n"
        "Return only the 1–2 sentence text, nothing else."
    )


def _looks_legible(text: str, *, max_chars: int) -> bool:
    """
    Heuristic filter so only 'good' summaries make it through:
    - 1–2 sentences
    - ends with a question mark
    - clearly below max_chars (no further truncation)
    """
    if not text:
        return False
    if len(text) > max_chars:
        return False

    # Basic sentence detection.
    sentences = re.split(r"[.!?]\s+", text.strip(" \n"))
    sentences = [s for s in sentences if s]
    if not (1 <= len(sentences) <= 2):
        return False

    if not text.strip().endswith("?"):
        return False

    # Avoid weird all-caps or very short blurbs.
    if len(text) < 40:
        return False
    return True


def generate_summary_and_hook(
    *,
    company: str,
    employees: int | None,
    percentage: float | None,
    location: str | None,
    industry: str | None,
    source_url: str | None,
    max_chars: int,
) -> str:
    """
    Call OpenAI to generate a 1–2 sentence summary + engagement hook.
    We never truncate mid-sentence: if the model returns something longer
    than `max_chars` or low-quality, we drop it completely.
    """
    if max_chars <= 0:
        return ""

    client = _get_client()
    if client is None:
        return ""

    snippet = _fetch_source_snippet(source_url, max_chars=800) if source_url else ""

    prompt = build_summary_prompt(
        company=company,
        employees=employees,
        percentage=percentage,
        location=location,
        industry=industry,
        source_snippet=snippet,
    )

    # Cost-conscious default: small chat model well-suited to short text.
    # If OPENAI_MODEL is unset or empty, fall back safely.
    model = Config.OPENAI_MODEL or "gpt-4.1-mini"

    try:
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a concise assistant for social posts."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=120,
            temperature=0.6,
        )
    except Exception:  # noqa: BLE001
        logger.exception("OpenAI summary generation failed")
        return ""

    text = (completion.choices[0].message.content or "").strip()
    if not _looks_legible(text, max_chars=max_chars):
        return ""
    return text


def generate_hiring_summary_and_hook(
    *,
    company: str,
    total_open_roles: int,
    engineering_open_roles: int,
    ai_open_roles: int,
    max_chars: int,
) -> str:
    """
    Variant tailored for hiring surges: 1–2 sentence summary that explains
    why the company might be scaling up and ends with a hiring-focused
    engagement hook.
    """
    if max_chars <= 0:
        return ""

    client = _get_client()
    if client is None:
        return ""

    prompt = (
        "You are writing a short, punchy comment to append to a Twitter post about a tech company hiring surge.\n"
        "Write exactly 1–2 sentences that:\n"
        "- Add brief context on WHY the company might be scaling up (AI investment, new product, growth stage, expansion).\n"
        "- End with a direct engagement question that invites replies, such as:\n"
        f"  - 'Is {company} on your radar?'\n"
        f"  - 'Would you make the jump to {company}?'\n"
        "  - 'Would you apply?'\n"
        "  - 'Know anyone who'd be a great fit?'\n"
        "\n"
        "Rules:\n"
        "- Exactly 1–2 sentences. No lists, no bullet points.\n"
        "- End the final sentence with a question mark.\n"
        "- Do NOT repeat the job count numbers — those are already in the tweet.\n"
        "- Do NOT use hashtags.\n"
        "- Keep it conversational and optimistic, not corporate.\n"
        "\n"
        f"Company: {company}\n"
        f"Open roles: ~{total_open_roles} total, ~{engineering_open_roles} engineering, ~{ai_open_roles} AI/ML\n"
        "Return only the 1–2 sentence text, nothing else."
    )

    model = Config.OPENAI_MODEL or "gpt-4.1-mini"

    try:
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a concise assistant for social posts."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=120,
            temperature=0.6,
        )
    except Exception:  # noqa: BLE001
        logger.exception("OpenAI hiring summary generation failed")
        return ""

    text = (completion.choices[0].message.content or "").strip()
    if not _looks_legible(text, max_chars=max_chars):
        return ""
    return text

