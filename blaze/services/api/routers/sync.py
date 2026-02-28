from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_google, get_imessage, get_wix

router = APIRouter(prefix="/api/sync")


@router.post("/wix/contacts")
def sync_wix_contacts(body: dict, wix=Depends(get_wix)):
    result = wix.sync_contacts(body)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)
    return result


@router.post("/wix/billing")
def sync_wix_billing(body: dict, wix=Depends(get_wix)):
    result = wix.sync_billing(body)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)
    return result


@router.post("/imessage/export")
def sync_imessage_export(body: dict, imessage=Depends(get_imessage)):
    result = imessage.ingest_export(body)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)
    return result


@router.post("/imessage/chatdb")
def sync_imessage_chatdb(body: dict, imessage=Depends(get_imessage)):
    result = imessage.ingest_chatdb(body)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)
    return result


@router.post("/google/gmail")
def sync_gmail(body: dict, google=Depends(get_google)):
    result = google.gmail_ingest_recent(body)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)
    return result
