from __future__ import annotations

import logging
from typing import Any

import requests

from src.sources.hiring_greenhouse import GreenhouseJob

logger = logging.getLogger(__name__)

ASHBY_BASE_URL = "https://api.ashbyhq.com/posting-api/job-board/{company_id}"


def fetch_ashby_jobs(company_name: str, company_id: str, *, timeout: int = 15) -> list[GreenhouseJob]:
    """
    Fetch open roles from a company's public Ashby job board.
    Never raises: on any network/parse/API error returns [] and logs.
    """
    url = ASHBY_BASE_URL.format(company_id=company_id)
    try:
        resp = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "x-bot-hiring-tracker/0.1"},
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("Ashby fetch failed for %s (%s): %s", company_name, company_id, e)
        return []

    try:
        data: dict[str, Any] = resp.json()
    except (ValueError, TypeError) as e:
        logger.warning("Ashby invalid JSON for %s (%s): %s", company_name, company_id, e)
        return []

    jobs_raw = data.get("jobPostings")
    if not isinstance(jobs_raw, list):
        logger.debug("Ashby no jobPostings array for %s (%s)", company_name, company_id)
        return []

    jobs: list[GreenhouseJob] = []
    for j in jobs_raw:
        if not isinstance(j, dict):
            continue
        try:
            job_id = str(j.get("id") or "")
            title = (j.get("title") or "").strip()
            if not job_id or not title:
                continue
            location = (j.get("locationName") or "").strip() or None
            dept = (j.get("departmentName") or "").strip()
            team = (j.get("teamName") or "").strip()
            depts = [d for d in [dept, team] if d]
            jobs.append(
                GreenhouseJob(
                    company=company_name,
                    external_id=job_id,
                    title=title,
                    location=location,
                    departments=depts,
                )
            )
        except (TypeError, KeyError) as e:
            logger.debug("Ashby skip malformed job for %s: %s", company_name, e)
            continue

    logger.info("Fetched %d Ashby jobs for %s", len(jobs), company_name)
    return jobs


# Notable tech companies on Ashby.
# If a slug is wrong or the company migrated ATS, fetch_ashby_jobs returns [] and we skip.
ASHBY_COMPANIES: dict[str, dict[str, str]] = {
    # AI / LLM
    "Perplexity": {"company_id": "perplexity", "careers_url": "https://www.perplexity.ai/careers"},
    "Character.AI": {"company_id": "character", "careers_url": "https://careers.character.ai/"},
    # Dev Tools
    "Replit": {"company_id": "replit", "careers_url": "https://replit.com/site/careers"},
    # Fintech / HR
    "Ramp": {"company_id": "ramp", "careers_url": "https://ramp.com/careers"},
}
