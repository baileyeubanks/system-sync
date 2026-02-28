from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_eleven, get_google, get_wix, get_xapi
from api.models import GoogleSmokeRequest, IntegrationSmokeRequest, XLogUsageRequest

router = APIRouter(prefix="/api/integrations")


@router.get("/x/usage")
def x_usage(xapi=Depends(get_xapi)):
    return xapi.get_usage()


@router.post("/x/log-usage")
def x_log_usage(body: XLogUsageRequest, xapi=Depends(get_xapi)):
    if body.amount_usd < 0:
        raise HTTPException(status_code=400, detail="amount_usd must be >= 0")
    return xapi.record_usage(body.amount_usd)


@router.post("/google/smoke")
def google_smoke(body: GoogleSmokeRequest, google=Depends(get_google)):
    subject = (body.delegated_subject or "").strip() or None
    bu = (body.business_unit or "").strip().upper() or None
    return google.hybrid_smoke(subject, business_unit=bu)


@router.post("/smoke")
def smoke_all(
    body: IntegrationSmokeRequest,
    google=Depends(get_google),
    wix=Depends(get_wix),
    xapi=Depends(get_xapi),
    eleven=Depends(get_eleven),
):
    subject = (body.delegated_subject or "").strip() or None
    return {
        "google": {
            "CC": google.hybrid_smoke(subject, business_unit="CC"),
            "ACS": google.hybrid_smoke(subject, business_unit="ACS"),
        },
        "wix": wix.smoke_probe(),
        "x": xapi.get_usage(),
        "elevenlabs": eleven.transcribe(None, text_hint="smoke probe"),
    }
