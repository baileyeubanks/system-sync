from __future__ import annotations

from api.db import Database


def build_daily_brief(db: Database, business_unit: str | None = None) -> dict:
    return db.daily_brief(business_unit=business_unit)

