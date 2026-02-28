from __future__ import annotations

from fastapi import APIRouter, Depends

from api.dependencies import get_settings

router = APIRouter()


@router.get("/health")
def health(settings=Depends(get_settings)):
    return {
        "status": "ok",
        "service": "blaze-v4-api",
        "business_guardrails_enabled": settings.business_guardrails_enabled,
        "runtime_role": settings.runtime_role,
    }
