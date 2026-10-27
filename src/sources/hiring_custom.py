from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

import requests

from src.sources.hiring_greenhouse import GreenhouseJob

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "x-bot-hiring-tracker/0.1"}


@dataclass
class JobFetchResult:
    """
    Used when a source can return the TRUE total job count separately from the
    sample fetched for category analysis.  For companies with 5,000–20,000 jobs
    we can't paginate everything; instead we fetch one page for categorisation and
    use the API's reported total for the surge-detection count.
    """
    total: int
    jobs: list[GreenhouseJob] = field(default_factory=list)


# ── Amazon ────────────────────────────────────────────────────────────────────

def _fetch_amazon(company_name: str, *, timeout: int = 20) -> list[GreenhouseJob] | JobFetchResult:
    """
    Amazon Jobs public search API.  Returns 'hits' as the verified total count.
    """
    try:
        resp = requests.get(
            "https://www.amazon.jobs/en/search.json",
            params={"result_limit": 100, "offset": 0, "base_query": ""},
            timeout=timeout,
            headers=_HEADERS,
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
    except requests.RequestException as e:
        logger.warning("Amazon fetch failed: %s", e)
        return []
    except (ValueError, TypeError) as e:
        logger.warning("Amazon invalid JSON: %s", e)
        return []

    api_total = int(data.get("hits") or 0)
    sample: list[GreenhouseJob] = []
    for j in data.get("jobs") or []:
        if not isinstance(j, dict):
            continue
        title = (j.get("title") or "").strip()
        if not title:
            continue
        job_id = str(j.get("id_icims") or j.get("id") or len(sample))
        location = (j.get("location") or "").strip() or None
        category = (j.get("job_category") or j.get("job_family") or "").strip()
        sample.append(GreenhouseJob(company=company_name, external_id=job_id,
                                    title=title, location=location,
                                    departments=[category] if category else []))

    if not sample and api_total == 0:
        return []
    logger.info("Fetched %d Amazon jobs (sample); api_total=%d", len(sample), api_total)
    return JobFetchResult(total=api_total, jobs=sample) if api_total > len(sample) else sample


# ── Dispatcher ────────────────────────────────────────────────────────────────

_CUSTOM_FETCHERS: dict[str, Callable[..., list[GreenhouseJob] | JobFetchResult]] = {
    "amazon": _fetch_amazon,
}


def fetch_custom_jobs(
    company_name: str, company_id: str, *, timeout: int = 20
) -> list[GreenhouseJob] | JobFetchResult:
    fetcher = _CUSTOM_FETCHERS.get(company_id)
    if not fetcher:
        logger.warning("No custom fetcher for %s (company_id=%s)", company_name, company_id)
        return []
    return fetcher(company_name, timeout=timeout)


# Google, Meta, Apple, Microsoft, Amazon all use their own proprietary ATS.
CUSTOM_COMPANIES: dict[str, dict[str, str]] = {
    "Amazon": {"company_id": "amazon", "careers_url": "https://amazon.jobs/"},
}
