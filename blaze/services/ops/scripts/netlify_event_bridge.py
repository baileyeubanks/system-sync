#!/usr/bin/env python3
"""
netlify_event_bridge.py — Routes Supabase events from Netlify to OpenClaw agents.

Polls the `events` table for new actionable events and dispatches them to the
correct OpenClaw agent(s) via the CLI.

Tracks processed events in a local SQLite file to avoid double-routing.
Key format in DB: "{event_id}:{agent}" — supports multi-agent dispatch per event.
Runs every 60s via LaunchAgent (com.blaze.netlify-bridge.plist).
"""
from __future__ import annotations

import base64
import json
import os
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://briokwdoonawhxisbydy.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

# Local tracking DB — prevents double-routing across restarts
TRACKING_DB = os.path.expanduser("~/blaze-data/netlify_bridge_seen.db")

# Max dispatches per run (prevents flooding agents)
MAX_DISPATCH_PER_RUN = 10

# Only route these event types to agents
ROUTABLE_EVENTS = {
    # ACS — Astro Cleaning Services
    "quote_submitted",
    "deposit_paid",
    "job_scheduled",
    "job_self_scheduled",
    "job_status_updated",
    "reschedule_requested",
    "receipt_sent",
    # CC — Content Co-op
    "co_edit_requested",
    "brief_submitted",           # New client brief from /onboard wizard
    "brief_message_from_client", # Client replied in their portal
    "client_request_submitted",    # Client submitted a portal request
}

# Event type → list of target agents
# CC events go to BOTH main (Bailey via @blazenbailey_bot) AND cc-worker
EVENT_AGENT_MAP = {
    # ACS
    "quote_submitted":           ["acs-worker"],
    "deposit_paid":              ["acs-worker"],
    "job_scheduled":             ["acs-worker"],
    "job_self_scheduled":        ["acs-worker"],
    "job_status_updated":        ["acs-worker"],
    "reschedule_requested":      ["acs-worker"],
    "receipt_sent":              ["acs-worker"],
    # CC — Bailey gets everything via main, cc-worker handles the workflow
    "co_edit_requested":         ["main", "cc-worker"],
    "brief_submitted":           ["main", "cc-worker"],
    "brief_message_from_client": ["main", "cc-worker"],
    "client_request_submitted":    ["acs-worker"],
}

# Direct-dispatch events — handled here on Mac Mini, never routed to agents
# These bypass the agent system entirely for speed and reliability.
DIRECT_DISPATCH_EVENTS = {"send_imessage", "send_email", "client_request_submitted"}

OPENCLAW_PATH = "/usr/local/bin/openclaw"
PATH_ENV = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"


# ---------------------------------------------------------------------------
# Local tracking
# ---------------------------------------------------------------------------

def init_db(db_path):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS routed_events (
            event_id TEXT PRIMARY KEY,
            routed_at TEXT NOT NULL,
            agent TEXT NOT NULL,
            event_type TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def is_already_routed(conn, event_id, agent):
    """Check using compound key '{event_id}:{agent}' to support multi-agent routing."""
    row = conn.execute(
        "SELECT 1 FROM routed_events WHERE event_id=?", ("%s:%s" % (event_id, agent),)
    ).fetchone()
    return row is not None


def mark_routed(conn, event_id, agent, event_type):
    """Store compound key '{event_id}:{agent}' — each agent tracked independently."""
    conn.execute(
        "INSERT OR IGNORE INTO routed_events (event_id, routed_at, agent, event_type) VALUES (?,?,?,?)",
        ("%s:%s" % (event_id, agent), datetime.now(timezone.utc).isoformat(), agent, event_type),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Supabase helpers
# ---------------------------------------------------------------------------

def supabase_get(path, params=None):
    """GET from Supabase REST API."""
    url = SUPABASE_URL.rstrip("/") + "/rest/v1/" + path.lstrip("/")
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    req.add_header("apikey", SUPABASE_KEY)
    req.add_header("Authorization", "Bearer " + SUPABASE_KEY)
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def fetch_recent_events(hours=2, offset=0, limit=50):
    """Fetch recent actionable events from Supabase."""
    from datetime import timedelta
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    types = ",".join(list(ROUTABLE_EVENTS) + list(DIRECT_DISPATCH_EVENTS))
    params = {
        "select": "id,type,payload,contact_id,business_id,created_at",
        "type": "in.(" + types + ")",
        "created_at": "gte." + since,
        "order": "created_at.asc",
        "limit": str(limit),
        "offset": str(offset),
    }
    return supabase_get("events", params)


def fetch_contact(contact_id):
    """Look up a contact by UUID."""
    if not contact_id:
        return None
    try:
        rows = supabase_get(
            "contacts",
            {"select": "id,name,phone,email,city,state", "id": "eq." + contact_id, "limit": "1"}
        )
        return rows[0] if rows else None
    except Exception:
        return None


def fetch_job(job_id):
    """Look up a job by UUID."""
    if not job_id:
        return None
    try:
        rows = supabase_get(
            "jobs",
            {"select": "id,scheduled_start,status,notes", "id": "eq." + job_id, "limit": "1"}
        )
        return rows[0] if rows else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Message builders
# ---------------------------------------------------------------------------

def cents_to_dollars(cents):
    if not cents:
        return "$0"
    return "$%.2f" % (int(cents) / 100.0)


def fmt_date(iso_str):
    if not iso_str:
        return "unknown date"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%a %b %-d at %-I:%M %p")
    except Exception:
        return iso_str[:16]


def build_message(event):
    """Build a natural-language briefing for the agent based on event type."""
    etype = event.get("type", "")
    payload = event.get("payload") or {}
    contact_id = event.get("contact_id") or payload.get("contact_id")
    contact = fetch_contact(contact_id)
    name = contact.get("name", "a client") if contact else "a client"

    if etype == "quote_submitted":
        q = payload.get("quote") or {}
        total = q.get("estimated_total", 0)
        service = (q.get("service_type") or "cleaning").replace("_", " ")
        sqft = q.get("square_footage", 0)
        freq = q.get("frequency", "one-time")
        beds = q.get("bedrooms", "?")
        baths = q.get("bathrooms", "?")
        phone = (contact.get("phone") if contact else None) or payload.get("phone") or ""
        email = (contact.get("email") if contact else None) or payload.get("email") or ""
        digits = "".join(c for c in phone if c.isdigit())
        phone_e164 = "+1" + digits if len(digits) == 10 else ("+" + digits if digits else "")
        first = (name.split()[0] if name and name != "a client" else "there")
        ack_msg = (
            "Hi %s! Thanks for reaching out to Astro Cleaning Services \u2728 "
            "We received your %s quote and Caio will personally follow up with you "
            "in the next few hours. Questions? Just reply here." % (first, service)
        )
        return (
            "New quote submitted from %(name)s.\n"
            "Service: %(service)s | $%(total)s | %(freq)s | %(sqft)s sqft | %(beds)s bed / %(baths)s bath\n"
            "Phone: %(phone_e164)s | Email: %(email)s\n"
            "Admin: https://astrocleanings.com/admin/quotes\n\n"
            "STEP 1 — Call the blaze_v4 tool with action=\"imsg_send\", "
            "recipient=\"%(phone_e164)s\", "
            "message=\"%(ack_msg)s\" "
            "(this is a standard ACK — send it immediately, no approval needed).\n\n"
            "STEP 2 — After sending, write a short personalized follow-up draft (2-3 sentences) "
            "in your reply text for Caio to review. Do NOT send STEP 2 automatically." % dict(
                name=name, service=service, total=total, freq=freq, sqft=sqft,
                beds=beds, baths=baths, phone_e164=phone_e164, email=email,
                ack_msg=ack_msg,
            )
        )

    if etype == "deposit_paid":
        amount = payload.get("amount_cents")
        amt_str = cents_to_dollars(amount) if amount else "a deposit"
        return (
            "Deposit paid by %s — %s received. They're confirmed! "
            "Schedule them in /admin/schedule." % (name, amt_str)
        )

    if etype in ("job_scheduled", "job_self_scheduled"):
        job_id = payload.get("job_id")
        job = fetch_job(job_id) if job_id else None
        date_str = fmt_date(job.get("scheduled_start")) if job else "a scheduled date"
        return (
            "New job scheduled for %s on %s. "
            "Check /admin/schedule for details." % (name, date_str)
        )

    if etype == "job_status_updated":
        status = payload.get("status", "unknown")
        job_id = payload.get("job_id")
        return (
            "Job status updated to '%s' for %s (job %s)." % (status, name, job_id or "?")
        )

    if etype == "reschedule_requested":
        return (
            "Reschedule requested by %s. "
            "Check /admin/schedule to find a new time." % name
        )

    if etype == "receipt_sent":
        return (
            "Receipt sent to %s after job completion." % name
        )

    if etype == "co_edit_requested":
        project = payload.get("project_name") or payload.get("project_id") or "a project"
        return (
            "New co-edit request for '%s'. Check the co-edit queue." % project
        )

    if etype == "brief_submitted":
        company = payload.get("company") or "Unknown company"
        contact_name = payload.get("contact_name") or "a client"
        email = payload.get("contact_email") or ""
        content_type = payload.get("content_type") or "video"
        deadline = payload.get("deadline") or "no deadline set"
        objective = payload.get("objective") or ""
        portal = payload.get("portal_url") or ""
        obj_line = (" Objective: %s." % objective) if objective else ""
        return (
            "New creative brief — %s from %s (%s). "
            "Content: %s. Deadline: %s.%s "
            "Portal: contentco-op.com%s" % (
                contact_name, company, email, content_type, deadline, obj_line, portal
            )
        )

    if etype == "brief_message_from_client":
        brief_id = payload.get("brief_id") or ""
        preview = payload.get("message_preview") or ""
        return (
            "Client message in brief portal (brief %s): \"%s\"" % (
                brief_id[:8], preview[:200]
            )
        )

    return "New event '%s' from %s — check the dashboard." % (etype, name)


# ---------------------------------------------------------------------------
# OpenClaw dispatch
# ---------------------------------------------------------------------------

def send_imessage(phone, message):
    """Send an iMessage/SMS via Mac Mini's Messages app using osascript.
    Falls back gracefully if Messages is not available or phone is not an iMessage contact.
    """
    # Normalize phone to E.164 for osascript
    digits = "".join(c for c in str(phone or "") if c.isdigit())
    if not digits:
        raise ValueError("invalid phone: %s" % phone)
    e164 = "+1" + digits if len(digits) == 10 else ("+" + digits)

    script = (
        'tell application "Messages"\n'
        '  set targetService to 1st service whose service type = iMessage\n'
        '  set targetBuddy to buddy "%s" of targetService\n'
        '  send "%s" to targetBuddy\n'
        'end tell' % (e164, message.replace('"', '\\"').replace('\n', '\\n'))
    )
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        # Try SMS fallback (green bubble)
        script_sms = (
            'tell application "Messages"\n'
            '  set targetService to 1st service whose service type = SMS\n'
            '  set targetBuddy to buddy "%s" of targetService\n'
            '  send "%s" to targetBuddy\n'
            'end tell' % (e164, message.replace('"', '\\"').replace('\n', '\\n'))
        )
        result2 = subprocess.run(
            ["osascript", "-e", script_sms],
            capture_output=True, text=True, timeout=30,
        )
        if result2.returncode != 0:
            raise RuntimeError("osascript failed: %s" % result2.stderr[:200])


def send_email_via_gmail(to, subject, body):
    """Send email via Gmail using service account DWD.
    Uses blaze@contentco-op.com as sender (has gmail.compose scope).
    """
    try:
        import base64 as b64
        from email.mime.text import MIMEText
        # Build raw MIME message
        msg = MIMEText(body, 'plain')
        msg['To'] = to
        msg['From'] = 'Astro Cleaning Services <blaze@contentco-op.com>'
        msg['Subject'] = subject
        raw = b64.urlsafe_b64encode(msg.as_bytes()).decode()

        # Get access token via service account DWD
        sa_path = os.path.expanduser(
            "~/.gemini/antigravity/playground/perihelion-armstrong/service_account.json"
        )
        with open(sa_path) as f:
            sa = json.load(f)

        import time, urllib.request as req
        import json as _json

        # JWT for Gmail compose scope
        now = int(time.time())
        header = b64.urlsafe_b64encode(
            _json.dumps({"alg": "RS256", "typ": "JWT"}).encode()
        ).rstrip(b'=').decode()
        claims = b64.urlsafe_b64encode(_json.dumps({
            "iss": sa["client_email"],
            "scope": "https://www.googleapis.com/auth/gmail.send",
            "aud": "https://oauth2.googleapis.com/token",
            "sub": "blaze@contentco-op.com",
            "iat": now, "exp": now + 3600,
        }).encode()).rstrip(b'=').decode()

        from cryptography.hazmat.primitives import serialization, hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        key = serialization.load_pem_private_key(
            sa["private_key"].encode(), password=None
        )
        sig_input = ("%s.%s" % (header, claims)).encode()
        sig = b64.urlsafe_b64encode(
            key.sign(sig_input, padding.PKCS1v15(), hashes.SHA256())
        ).rstrip(b'=').decode()
        jwt_token = "%s.%s.%s" % (header, claims, sig)

        # Exchange for access token
        token_data = urllib.parse.urlencode({
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": jwt_token,
        }).encode()
        token_req = req.Request(
            "https://oauth2.googleapis.com/token",
            data=token_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with req.urlopen(token_req, timeout=15) as resp:
            access_token = _json.loads(resp.read())["access_token"]

        # Send via Gmail API
        send_req = req.Request(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            data=_json.dumps({"raw": raw}).encode(),
            headers={
                "Authorization": "Bearer " + access_token,
                "Content-Type": "application/json",
            },
        )
        with req.urlopen(send_req, timeout=15) as resp:
            resp.read()  # consume response

    except Exception as exc:
        raise RuntimeError("email send failed: %s" % exc)


def notify_team_group(message):
    """Post a message to @ACS_CC_TEAM via @agentastro_bot (ASTRO_TELEGRAM_BOT_TOKEN)."""
    bot_token = os.environ.get("ASTRO_TELEGRAM_BOT_TOKEN", "")
    if not bot_token:
        return
    data = json.dumps({
        "chat_id": "-1003808234745",  # @ACS_CC_TEAM
        "text": message,
        "disable_web_page_preview": True,
    }).encode()
    req = urllib.request.Request(
        "https://api.telegram.org/bot%s/sendMessage" % bot_token,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception as exc:
        print("[bridge] team group notify failed: %s" % exc, flush=True)


# Events that get a direct group ping in addition to agent dispatch
TEAM_GROUP_EVENTS = {
    "quote_submitted",
    "job_self_scheduled",
    "deposit_paid",
    "reschedule_requested",
    "job_confirmed_by_client",
    "client_request_submitted",
}



# ---------------------------------------------------------------------------
# Client request → Calendar note push
# ---------------------------------------------------------------------------

SA_FILE = "/Users/_mxappservice/.gemini/antigravity/playground/perihelion-armstrong/service_account.json"
CALENDAR_ID = "caio@astrocleanings.com"

def push_note_to_calendar(event):
    """When a client submits a special_attention or general request, append it to their next calendar event."""
    payload = event.get("payload") or {}
    req_type = payload.get("type", "general")
    message = payload.get("message", "")
    contact_id = event.get("contact_id")

    if not message:
        print("[bridge] client_request: no message, skipping calendar push", flush=True)
        return False

    # Only push special_attention and general requests to calendar
    if req_type not in ("special_attention", "general"):
        print("[bridge] client_request: type=%s, not pushing to calendar" % req_type, flush=True)
        return True

    # Look up the contact name
    contact_name = "Unknown"
    if contact_id:
        try:
            url = SUPABASE_URL + "/rest/v1/contacts?select=name&id=eq." + str(contact_id) + "&limit=1"
            req = urllib.request.Request(url, headers={
                "apikey": SUPABASE_KEY,
                "Authorization": "Bearer " + SUPABASE_KEY,
            })
            rows = json.loads(urllib.request.urlopen(req).read())
            if rows:
                contact_name = rows[0].get("name", "Unknown")
        except Exception as exc:
            print("[bridge] client_request: contact lookup failed: %s" % exc, flush=True)

    # Find their next calendar event by searching for the contact name
    try:
        from google.oauth2 import service_account as sa_mod
        from googleapiclient.discovery import build as gcal_build

        creds = sa_mod.Credentials.from_service_account_file(
            SA_FILE, scopes=["https://www.googleapis.com/auth/calendar"]
        )
        creds = creds.with_subject(CALENDAR_ID)
        service = gcal_build("calendar", "v3", credentials=creds)

        now = datetime.now(timezone.utc).isoformat()
        events_result = service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=now,
            maxResults=50,
            singleEvents=True,
            orderBy="startTime",
            q=contact_name.split()[0] if contact_name != "Unknown" else "",
        ).execute()

        items = events_result.get("items", [])
        target_event = None
        for item in items:
            title = item.get("summary", "").lower()
            if contact_name.lower().split()[0] in title:
                target_event = item
                break

        if not target_event:
            print("[bridge] client_request: no upcoming event for %s" % contact_name, flush=True)
            return True

        # Append the note to the event description
        old_desc = target_event.get("description", "")
        timestamp = datetime.now().strftime("%m/%d %H:%M")
        note_section = "\n\nClient Note [%s]:\n%s" % (timestamp, message)

        # Check if note already appended (dedup)
        if message[:50] in old_desc:
            print("[bridge] client_request: note already in event, skipping", flush=True)
            return True

        new_desc = old_desc + note_section

        service.events().patch(
            calendarId=CALENDAR_ID,
            eventId=target_event["id"],
            body={"description": new_desc}
        ).execute()

        print("[bridge] client_request: pushed note to %s event on %s" % (
            contact_name, target_event.get("start", {}).get("dateTime", "?")[:10]
        ), flush=True)
        return True

    except ImportError:
        print("[bridge] client_request: google-auth not available, skipping calendar push", flush=True)
        return True
    except Exception as exc:
        print("[bridge] client_request: calendar push failed: %s" % exc, flush=True)
        return False


def handle_direct_dispatch(event):
    """Handle send_imessage / send_email events directly, no agent involved."""
    etype = event.get("type")
    payload = event.get("payload") or {}

    if etype == "send_imessage":
        phone = payload.get("phone", "")
        message = payload.get("message", "")
        if not phone or not message:
            print("[bridge] send_imessage: missing phone or message", flush=True)
            return False
        send_imessage(phone, message)
        print("[bridge] send_imessage → %s: OK" % phone[:8], flush=True)
        return True

    if etype == "send_email":
        to = payload.get("to", "")
        subject = payload.get("subject", "Astro Cleaning Services")
        body = payload.get("body", "")
        if not to or not body:
            print("[bridge] send_email: missing to or body", flush=True)
            return False
        send_email_via_gmail(to, subject, body)
        print("[bridge] send_email → %s: OK" % to, flush=True)
        return True

    if etype == "client_request_submitted":
        push_note_to_calendar(event)
        return True

    return False


def dispatch_to_agent(agent, message):
    """Dispatch a message to an OpenClaw agent.

    - main: sends via Telegram directly (openclaw message send) so Bailey gets
      an immediate push notification on his phone via @blazenbailey_bot.
    - All other agents: headless CLI session (openclaw agent) for workflow processing.
    """
    env = os.environ.copy()
    env["PATH"] = PATH_ENV + ":" + env.get("PATH", "")

    if agent == "main":
        # Direct Telegram push to Bailey — bypasses the headless session gap
        cmd = [
            OPENCLAW_PATH, "message", "send",
            "--channel", "telegram",
            "--account", "main",
            "--target", "telegram:7747110667",
            "--message", message,
        ]
    else:
        cmd = [
            OPENCLAW_PATH, "agent",
            "--agent", agent,
            "--message", message,
            "--json",
        ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError("openclaw exit %d: %s" % (result.returncode, result.stderr[:200]))
    return result.stdout


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def catchup_mode(conn):
    """Mark all existing events as seen WITHOUT dispatching. Run once to initialize."""
    print("[bridge] CATCHUP: Marking all existing routable events as seen (no dispatch)...", flush=True)
    count = 0
    offset = 0
    while True:
        try:
            events = fetch_recent_events(hours=72, offset=offset, limit=100)
        except Exception as exc:
            print("[bridge] ERROR fetching events: %s" % exc, flush=True)
            break
        if not events:
            break
        for event in events:
            event_id = event.get("id")
            etype = event.get("type")
            if not event_id or not etype:
                continue
            agents = EVENT_AGENT_MAP.get(etype, ["acs-worker"])
            for agent in agents:
                if is_already_routed(conn, event_id, agent):
                    continue
                mark_routed(conn, event_id, agent, etype)
                count += 1
        if len(events) < 100:
            break
        offset += len(events)
    print("[bridge] CATCHUP: Marked %d agent-event pairs as seen. Future runs will only dispatch new events." % count, flush=True)


def main():
    catchup = "--catchup" in sys.argv

    if not SUPABASE_KEY:
        print("[bridge] ERROR: SUPABASE_SERVICE_KEY not set", flush=True)
        sys.exit(1)

    conn = init_db(TRACKING_DB)

    if catchup:
        catchup_mode(conn)
        conn.close()
        return

    print("[bridge] Fetching recent events from Supabase...", flush=True)

    try:
        events = fetch_recent_events(hours=2)
    except Exception as exc:
        print("[bridge] ERROR fetching events: %s" % exc, flush=True)
        conn.close()
        sys.exit(1)

    new_count = 0
    done = False
    for event in events:
        if done:
            break

        event_id = event.get("id")
        etype = event.get("type")

        if not event_id or not etype:
            continue

        # Direct dispatch (iMessage/email) — bypass agent system
        if etype in DIRECT_DISPATCH_EVENTS:
            sentinel = "direct:%s" % event_id
            if is_already_routed(conn, sentinel, "direct"):
                continue
            try:
                handle_direct_dispatch(event)
                mark_routed(conn, sentinel, "direct", etype)
                new_count += 1
            except Exception as exc:
                print("[bridge] ERROR direct dispatch %s: %s" % (etype, exc), flush=True)
            continue

        agents = EVENT_AGENT_MAP.get(etype)
        if not agents:
            continue

        for agent in agents:
            if new_count >= MAX_DISPATCH_PER_RUN:
                print("[bridge] Batch limit reached (%d). Remaining events deferred to next run." % MAX_DISPATCH_PER_RUN, flush=True)
                done = True
                break

            if is_already_routed(conn, event_id, agent):
                continue

            try:
                message = build_message(event)
            except Exception as exc:
                print("[bridge] WARNING: could not build message for %s/%s: %s" % (etype, event_id, exc), flush=True)
                message = "New event '%s' — ID: %s. Check the dashboard." % (etype, event_id)

            print("[bridge] Routing %s → %s: %s..." % (etype, agent, message[:80]), flush=True)

            try:
                dispatch_to_agent(agent, message)
                mark_routed(conn, event_id, agent, etype)
                new_count += 1
                # Also ping @ACS_CC_TEAM for key ACS events (non-blocking)
                if etype in TEAM_GROUP_EVENTS and agent == "acs-worker":
                    notify_team_group(message)
            except Exception as exc:
                print("[bridge] ERROR dispatching %s to %s: %s" % (etype, agent, exc), flush=True)

    print("[bridge] Done. Routed %d new events." % new_count, flush=True)
    conn.close()


if __name__ == "__main__":
    main()
