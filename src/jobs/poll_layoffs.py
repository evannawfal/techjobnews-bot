from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from src.pipeline.detect_new_layoffs import DetectResult, upsert_layoffs_events
from src.sources.layoffs_fyi import fetch_layoffs_fyi

logger = logging.getLogger(__name__)


def poll_layoffs(session: Session) -> DetectResult:
    events = fetch_layoffs_fyi()
    return upsert_layoffs_events(session, events)

