from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from api.dependencies import get_db

router = APIRouter(prefix="/api/contacts")


def _validate_bu(bu: str) -> None:
    if bu and bu not in {"CC", "ACS"}:
        raise HTTPException(status_code=400, detail="business_unit must be CC or ACS")


@router.get("/unified/{contact_id}")
def get_unified_contact(contact_id: int, db=Depends(get_db)):
    contact = db.get_unified_contact(contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="not found")
    return contact


@router.get("/search")
def search_contacts(
    q: str = Query(""),
    business_unit: str = Query(None),
    limit: int = Query(10),
    db=Depends(get_db),
):
    q = q.strip()
    if not q:
        raise HTTPException(status_code=400, detail="q is required")
    if business_unit:
        _validate_bu(business_unit)
    clamped_limit = max(1, min(50, limit))
    return {
        "query": q,
        "results": db.search_contacts(q, business_unit=business_unit, limit=clamped_limit),
    }
