"""
sms.py — Twilio SMS endpoint for Blaze V4 FastAPI
POST /api/sms/send  { to, body, business_unit? }
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from api.agent_auth import check_business_unit, get_agent_id

router = APIRouter(prefix="/api/sms")


def _twilio_config():
    sid = os.getenv("TWILIO_ACCOUNT_SID", "")
    token = os.getenv("TWILIO_AUTH_TOKEN", "")
    from_num = os.getenv("TWILIO_FROM_NUMBER", "")
    return sid, token, from_num


def _normalize_phone(phone: str) -> str:
    """Convert phone to E.164 format."""
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits[0] == "1":
        return "+" + digits
    if phone.startswith("+"):
        return phone
    return "+" + digits


@router.post("/send")
async def send_sms(request: Request):
    agent_id = get_agent_id(request.headers)

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")

    to = (body.get("to") or "").strip()
    message = (body.get("body") or body.get("message") or "").strip()
    business_unit = (body.get("business_unit") or "").upper()

    if not to:
        raise HTTPException(status_code=400, detail="'to' is required")
    if not message:
        raise HTTPException(status_code=400, detail="'body' is required")

    if business_unit:
        ok, err = check_business_unit(agent_id, business_unit)
        if not ok:
            raise HTTPException(status_code=403, detail=err)

    sid, token, from_num = _twilio_config()
    if not sid or not token or not from_num:
        raise HTTPException(
            status_code=503,
            detail="Twilio not configured (missing TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM_NUMBER)"
        )

    to_e164 = _normalize_phone(to)

    url = "https://api.twilio.com/2010-04-01/Accounts/%s/Messages.json" % sid
    credentials = base64.b64encode(("%s:%s" % (sid, token)).encode()).decode()
    params = urllib.parse.urlencode({
        "To": to_e164,
        "From": from_num,
        "Body": message,
    }).encode()

    req = urllib.request.Request(url, data=params, method="POST")
    req.add_header("Authorization", "Basic " + credentials)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode()
        raise HTTPException(status_code=502, detail="Twilio error: " + error_body)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="SMS send failed: " + str(exc))

    return {
        "ok": True,
        "sid": result.get("sid"),
        "to": to_e164,
        "status": result.get("status"),
    }
