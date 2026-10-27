from __future__ import annotations

from src.db.models import HiringSnapshot

# Base template — factual skeleton. Claude-generated hook gets appended by the publish job.
# Keep this under ~220 chars so there's room for the hook within the 500-char budget.
HIRING_SURGE_TEMPLATE = """{company} is on a hiring surge 🚀

{total_open_roles}+ open roles right now{eng_ai_line}

{focus_line}

📍 {location_line}

{careers_url}""".strip()


def render_hiring_surge(snapshot: HiringSnapshot) -> str:
    top_depts: list[str] = getattr(snapshot, "_top_departments", []) or []
    top_locs: list[str] = getattr(snapshot, "_top_locations", []) or []
    careers_url: str = str(getattr(snapshot, "_careers_url", None) or "").strip() or "Company careers page"
    company: str = (snapshot.company or "").strip() or "Company"
    total: int = snapshot.total_open_roles or 0
    eng: int = snapshot.engineering_open_roles or 0
    ai: int = snapshot.ai_open_roles or 0

    # Engineering / AI inline line — only show non-zero counts.
    eng_parts: list[str] = []
    if eng > 0:
        eng_parts.append(f"⚙️ {eng}+ engineering")
    if ai > 0:
        eng_parts.append(f"🤖 {ai} AI/ML")
    eng_ai_line = "\n" + " · ".join(eng_parts) if eng_parts else ""

    # Focus areas — top 3 departments, inline.
    dept_items = [d for d in top_depts if d][:3]
    focus_line = "Focus: " + " · ".join(dept_items) if dept_items else "Hiring across multiple teams"

    # Locations — top 3, inline.
    loc_items = [loc for loc in top_locs if loc][:3]
    location_line = " · ".join(loc_items) if loc_items else "Multiple locations"

    return HIRING_SURGE_TEMPLATE.format(
        company=company,
        total_open_roles=total,
        eng_ai_line=eng_ai_line,
        focus_line=focus_line,
        location_line=location_line,
        careers_url=careers_url,
    )
