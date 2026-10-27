from __future__ import annotations

import datetime as dt
import logging
from collections import Counter
from statistics import median

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from src.db.models import HiringSnapshot
from src.sources.hiring_ashby import ASHBY_COMPANIES, fetch_ashby_jobs
from src.sources.hiring_custom import CUSTOM_COMPANIES, JobFetchResult, fetch_custom_jobs
from src.sources.hiring_greenhouse import GREENHOUSE_COMPANIES, GreenhouseJob, fetch_greenhouse_jobs
from src.sources.hiring_lever import LEVER_COMPANIES, fetch_lever_jobs

logger = logging.getLogger(__name__)

# Unified source registry — add new ATS sources here.
_SOURCES = [
    (GREENHOUSE_COMPANIES, fetch_greenhouse_jobs),
    (LEVER_COMPANIES, fetch_lever_jobs),
    (ASHBY_COMPANIES, fetch_ashby_jobs),
    (CUSTOM_COMPANIES, fetch_custom_jobs),
]


def _classify_jobs(jobs: list[GreenhouseJob]) -> tuple[int, int, int, list[str], list[str]]:
    """
    Return (total, engineering_count, ai_count, top_departments, top_locations)
    based on simple keyword heuristics.
    """
    total = len(jobs)
    eng = 0
    ai = 0
    dept_counter: Counter[str] = Counter()
    loc_counter: Counter[str] = Counter()

    for j in jobs:
        title_l = j.title.lower()
        if any(k in title_l for k in ("engineer", "developer", "engineering", "software", "infra")):
            eng += 1
        if any(k in title_l for k in ("ai", "machine learning", "ml", "llm", "deep learning")):
            ai += 1

        for d in j.departments:
            dept_counter[d] += 1
        if j.location:
            loc_counter[j.location] += 1

    top_departments = [name for name, _ in dept_counter.most_common(3)]
    top_locations = [name for name, _ in loc_counter.most_common(3)]
    return total, eng, ai, top_departments, top_locations


def _get_recent_baseline_query(company: str, as_of_date: dt.date, window_days: int = 21) -> Select[tuple[int]]:
    start_date = as_of_date - dt.timedelta(days=window_days)
    return select(HiringSnapshot.total_open_roles).where(
        HiringSnapshot.company == company,
        HiringSnapshot.snapshot_date >= start_date,
        HiringSnapshot.snapshot_date < as_of_date,
    )


def detect_hiring_surges(session: Session, *, today: dt.date | None = None) -> list[HiringSnapshot]:
    """
    For each configured company (Greenhouse, Lever, Ashby):
    - Fetch current open roles
    - Store / update today's snapshot
    - Compare against recent history to flag 'surge' days

    Heuristic for surge (per company):
    - Require at least 1 day of prior history (avoids first-run flood)
    - Compute median total_open_roles over last ~3 weeks (excluding today)
    - Require current_total >= 30 (avoid tiny companies)
    - Require:
        - current_total >= baseline + max(10, 0.4 * baseline)
        - OR baseline < 10 and current_total >= 25  (fast-growing new hiring ramp)
    """
    if today is None:
        today = dt.date.today()

    surged_snapshots: list[HiringSnapshot] = []

    for companies, fetch_fn in _SOURCES:
        for company, meta in companies.items():
            if not isinstance(meta, dict):
                continue
            company_id = meta.get("company_id")
            if not company_id:
                logger.warning("Config missing company_id for %s", company)
                continue
            careers_url = meta.get("careers_url") or ""

            raw = fetch_fn(company, company_id)
            if not raw:
                # Endpoint stale, 404, or empty board: skip without failing; no snapshot update.
                continue

            # Sources with large job boards (Google, Amazon, etc.) return a JobFetchResult
            # with the API's true total count + a sample of jobs for categorisation.
            # Standard sources (Greenhouse/Lever/Ashby) return list[GreenhouseJob] directly.
            if isinstance(raw, JobFetchResult):
                jobs_sample = raw.jobs
                true_total = raw.total
            else:
                jobs_sample = raw
                true_total = len(raw)

            if true_total == 0:
                continue

            _, eng, ai, top_depts, top_locs = _classify_jobs(jobs_sample)
            total = true_total

            # Scale eng/ai counts up proportionally when sample < true total
            # so the tweet shows representative numbers (not just counts from 20 sample jobs).
            if isinstance(raw, JobFetchResult) and jobs_sample and raw.total > len(jobs_sample):
                scale = raw.total / len(jobs_sample)
                eng = int(eng * scale)
                ai = int(ai * scale)

            try:
                # Upsert today's snapshot (per-company so one failure doesn't lose others).
                snap = session.scalar(
                    select(HiringSnapshot).where(
                        HiringSnapshot.company == company,
                        HiringSnapshot.snapshot_date == today,
                    )
                )
                if snap is None:
                    snap = HiringSnapshot(
                        company=company,
                        snapshot_date=today,
                        total_open_roles=total,
                        engineering_open_roles=eng,
                        ai_open_roles=ai,
                        surge_flag=False,
                        surge_posted_at=None,
                    )
                    session.add(snap)
                else:
                    snap.total_open_roles = total
                    snap.engineering_open_roles = eng
                    snap.ai_open_roles = ai

                # Compute baseline over recent history.
                baseline_values = [row[0] for row in session.execute(_get_recent_baseline_query(company, today)).all()]

                is_surge = False
                if not baseline_values:
                    # No prior snapshots — bootstrapping. Never flag a surge on first sight.
                    baseline = 0.0
                    logger.info(
                        "Hiring snapshot %s: total=%d eng=%d ai=%d — no baseline yet, storing initial snapshot",
                        company, total, eng, ai,
                    )
                else:
                    baseline = median(baseline_values)
                    if total >= 30:
                        if baseline >= 10:
                            min_increase = max(10, int(0.4 * baseline))
                            if total >= baseline + min_increase:
                                is_surge = True
                        else:
                            if total >= 25:
                                is_surge = True

                    logger.info(
                        "Hiring snapshot %s: total=%d eng=%d ai=%d baseline=%.1f surge=%s depts=%s locs=%s",
                        company,
                        total,
                        eng,
                        ai,
                        baseline,
                        is_surge,
                        ", ".join(top_depts) or "-",
                        ", ".join(top_locs) or "-",
                    )

                snap.surge_flag = is_surge

                # Attach convenience attributes (not persisted) for posting later.
                setattr(snap, "_careers_url", careers_url)
                setattr(snap, "_top_departments", top_depts)
                setattr(snap, "_top_locations", top_locs)

                if is_surge:
                    surged_snapshots.append(snap)

                session.commit()
            except Exception:  # noqa: BLE001
                session.rollback()
                logger.exception("Failed to upsert hiring snapshot for %s", company)
                continue

    return surged_snapshots

