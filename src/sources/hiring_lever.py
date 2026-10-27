from __future__ import annotations

import logging
from typing import Any

import requests

from src.sources.hiring_greenhouse import GreenhouseJob

logger = logging.getLogger(__name__)

LEVER_BASE_URL = "https://api.lever.co/v0/postings/{company_id}?mode=json"


def fetch_lever_jobs(company_name: str, company_id: str, *, timeout: int = 15) -> list[GreenhouseJob]:
    """
    Fetch open roles from a company's public Lever board.
    Never raises: on any network/parse/API error returns [] and logs.
    """
    url = LEVER_BASE_URL.format(company_id=company_id)
    try:
        resp = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "x-bot-hiring-tracker/0.1"},
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("Lever fetch failed for %s (%s): %s", company_name, company_id, e)
        return []

    try:
        data: list[Any] = resp.json()
    except (ValueError, TypeError) as e:
        logger.warning("Lever invalid JSON for %s (%s): %s", company_name, company_id, e)
        return []

    if not isinstance(data, list):
        logger.debug("Lever unexpected response shape for %s (%s); got %s", company_name, company_id, type(data).__name__)
        return []

    jobs: list[GreenhouseJob] = []
    for j in data:
        if not isinstance(j, dict):
            continue
        try:
            job_id = str(j.get("id") or "")
            title = (j.get("text") or "").strip()
            if not job_id or not title:
                continue
            categories = j.get("categories") or {}
            location = (categories.get("location") or "").strip() or None
            team = (categories.get("team") or "").strip()
            dept = (categories.get("department") or "").strip()
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
            logger.debug("Lever skip malformed job for %s: %s", company_name, e)
            continue

    logger.info("Fetched %d Lever jobs for %s", len(jobs), company_name)
    return jobs


# Notable tech companies on Lever.
# If a board slug is wrong or the company migrated ATS, fetch_lever_jobs returns [] and we skip.
LEVER_COMPANIES: dict[str, dict[str, str]] = {
    # Data / Defense / Analytics
    "Palantir": {"company_id": "palantir", "careers_url": "https://www.palantir.com/careers/"},
}
