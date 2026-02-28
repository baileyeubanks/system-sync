from __future__ import annotations

import re
from typing import Any


def detect_business_unit(text: str, fallback: str = "CC") -> str:
    normalized = text.lower()
    if "astro cleaning" in normalized or "acs" in normalized:
        return "ACS"
    if "content co-op" in normalized or "content coop" in normalized or "cc" in normalized:
        return "CC"
    return fallback if fallback in {"CC", "ACS"} else "CC"


def route_intent(transcript: str) -> dict[str, Any]:
    text = (transcript or "").strip()
    normalized = text.lower()
    if not normalized:
        return {"intent": "unknown", "confidence": 0.0, "query": ""}

    if any(token in normalized for token in ("daily brief", "morning brief", "briefing")):
        return {"intent": "daily_brief", "confidence": 0.9, "query": text}

    if any(token in normalized for token in ("follow up", "follow-up", "remind", "reminder")):
        return {"intent": "follow_up_capture", "confidence": 0.85, "query": text}

    if any(token in normalized for token in ("quote", "invoice", "billing", "estimate")):
        return {"intent": "quote_status", "confidence": 0.8, "query": text}

    if any(token in normalized for token in ("job status", "crew", "cleaning status", "where are")):
        return {"intent": "job_status", "confidence": 0.76, "query": text}

    if any(token in normalized for token in ("who is", "find", "contact", "phone", "email", "lookup")):
        # Pull a loose name hint for contact lookups.
        name_hint = text
        m = re.search(r"(?:who is|find|lookup)\s+(.+)", normalized)
        if m:
            name_hint = m.group(1).strip()
        return {"intent": "contact_lookup", "confidence": 0.78, "query": name_hint}

    return {"intent": "unknown", "confidence": 0.4, "query": text}
