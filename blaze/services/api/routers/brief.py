from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from api.dependencies import get_db

router = APIRouter(prefix="/api/brief")


@router.get("/daily")
def daily_brief(
    business_unit: str = Query(""),
    db=Depends(get_db),
):
    bu = business_unit.upper() if business_unit else ""
    effective_bu = bu if bu in {"CC", "ACS"} else None
    return db.daily_brief(business_unit=effective_bu)
