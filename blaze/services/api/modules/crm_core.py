from __future__ import annotations

from api.db import Database


def unified_lookup(db: Database, query: str, business_unit: str | None = None) -> dict:
    return {
        "query": query,
        "business_unit": business_unit,
        "results": db.search_contacts(query, business_unit=business_unit),
    }

