from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)

AIRTABLE_URL = (
    "https://airtable.com/app1PaujS9zxVGUZ4/"
    "shroKsHx3SdYYOzeh/tblleV7Pnb6AcPCYL?viewControls=on"
)


def _parse_int_maybe(s: str | None) -> int | None:
    if not s:
        return None
    s = s.strip()
    if not s or s in {"—", "-", "N/A", "No Data"}:
        return None
    s = re.sub(r"[,\s]", "", s)
    return int(s) if s.isdigit() else None


def _parse_percent_maybe(s: str | None) -> float | None:
    if not s:
        return None
    s = s.strip().replace("%", "")
    if not s or s in {"—", "-", "N/A", "No Data"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_date_maybe(s: str | None) -> dt.date | None:
    if not s:
        return None
    s = s.strip()
    if not s or s in {"—", "-", "N/A", "No Data"}:
        return None

    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y", "%Y-%m-%d", "%b %d, %Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _stable_hash(obj: Any) -> str:
    b = json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(b).hexdigest()


@dataclass(frozen=True)
class LayoffEvent:
    source: str
    external_id: str
    payload: dict[str, Any]
    company: str
    event_date: dt.date | None
    employees: int | None
    percentage: float | None
    url: str | None
    content_hash: str
    location: str | None
    industry: str | None
    country: str | None


def _normalize_row_dict(row: dict[str, str | None]) -> LayoffEvent | None:
    company = (row.get("Company") or "").strip()
    if not company:
        return None

    employees_raw = row.get("# Laid Off")
    percent_raw = row.get("%")
    date_raw = row.get("Date")
    source_url = row.get("Source URL")
    
    # Extract new fields from the row dictionary
    location = row.get("Location")
    industry = row.get("Industry")
    country = row.get("Country")

    employees = _parse_int_maybe(employees_raw)
    percentage = _parse_percent_maybe(percent_raw)
    event_date = _parse_date_maybe(date_raw)

    normalized_obj = {
        "source": "layoffs_fyi",
        "company": company,
        "date": event_date.isoformat() if event_date else None,
        "employees": employees,
        "percentage": percentage,
        "url": source_url,
        "location": location,
        "industry": industry,
    }
    content_hash = _stable_hash(normalized_obj)

    raw_row = {
        "company": company,
        "laid_off": employees_raw,
        "percentage": percent_raw,
        "date": date_raw,
        "url": source_url,
        "location": location,
        "industry": industry,
        "country": country,
    }
    external_id = _stable_hash(raw_row)

    return LayoffEvent(
        source="layoffs_fyi",
        external_id=external_id,
        payload=raw_row,
        company=company,
        event_date=event_date,
        employees=employees,
        percentage=percentage,
        url=source_url,
        content_hash=content_hash,
        location=location,
        industry=industry,
        country=country,
    )

def _looks_like_date(text: str) -> bool:
    return _parse_date_maybe(text) is not None


def _looks_like_url(text: str) -> bool:
    return text.startswith("http://") or text.startswith("https://")


def _extract_visible_rows_from_main_frame(frame, max_rows: int) -> list[dict[str, str | None]]:
    js = """
    () => {
        let rowsByIndex = {};
        
        // Airtable's DOM structure can vary. Try multiple standard patterns to locate rows.
        
        // Pattern 1: data-rowindex (Standard Airtable grid structure)
        let rowContainers = Array.from(document.querySelectorAll('div[data-rowindex]'));
        if (rowContainers.length > 0) {
            rowContainers.forEach(container => {
                const idx = container.getAttribute('data-rowindex');
                if (!rowsByIndex[idx]) rowsByIndex[idx] = [];
                const cells = Array.from(container.querySelectorAll('.cell, [role="gridcell"], [data-columnindex], .gridCell'));
                if (cells.length > 0) {
                    cells.forEach(cell => rowsByIndex[idx].push(cell.innerText.trim()));
                } else {
                    Array.from(container.children).forEach(child => rowsByIndex[idx].push(child.innerText.trim()));
                }
            });
        } 
        
        // Pattern 2: role="row" (Fallback for alternative views)
        if (Object.keys(rowsByIndex).length === 0) {
            rowContainers = Array.from(document.querySelectorAll('[role="row"], tr'));
            rowContainers.forEach((row, idx) => {
                rowsByIndex[idx] = [];
                const cells = Array.from(row.querySelectorAll('[role="gridcell"], .cell, td, th'));
                if (cells.length > 0) {
                    cells.forEach(cell => rowsByIndex[idx].push(cell.innerText.trim()));
                } else {
                    Array.from(row.children).forEach(child => rowsByIndex[idx].push(child.innerText.trim()));
                }
            });
        }

        const sortedIndices = Object.keys(rowsByIndex).map(Number).sort((a,b) => a-b);
        return sortedIndices.map(idx => rowsByIndex[idx]).filter(r => r.length > 0);
    }
    """
    raw_rows = frame.evaluate(js)
    logger.info("Raw rows extracted from DOM: %d", len(raw_rows))

    rows: list[dict[str, str | None]] = []

    for data in raw_rows:
        # Minimum: company name + at least one more cell
        if not data or len(data) < 2:
            continue

        company = data[0]
        if not company or company in {"Company", "Hide fields", "Filter"}:
            continue

        # Find the Date column dynamically, but only search the first 8 columns
        # to avoid accidentally matching "Date Added" (which appears later in the row).
        date_idx = -1
        for i, val in enumerate(data[:8]):
            if val and _looks_like_date(val):
                date_idx = i
                break

        # Airtable Column Heuristics for Layoffs.fyi:
        # [0] Company | [1] Location | [2] Industry | [3] # Laid Off | [4] Date | [5] % | [6] Source | ... | [N] Country
        if date_idx == -1:
            # No parseable date found (entry may be newly added with incomplete data).
            # Fall back to best-effort positional mapping rather than dropping the row.
            logger.debug("No date found for company=%r; using positional fallback", company)
            row = {
                "Company": company,
                "Location": data[1] if len(data) > 1 else None,
                "Industry": data[2] if len(data) > 2 else None,
                "# Laid Off": data[3] if len(data) > 3 else None,
                "Date": None,
                "%": next((v for v in data[3:] if v and "%" in v), None),
                "Source URL": next((v for v in data[2:] if v and _looks_like_url(v)), None),
                "Country": data[-1] if len(data) > 5 else None,
            }
        else:
            row = {
                "Company": company,
                "Location": data[1] if len(data) > 1 else None,
                "Industry": data[2] if len(data) > 2 else None,
                "# Laid Off": data[date_idx - 1] if date_idx > 0 else None,
                "Date": data[date_idx],
                "%": next((v for v in data[date_idx + 1:] if v and "%" in v), None),
                "Source URL": next((v for v in data[date_idx + 1:] if v and _looks_like_url(v)), None),
                "Country": data[-1] if len(data) > 8 else None,
            }

        rows.append(row)
        if len(rows) >= max_rows:
            break

    return rows

def fetch_layoffs_fyi(timeout_seconds: int = 40, max_rows: int = 50) -> list[LayoffEvent]:
    events: list[LayoffEvent] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1800, "height": 1200})

        try:
            page.goto(
                AIRTABLE_URL,
                wait_until="domcontentloaded",
                timeout=timeout_seconds * 1000,
            )

            # Give complex React components initial time to mount
            page.wait_for_timeout(8000)
            
            # Explicitly wait for structural grid rows to appear rather than general text
            try:
                page.wait_for_selector('div[data-rowindex], [role="row"]', timeout=15000)
            except PlaywrightTimeoutError:
                logger.warning("Standard row selectors timed out. DOM may be rendering slowly.")

            logger.info("Page title: %s", page.title())
            logger.info("Page URL: %s", page.url)

            html = page.content()
            with open("airtable_debug.html", "w", encoding="utf-8") as f:
                f.write(html)
            logger.info("Saved rendered HTML to airtable_debug.html")

            main_frame = page.main_frame
            extracted_rows = _extract_visible_rows_from_main_frame(main_frame, max_rows=max_rows)

            if not extracted_rows:
                raise RuntimeError("Could not recover any visible Airtable rows from main frame")

            for row in extracted_rows:
                event = _normalize_row_dict(row)
                if event:
                    events.append(event)

            # In-memory dedupe
            deduped: list[LayoffEvent] = []
            seen_hashes: set[str] = set()
            for event in events:
                if event.content_hash in seen_hashes:
                    continue
                seen_hashes.add(event.content_hash)
                deduped.append(event)

            events = deduped

        except PlaywrightTimeoutError as e:
            raise RuntimeError("Timed out loading Airtable app directly") from e
        finally:
            browser.close()

    logger.info("Fetched %d layoff rows from Airtable", len(events))
    return events