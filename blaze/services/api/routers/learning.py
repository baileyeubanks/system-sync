from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from api.dependencies import get_db
from api.models import LearningInsightRequest, LearningItemRequest, LearningSourceRequest

router = APIRouter(prefix="/api/learning")


def _validate_bu(bu: str) -> None:
    if bu not in {"CC", "ACS"}:
        raise HTTPException(status_code=400, detail="business_unit must be CC or ACS")


@router.get("/digest")
def learning_digest(
    business_unit: str = Query("CC"),
    limit: int = Query(20),
    tag: str = Query(""),
    db=Depends(get_db),
):
    bu = (business_unit or "CC").upper()
    _validate_bu(bu)
    tag_val = tag.strip() or None
    return db.list_learning_digest(business_unit=bu, limit=limit, tag=tag_val)


@router.get("/search")
def learning_search(
    q: str = Query(""),
    business_unit: str = Query(""),
    limit: int = Query(20),
    db=Depends(get_db),
):
    q = q.strip()
    if not q:
        raise HTTPException(status_code=400, detail="q is required")
    bu = business_unit.upper() if business_unit else None
    if bu:
        _validate_bu(bu)
    return db.search_learning_knowledge(query=q, business_unit=bu, limit=limit)


@router.post("/source")
def add_learning_source(body: LearningSourceRequest, db=Depends(get_db)):
    bu = (body.business_unit or "CC").upper()
    _validate_bu(bu)
    source_type = (body.source_type or "").strip()
    source_ref = (body.source_ref or "").strip()
    if not source_type or not source_ref:
        raise HTTPException(status_code=400, detail="source_type and source_ref are required")
    source_id = db.upsert_learning_source(
        business_unit=bu,
        source_type=source_type,
        source_ref=source_ref,
        title=(body.title or "").strip() or None,
        metadata=body.metadata if isinstance(body.metadata, dict) else None,
        active=bool(body.active),
    )
    return {"ok": True, "source_id": source_id, "business_unit": bu}


@router.post("/item")
def add_learning_item(body: LearningItemRequest, db=Depends(get_db)):
    bu = (body.business_unit or "CC").upper()
    _validate_bu(bu)
    source_type = (body.source_type or "").strip()
    title = (body.title or "").strip()
    if not source_type or not title:
        raise HTTPException(status_code=400, detail="source_type and title are required")
    tags = body.tags
    if tags is not None and not isinstance(tags, list):
        raise HTTPException(status_code=400, detail="tags must be a list when provided")
    learning_item_id = db.add_learning_item(
        business_unit=bu,
        source_type=source_type,
        source_ref=(body.source_ref or "").strip() or None,
        title=title,
        url=(body.url or "").strip() or None,
        published_at=(body.published_at or "").strip() or None,
        transcript_text=(body.transcript_text or "").strip() or None,
        summary_text=(body.summary_text or "").strip() or None,
        relevance_score=float(body.relevance_score or 0),
        tags=tags,
        idempotency_key=(body.idempotency_key or "").strip() or None,
        source_id=int(body.source_id) if body.source_id else None,
    )
    return {"ok": True, "learning_item_id": learning_item_id, "business_unit": bu}


@router.post("/insight")
def add_learning_insight(body: LearningInsightRequest, db=Depends(get_db)):
    bu = (body.business_unit or "CC").upper()
    _validate_bu(bu)
    insight_type = (body.insight_type or "").strip()
    title = (body.title or "").strip()
    insight_text = (body.insight_text or "").strip()
    if not insight_type or not title or not insight_text:
        raise HTTPException(status_code=400, detail="insight_type, title, and insight_text are required")
    tags = body.tags
    if tags is not None and not isinstance(tags, list):
        raise HTTPException(status_code=400, detail="tags must be a list when provided")
    insight_id = db.add_learning_insight(
        business_unit=bu,
        insight_type=insight_type,
        title=title,
        insight_text=insight_text,
        confidence=float(body.confidence or 0.5),
        priority=int(body.priority or 3),
        learning_item_id=int(body.learning_item_id) if body.learning_item_id else None,
        contact_id=int(body.contact_id) if body.contact_id else None,
        tags=tags,
        status=(body.status or "active").strip(),
    )
    return {"ok": True, "insight_id": insight_id, "business_unit": bu}
