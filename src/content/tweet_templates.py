from __future__ import annotations

# The Main Alert (Pure text for maximum reach)
SINGLE_ALERT_TEMPLATE = """
📢 NEW TECH LAYOFF DETECTED

🏢 Company: {company}
📍 Location: {location}
🏭 Industry: {industry}
👥 Impact: {employees_line}{percent_line}

👇 SOURCE LINK IN REPLIES
#TechLayoffs #TechNews #{industry_hashtag}
""".strip()

# The Reply Link (Where the link-penalty is hidden)
REPLY_LINK_TEMPLATE = "🔗 Full Details: {source}"

def render_single_alert(*, company: str, employees: int | None, 
                        percentage: float | None, source_url: str | None,
                        location: str | None = None, industry: str | None = None, 
                        country: str | None = None) -> tuple[str, str]:
    
    # Format employees
    employees_line = f"{employees:,} staff" if employees else "Unspecified staff"
    
    # Format percentage
    percent_line = f" ({percentage:.1f}%)" if percentage else ""
    
    # Format Location
    loc_text = f"{location}, {country}" if location and country else (location or country or "Global")
    
    # Format Industry & Hashtag (Strip spaces, ampersands, and dots)
    ind_text = industry if industry else "Tech"
    ind_hashtag = (
        industry.replace(" ", "")
        .replace("&", "")
        .replace(".", "") 
        if industry else "Technology"
    )
    
    # Fallback for source
    source = source_url if source_url else "layoffs.fyi"

    # Render main alert
    main_text = SINGLE_ALERT_TEMPLATE.format(
        company=company.strip().upper(),
        location=loc_text,
        industry=ind_text,
        employees_line=employees_line,
        percent_line=percent_line,
        industry_hashtag=ind_hashtag
    )
    
    # Render reply
    reply_text = REPLY_LINK_TEMPLATE.format(source=source)
    
    return main_text, reply_text