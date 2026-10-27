from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import requests

logger = logging.getLogger(__name__)


GREENHOUSE_BASE_URL = "https://boards-api.greenhouse.io/v1/boards/{company_id}/jobs"


@dataclass
class GreenhouseJob:
    company: str
    external_id: str
    title: str
    location: str | None
    departments: list[str]


def fetch_greenhouse_jobs(company_name: str, company_id: str, *, timeout: int = 15) -> list[GreenhouseJob]:
    """
    Fetch open roles from a company's public Greenhouse board.
    Never raises: on any network/parse/API error returns [] and logs.
    """
    url = GREENHOUSE_BASE_URL.format(company_id=company_id)
    try:
        resp = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "x-bot-hiring-tracker/0.1"},
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("Greenhouse fetch failed for %s (%s): %s", company_name, company_id, e)
        return []

    try:
        data: dict[str, Any] = resp.json()
    except (ValueError, TypeError) as e:
        logger.warning("Greenhouse invalid JSON for %s (%s): %s", company_name, company_id, e)
        return []

    jobs_raw = data.get("jobs")
    if not isinstance(jobs_raw, list):
        logger.debug("Greenhouse no jobs array for %s (%s)", company_name, company_id)
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
            loc_obj = j.get("location")
            location = None
            if isinstance(loc_obj, dict):
                location = (loc_obj.get("name") or "").strip() or None
            depts = []
            for dept in j.get("departments") or []:
                if isinstance(dept, dict):
                    name = (dept.get("name") or "").strip()
                    if name:
                        depts.append(name)
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
            logger.debug("Greenhouse skip malformed job for %s: %s", company_name, e)
            continue

    logger.info("Fetched %d Greenhouse jobs for %s", len(jobs), company_name)
    return jobs

# If a board goes stale or returns 404, fetch_greenhouse_jobs returns [] and we skip that company.
GREENHOUSE_COMPANIES: dict[str, dict[str, str]] = {
    # AI / ML
    "Anthropic": {"company_id": "anthropic", "careers_url": "https://www.anthropic.com/careers"},
    "Scale AI": {"company_id": "scaleai", "careers_url": "https://scale.com/careers"},
    "Databricks": {"company_id": "databricks", "careers_url": "https://www.databricks.com/company/careers"},
    # Fintech / Payments
    "Stripe": {"company_id": "stripe", "careers_url": "https://stripe.com/jobs/search"},
    "Coinbase": {"company_id": "coinbase", "careers_url": "https://www.coinbase.com/careers"},
    "Brex": {"company_id": "brex", "careers_url": "https://www.brex.com/careers"},
    "Robinhood": {"company_id": "robinhood", "careers_url": "https://careers.robinhood.com"},
    "Affirm": {"company_id": "affirm", "careers_url": "https://www.affirm.com/careers"},
    "Chime": {"company_id": "chime", "careers_url": "https://www.chime.com/careers/"},
    "Marqeta": {"company_id": "marqeta", "careers_url": "https://www.marqeta.com/careers"},
    "Carta": {"company_id": "carta", "careers_url": "https://carta.com/careers/"},
    # Consumer / Social
    "Airbnb": {"company_id": "airbnb", "careers_url": "https://careers.airbnb.com"},
    "Reddit": {"company_id": "reddit", "careers_url": "https://www.reddit.com/careers/"},
    "Pinterest": {"company_id": "pinterest", "careers_url": "https://www.pinterestcareers.com"},
    "Discord": {"company_id": "discord", "careers_url": "https://discord.com/careers"},
    "Duolingo": {"company_id": "duolingo", "careers_url": "https://careers.duolingo.com"},
    "Instacart": {"company_id": "instacart", "careers_url": "https://careers.instacart.com"},
    # SaaS / Productivity / Collaboration
    "Asana": {"company_id": "asana", "careers_url": "https://asana.com/jobs"},
    "Figma": {"company_id": "figma", "careers_url": "https://www.figma.com/careers/"},
    "Airtable": {"company_id": "airtable", "careers_url": "https://airtable.com/careers"},
    "Webflow": {"company_id": "webflow", "careers_url": "https://webflow.com/careers"},
    "Gusto": {"company_id": "gusto", "careers_url": "https://gusto.com/about/careers"},
    "Grammarly": {"company_id": "grammarly", "careers_url": "https://www.grammarly.com/jobs"},
    "Twilio": {"company_id": "twilio", "careers_url": "https://www.twilio.com/company/jobs"},
    # Developer Tools / Infra / Cloud
    "Datadog": {"company_id": "datadog", "careers_url": "https://www.datadoghq.com/careers/"},
    "Cloudflare": {"company_id": "cloudflare", "careers_url": "https://www.cloudflare.com/careers/"},
    "MongoDB": {"company_id": "mongodb", "careers_url": "https://www.mongodb.com/careers"},
    "Elastic": {"company_id": "elastic", "careers_url": "https://www.elastic.co/about/careers"},
    "GitLab": {"company_id": "gitlab", "careers_url": "https://about.gitlab.com/jobs/"},
    "Vercel": {"company_id": "vercel", "careers_url": "https://vercel.com/careers"},
    "Dropbox": {"company_id": "dropbox", "careers_url": "https://www.dropbox.com/jobs"},
    # Security / Identity
    "Okta": {"company_id": "okta", "careers_url": "https://www.okta.com/company/careers/"},
    # Logistics / Marketplace
    "Flexport": {"company_id": "flexport", "careers_url": "https://www.flexport.com/careers"},
    "Faire": {"company_id": "faire", "careers_url": "https://www.faire.com/careers"},
    # Education
    "Coursera": {"company_id": "coursera", "careers_url": "https://www.coursera.org/careers"},
    "Khan Academy": {"company_id": "khanacademy", "careers_url": "https://www.khanacademy.org/careers"},
    "Udemy": {"company_id": "udemy", "careers_url": "https://about.udemy.com/careers/"},
    # Health
    "Oscar Health": {"company_id": "oscar", "careers_url": "https://www.hioscar.com/careers"},
}

