from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.db.models import NormalizedEvent, PostCandidate, RawEvent
from src.sources.layoffs_fyi import LayoffEvent

logger = logging.getLogger(__name__)


class DetectResult:
    def __init__(self, *, fetched: int, inserted_normalized: int, inserted_candidates: int):
        self.fetched = fetched
        self.inserted_normalized = inserted_normalized
        self.inserted_candidates = inserted_candidates


def upsert_layoffs_events(session: Session, events: list[LayoffEvent]) -> DetectResult:
    """
    Insert new raw events, normalize them, create post candidates.

    De-dupe strategy:
    - raw_events unique on (source, external_id)
    - normalized_events unique on content_hash
    - post_candidates unique on normalized_event_id
    """
    inserted_norm = 0
    inserted_candidates = 0

    for ev in events:
        # raw event insert (best-effort)
        raw = RawEvent(source=ev.source, external_id=ev.external_id, payload_json=ev.payload)
        session.add(raw)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            # raw already exists; continue to normalized de-dupe anyway
            session.begin()

        # normalized insert (strong de-dupe)
        exists = session.scalar(select(NormalizedEvent.id).where(NormalizedEvent.content_hash == ev.content_hash))
        if exists:
            continue

        norm = NormalizedEvent(
            source=ev.source,
            company=ev.company,
            event_date=ev.event_date,
            employees=ev.employees,
            percentage=ev.percentage,
            source_url=ev.url,
            content_hash=ev.content_hash,
            created_at=dt.datetime.now(dt.timezone.utc),
        )
        session.add(norm)
        session.flush()
        inserted_norm += 1

        candidate = PostCandidate(normalized_event_id=norm.id, status="pending", reason=None)
        session.add(candidate)
        inserted_candidates += 1

    session.commit()
    logger.info(
        "Detect result: fetched=%d inserted_normalized=%d inserted_candidates=%d",
        len(events),
        inserted_norm,
        inserted_candidates,
    )
    return DetectResult(fetched=len(events), inserted_normalized=inserted_norm, inserted_candidates=inserted_candidates)

