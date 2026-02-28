from __future__ import annotations

import secrets
from time import time

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_db, get_eleven
from api.intent_router import route_intent
from api.models import VoiceSpeakRequest, VoiceTranscribeRequest

router = APIRouter(prefix="/api/voice")


def _voice_router_response(transcript, business_unit, idempotency_key, db):
    route = route_intent(transcript)
    intent = route.get("intent", "unknown")

    if intent == "contact_lookup":
        matches = db.search_contacts(
            route.get("query", transcript), business_unit=business_unit, limit=5
        )
        return {"intent": intent, "matches": matches, "query": route.get("query")}

    if intent == "quote_status":
        target_bu = "CC" if business_unit == "CC" else "ACS"
        snapshot = db.get_billing_snapshot(target_bu)
        return {"intent": intent, "billing": snapshot}

    if intent == "job_status":
        preview = db.build_acs_reminder_preview(lead_minutes=30)
        return {"intent": intent, "acs_jobs_preview": preview[:5], "count": len(preview)}

    if intent == "follow_up_capture":
        follow_up_id = db.add_follow_up(
            business_unit=business_unit,
            notes=transcript,
            idempotency_key=idempotency_key,
        )
        return {"intent": intent, "follow_up_id": follow_up_id}

    if intent == "daily_brief":
        brief = db.daily_brief(business_unit=business_unit)
        return {"intent": intent, "daily_brief": brief}

    return {"intent": "unknown", "note": "No matching voice workflow found."}


@router.post("/transcribe")
def transcribe(body: VoiceTranscribeRequest, db=Depends(get_db), eleven=Depends(get_eleven)):
    bu = (body.business_unit or "CC").upper()
    if bu not in {"CC", "ACS"}:
        raise HTTPException(status_code=400, detail="business_unit must be CC or ACS")

    idempotency_key = body.idempotency_key or secrets.token_hex(12)
    started = time()
    result = eleven.transcribe(body.audio_base64, body.text_hint)
    transcript = (result.get("text") or body.text_hint or "").strip()
    voice_route = _voice_router_response(
        transcript, business_unit=bu, idempotency_key=idempotency_key, db=db
    )
    latency_ms = int((time() - started) * 1000)

    db.add_voice_event(
        business_unit=bu,
        intent=voice_route.get("intent", "transcribe"),
        transcript=transcript,
        confidence=result.get("confidence"),
        latency_ms=latency_ms,
        status="ok" if result.get("ok") else "error",
        idempotency_key=idempotency_key,
        details={"stt_mode": result.get("mode"), "route": voice_route},
    )

    if not result.get("ok"):
        raise HTTPException(
            status_code=502,
            detail={
                "ok": False,
                "business_unit": bu,
                "stt": result,
                "route": voice_route,
                "idempotency_key": idempotency_key,
            },
        )
    return {
        "ok": True,
        "business_unit": bu,
        "stt": result,
        "route": voice_route,
        "idempotency_key": idempotency_key,
    }


@router.post("/speak")
def speak(body: VoiceSpeakRequest, db=Depends(get_db), eleven=Depends(get_eleven)):
    bu = (body.business_unit or "CC").upper()
    if bu not in {"CC", "ACS"}:
        raise HTTPException(status_code=400, detail="business_unit must be CC or ACS")

    text = (body.text or "").strip()
    result = eleven.speak(text, body.voice_id)
    idempotency_key = body.idempotency_key or secrets.token_hex(12)
    db.add_voice_event(
        business_unit=bu,
        intent=body.intent,
        transcript=text,
        confidence=None,
        latency_ms=result.get("latency_ms"),
        status="ok" if result.get("ok") else "error",
        idempotency_key=idempotency_key,
        details={"tts_mode": result.get("mode"), "voice_id": result.get("voice_id")},
    )
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result)
    return result
