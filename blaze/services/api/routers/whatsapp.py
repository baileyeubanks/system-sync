"""
whatsapp.py — WhatsApp Business Cloud API router for Blaze V4 FastAPI
GET  /api/whatsapp/webhook  — Meta webhook verification
POST /api/whatsapp/webhook  — Receive incoming messages
POST /api/whatsapp/send     — Send outbound messages (agent tool)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from typing import Optional, Tuple

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from api.agent_auth import check_business_unit, get_agent_id

logger = logging.getLogger("blaze.whatsapp")
router = APIRouter(prefix="/api/whatsapp")

GRAPH_API = "https://graph.facebook.com/v21.0"
SUPABASE_URL = "https://briokwdoonawhxisbydy.supabase.co"
CONTACTS_DB = os.path.expanduser("~/blaze-data/contacts/contacts.db")


def _wa_config():
    phone_id = os.getenv("WHATSAPP_PHONE_ID", "")
    token = os.getenv("WHATSAPP_TOKEN", "")
    verify_token = os.getenv("WHATSAPP_VERIFY_TOKEN", "blazeacs2026")
    app_secret = os.getenv("WHATSAPP_APP_SECRET", "")
    return phone_id, token, verify_token, app_secret


def _supa_key():
    return os.getenv("SUPABASE_SERVICE_KEY", "")


# ---------------------------------------------------------------------------
# Webhook verification (GET) — Meta hub.challenge handshake
# ---------------------------------------------------------------------------

@router.get("/webhook")
async def verify_webhook(request: Request):
    params = dict(request.query_params)
    mode = params.get("hub.mode", "")
    token = params.get("hub.verify_token", "")
    challenge = params.get("hub.challenge", "")

    _, _, verify_token, _ = _wa_config()

    if mode == "subscribe" and token == verify_token:
        logger.info("WhatsApp webhook verified")
        return PlainTextResponse(challenge)

    logger.warning("WhatsApp webhook verification failed — bad token")
    raise HTTPException(status_code=403, detail="verification failed")


# ---------------------------------------------------------------------------
# Receive incoming messages (POST)
# ---------------------------------------------------------------------------

@router.post("/webhook")
async def receive_webhook(request: Request):
    raw_body = await request.body()

    _, _, _, app_secret = _wa_config()
    if app_secret:
        sig_header = request.headers.get("X-Hub-Signature-256", "")
        expected = "sha256=" + hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig_header, expected):
            logger.warning("WhatsApp signature mismatch — dropping")
            raise HTTPException(status_code=403, detail="invalid signature")

    try:
        payload = json.loads(raw_body.decode())
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON")

    try:
        _process_webhook(payload)
    except Exception as exc:
        logger.error("WhatsApp webhook processing error: %s", exc, exc_info=True)

    return {"ok": True}


def _process_webhook(payload: dict):
    entries = payload.get("entry", [])
    for entry in entries:
        for change in entry.get("changes", []):
            value = change.get("value", {})
            messages = value.get("messages", [])
            contacts = value.get("contacts", [])

            contact_names = {}
            for c in contacts:
                wa_id = c.get("wa_id", "")
                name = c.get("profile", {}).get("name", "")
                if wa_id:
                    contact_names[wa_id] = name

            for msg in messages:
                _route_whatsapp_message(msg, contact_names, value)


def _route_whatsapp_message(msg: dict, contact_names: dict, value: dict):
    msg_type = msg.get("type", "")
    wa_id = msg.get("from", "")
    msg_id = msg.get("id", "")
    timestamp = int(msg.get("timestamp", 0))

    phone = "+" + wa_id if not wa_id.startswith("+") else wa_id

    if msg_type == "text":
        text = msg.get("text", {}).get("body", "")
    elif msg_type == "audio":
        text = "[voice message]"
    elif msg_type == "image":
        text = "[image]"
    elif msg_type == "document":
        text = "[document: %s]" % msg.get("document", {}).get("filename", "")
    elif msg_type == "sticker":
        text = "[sticker]"
    elif msg_type == "location":
        loc = msg.get("location", {})
        text = "[location: %s, %s]" % (loc.get("latitude", ""), loc.get("longitude", ""))
    else:
        text = "[%s]" % msg_type

    if not text:
        return

    display_name = contact_names.get(wa_id, "")
    age_secs = time.time() - timestamp
    is_historical = age_secs > 7200

    logger.info("WhatsApp msg from %s (%s): %s", phone, display_name, text[:80])

    _mark_read(msg_id)

    # --- Contact cross-reference (Supabase + contacts.db) ---
    contact_record = _sync_wa_contact(phone, display_name)

    # Classify using enriched contact data
    sender_class, sender_info = _classify_wa_sender(phone, display_name, contact_record)

    do_telegram = (not is_historical) and _should_telegram(phone, timestamp)

    _dispatch_to_acs_worker(phone, text, sender_class, sender_info, do_telegram, source="whatsapp")


# ---------------------------------------------------------------------------
# Supabase contact cross-reference + upsert
# ---------------------------------------------------------------------------

def _supa_request(method: str, path: str, data: dict = None, params: str = "") -> Optional[dict]:
    key = _supa_key()
    if not key:
        return None
    url = SUPABASE_URL + "/rest/v1/" + path + (("?" + params) if params else "")
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("apikey", key)
    req.add_header("Authorization", "Bearer " + key)
    req.add_header("Content-Type", "application/json")
    req.add_header("Prefer", "return=representation")
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        logger.warning("Supabase %s %s → %s: %s", method, path, exc.code, exc.read().decode()[:200])
        return None
    except Exception as exc:
        logger.warning("Supabase request failed: %s", exc)
        return None


def _sync_wa_contact(phone: str, display_name: str) -> Optional[dict]:
    """
    Look up contact in Supabase by phone or whatsapp_phone.
    - Found:  update last_contacted, preferred_channel, whatsapp_phone if blank
    - Not found: create new wa_prospect contact
    Returns the contact dict (from Supabase) or None on failure.
    """
    # E.164 lookup: match phone column OR whatsapp_phone column
    encoded = urllib.request.quote(phone)
    result = _supa_request("GET", "contacts",
                           params="or=(phone.eq.%s,whatsapp_phone.eq.%s)&limit=1" % (encoded, encoded))

    now_iso = _iso_now()

    if result:  # existing contact
        contact = result[0]
        contact_id = contact["id"]
        patch: dict = {"last_contacted": now_iso, "preferred_channel": "whatsapp"}
        if not contact.get("whatsapp_phone"):
            patch["whatsapp_phone"] = phone
        # Merge "whatsapp" tag if not present
        existing_tags = contact.get("tags") or []
        if isinstance(existing_tags, str):
            try:
                existing_tags = json.loads(existing_tags)
            except Exception:
                existing_tags = []
        if "whatsapp" not in existing_tags:
            patch["tags"] = existing_tags + ["whatsapp"]
        updated = _supa_request("PATCH", "contacts",
                                data=patch,
                                params="id=eq.%s" % contact_id)
        merged = {**contact, **patch}
        logger.info("Supabase: updated existing contact id=%s (%s)", contact_id, contact.get("name", phone))
        _upsert_local_contact(phone, contact.get("name") or display_name, merged)
        return merged

    # No match — create new prospect
    tags = ["wa_prospect", "whatsapp"]
    new_contact = {
        "name": display_name or phone,
        "phone": phone,
        "whatsapp_phone": phone,
        "preferred_channel": "whatsapp",
        "tags": tags,
        "last_contacted": now_iso,
        "metadata": {"source": "whatsapp_inbound", "first_wa_message": now_iso},
    }
    created = _supa_request("POST", "contacts", data=new_contact)
    if created:
        record = created[0] if isinstance(created, list) else created
        logger.info("Supabase: created new wa_prospect %s (%s)", phone, display_name)
        _upsert_local_contact(phone, display_name or phone, record)
        return record

    logger.warning("Supabase: failed to sync contact for %s", phone)
    return None


def _upsert_local_contact(phone: str, name: str, supa_record: dict):
    """Sync Supabase contact into local contacts.db."""
    try:
        conn = sqlite3.connect(CONTACTS_DB, timeout=5)
        # Check if exists
        cur = conn.execute("SELECT id FROM contacts WHERE phone=? LIMIT 1", (phone,))
        row = cur.fetchone()

        tags_raw = supa_record.get("tags") or []
        if isinstance(tags_raw, list):
            tags_str = ",".join(tags_raw)
        else:
            tags_str = str(tags_raw)

        priority = supa_record.get("priority_score") or 0
        notes = supa_record.get("ai_summary") or ""
        source = "whatsapp"
        now = _iso_now()

        if row:
            conn.execute(
                "UPDATE contacts SET last_contacted=?, source=?, updated_at=? WHERE phone=?",
                (now, source, now, phone)
            )
        else:
            conn.execute(
                """INSERT OR IGNORE INTO contacts
                   (name, phone, category, tags, priority_score, notes, source, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (name, phone, "unknown", tags_str, priority, notes, source, now, now)
            )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("contacts.db upsert failed for %s: %s", phone, exc)


def _iso_now() -> str:
    import datetime
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Telegram rate limiter
# ---------------------------------------------------------------------------

_tg_last_sent: dict = {}
TG_COOLDOWN_SECS = 900

def _should_telegram(phone: str, msg_ts: float) -> bool:
    last = _tg_last_sent.get(phone, 0)
    if time.time() - last < TG_COOLDOWN_SECS:
        return False
    _tg_last_sent[phone] = time.time()
    return True


# ---------------------------------------------------------------------------
# Sender classification
# ---------------------------------------------------------------------------

KNOWN_EMPLOYEES_WA = {
    "+15048581959": {"name": "Caio",    "role": "president"},
    "+13464015841": {"name": "Caio",    "role": "president"},
    "+15013515927": {"name": "Bailey",  "role": "owner"},
    "+18502471622": {"name": "Kailany", "role": "crew"},
    "+18575402386": {"name": "Diego",   "role": "crew"},
    "+17137322140": {"name": "Thiala",  "role": "crew"},
}


def _classify_wa_sender(phone: str, display_name: str,
                         supa_contact: Optional[dict]) -> Tuple[str, dict]:
    # Known employees always win
    if phone in KNOWN_EMPLOYEES_WA:
        return "employee", KNOWN_EMPLOYEES_WA[phone]

    # Use Supabase record if we have one
    if supa_contact:
        tags = supa_contact.get("tags") or []
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except Exception:
                tags = []

        name = supa_contact.get("name") or display_name or phone
        priority = supa_contact.get("priority_score") or 0
        is_core = supa_contact.get("is_core") or False

        # Crew / staff tags → treat as employee
        if any(t in tags for t in ["crew", "employee", "staff"]):
            return "employee", {"name": name, "role": "crew"}

        # Known client tags
        if any(t in tags for t in ["acs_client", "client", "recurring"]):
            return "client", {
                "name": name,
                "priority_score": priority,
                "is_core": is_core,
                "tags": tags,
                "ai_summary": supa_contact.get("ai_summary", ""),
            }

        # Warm prospects
        if any(t in tags for t in ["wa_prospect", "prospect", "warm_lead"]):
            return "prospect", {
                "name": name,
                "priority_score": priority,
                "tags": tags,
                "new_contact": "wa_prospect" in tags,
            }

        # Fallback — known but unclassified
        return "known", {"name": name, "priority_score": priority, "tags": tags}

    # No Supabase record + not in employee list → unknown prospect
    return "prospect", {"name": display_name or "Unknown", "new_contact": True}


# ---------------------------------------------------------------------------
# Dispatch to acs-worker via OpenClaw
# ---------------------------------------------------------------------------

OPENCLAW = "/usr/local/bin/openclaw"
OPENCLAW_ENV = {**os.environ, "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"}
AI_TIMEOUT = 120

def _dispatch_to_acs_worker(phone: str, text: str, sender_class: str,
                              sender_info: dict, do_telegram: bool,
                              source: str = "whatsapp"):
    sender_name = sender_info.get("name", phone)
    is_urgent = any(w in text.lower() for w in ["cancel", "emergency", "complaint", "urgent", "asap"])

    if sender_class == "employee":
        if do_telegram or is_urgent:
            role = sender_info.get("role", "crew")
            _tg_caio("[WhatsApp] %s (%s): %s" % (sender_name, role, text[:200]))
        return

    priority = sender_info.get("priority_score", 0)
    tags = sender_info.get("tags", [])
    is_new = sender_info.get("new_contact", False)
    ai_summary = sender_info.get("ai_summary", "")

    new_label = " [NEW CONTACT]" if is_new else ""
    summary_line = ("\nContact summary: %s" % ai_summary) if ai_summary else ""

    prompt = (
        "Incoming WhatsApp message to ACS (Astro Cleaning Services).%s\n"
        "From: %s | Phone: %s | Class: %s | Priority: %s | Tags: %s | Source: %s\n"
        "%s"
        "Message: %s\n\n"
        "Respond appropriately. If new contact, start intake. "
        "If existing client, check history and handle request. "
        "If urgent (cancel/complaint), escalate to Caio via Telegram."
    ) % (new_label, sender_name, phone, sender_class, priority, tags, source, summary_line, text)

    if do_telegram or is_urgent:
        prefix = "⚠️ " if is_urgent else "[WhatsApp%s] " % (" NEW" if is_new else "")
        _tg_caio("%s%s (%s): %s" % (prefix, sender_name, phone, text[:200]))

    try:
        result = subprocess.run(
            [OPENCLAW, "agent", "--agent", "acs-worker", "--message", prompt, "--json"],
            capture_output=True, text=True, timeout=AI_TIMEOUT, env=OPENCLAW_ENV
        )
        if result.returncode != 0:
            logger.warning("acs-worker dispatch failed: %s", result.stderr[:200])
    except subprocess.TimeoutExpired:
        logger.warning("acs-worker timed out for %s", phone)
    except Exception as exc:
        logger.error("acs-worker dispatch error: %s", exc)


def _tg_caio(msg: str):
    try:
        subprocess.Popen(
            [OPENCLAW, "message", "send",
             "--channel", "telegram", "--account", "astro",
             "--target", "telegram:7124538299", "--message", msg],
            env=OPENCLAW_ENV
        )
    except Exception as exc:
        logger.warning("Telegram notify failed: %s", exc)


# ---------------------------------------------------------------------------
# Mark message as read
# ---------------------------------------------------------------------------

def _mark_read(msg_id: str):
    phone_id, token, _, _ = _wa_config()
    if not phone_id or not token:
        return
    url = "%s/%s/messages" % (GRAPH_API, phone_id)
    data = json.dumps({"messaging_product": "whatsapp", "status": "read", "message_id": msg_id}).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5):
            pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Send outbound message (agent tool)
# ---------------------------------------------------------------------------

@router.post("/send")
async def send_whatsapp(request: Request):
    agent_id = get_agent_id(request.headers)

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")

    to = (body.get("to") or body.get("phone") or "").strip()
    message = (body.get("message") or body.get("body") or "").strip()
    business_unit = (body.get("business_unit") or "").upper()

    if not to:
        raise HTTPException(status_code=400, detail="'to' is required")
    if not message:
        raise HTTPException(status_code=400, detail="'message' is required")

    if business_unit:
        ok, err = check_business_unit(agent_id, business_unit)
        if not ok:
            raise HTTPException(status_code=403, detail=err)

    phone_id, token, _, _ = _wa_config()
    if not phone_id or not token:
        raise HTTPException(status_code=503, detail="WhatsApp not configured")

    wa_to = to.lstrip("+").replace("-", "").replace(" ", "")

    url = "%s/%s/messages" % (GRAPH_API, phone_id)
    payload = {
        "messaging_product": "whatsapp",
        "to": wa_to,
        "type": "text",
        "text": {"body": message},
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode()
        raise HTTPException(status_code=502, detail="WhatsApp API error: " + error_body)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="WhatsApp send failed: " + str(exc))

    msg_id = result.get("messages", [{}])[0].get("id")
    return {"ok": True, "message_id": msg_id, "to": "+" + wa_to}
