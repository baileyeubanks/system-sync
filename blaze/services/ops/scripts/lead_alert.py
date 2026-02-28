#!/usr/bin/env python3
"""
lead_alert.py — Checks event_log for unalerted new Wix leads,
sends Telegram alert to Bailey via OpenClaw API.

Runs: On each wix_pipeline.py execution (or every 10 min via cron)
2026-02-22
"""
import sqlite3, json, os, urllib.request
from datetime import datetime
import sys; sys.path.insert(0, os.path.dirname(__file__)); import blaze_telegram as _tg

EVENT_LOG   = "/Users/_mxappservice/blaze-data/event_log.db"
CONTACTS_DB = "/Users/_mxappservice/blaze-data/contacts/contacts.db"
BLAZE_API   = "http://127.0.0.1:8899"
LOG_PATH    = "/Users/_mxappservice/blaze-logs/lead-alert.log"
NOW = datetime.now().isoformat()


def get_new_leads():
    """Pull events from event_log that haven't been alerted yet."""
    try:
        conn = sqlite3.connect(EVENT_LOG, timeout=5)
        conn.row_factory = sqlite3.Row

        # Add alerted column if not exists
        try:
            conn.execute("ALTER TABLE events ADD COLUMN alerted INTEGER DEFAULT 0")
            conn.commit()
        except Exception:
            pass

        leads = conn.execute("""
            SELECT e.*, c.phone, c.email, c.company, c.notes
            FROM events e
            LEFT JOIN contacts c ON e.entity_id = c.id
            WHERE e.source = 'wix-pipeline'
              AND (e.alerted IS NULL OR e.alerted = 0)
            ORDER BY e.created_at DESC
        """).fetchall()

        conn.close()
        return leads
    except Exception as ex:
        print(f"event_log error: {ex}")
        return []


def get_contact_detail(contact_id):
    """Get full contact info for the alert."""
    try:
        conn = sqlite3.connect(CONTACTS_DB, timeout=5)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
        conn.close()
        return row
    except Exception:
        return None


def format_alert(event, contact):
    """Build the Telegram alert message."""
    event_type = event["event_type"]
    name = (contact["name"] if contact else None) or event["entity_name"] or "Unknown Lead"
    company = (contact and contact["company"]) or ""
    phone = (contact and contact["phone"]) or ""
    email = (contact and contact["email"]) or ""

    # Extract key info from notes
    notes = (contact and contact["notes"]) or ""
    service_info = ""
    for line in notes.split("\n"):
        if "Service:" in line or "Sqft:" in line or "Estimate:" in line:
            service_info = line.strip()[:100]
            break

    label = {
        "astro_quote_lead": "🔥 ACS QUOTE LEAD",
        "astro_whiteglove_lead": "💎 ACS WHITE GLOVE LEAD",
        "booking_confirmed": "✅ BOOKING CONFIRMED",
        "invoice_paid": "💰 INVOICE PAID",
        "form_submitted": "📥 NEW WIX LEAD",
    }.get(event_type, "📥 NEW WIX LEAD")

    parts = [f"{label}: *{name}*"]
    if company:  parts.append(f"Company: {company}")
    if phone:    parts.append(f"Phone: {phone}")
    if email:    parts.append(f"Email: {email}")
    if service_info: parts.append(service_info)
    parts.append("Reply Y to follow up now or N to skip")

    return "\n".join(parts)


def _tg.send(message):
    """Send message via Blaze API → Telegram."""
    try:
        payload = json.dumps({
            "message": message,
            "channel": "telegram",
            "priority": "high"
        }).encode()
        req = urllib.request.Request(
            f"{BLAZE_API}/api/notify",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status == 200
    except Exception as e:
        print(f"API send failed: {e}")
        return False


def mark_alerted(event_ids):
    """Mark events as alerted in event_log."""
    if not event_ids:
        return
    conn = sqlite3.connect(EVENT_LOG, timeout=5)
    conn.executemany(
        "UPDATE events SET alerted = 1 WHERE id = ?",
        [(eid,) for eid in event_ids]
    )
    conn.commit()
    conn.close()


def run():
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    leads = get_new_leads()

    if not leads:
        return  # Nothing to do

    alerted_ids = []
    for ev in leads:
        contact = get_contact_detail(ev["entity_id"]) if ev["entity_id"] else None
        message = format_alert(ev, contact)
        success = _tg._tg.send(message)
        status = "SENT" if success else "FAILED"
        print(f"[{status}] {ev['event_type']}: {ev['entity_name']}")

        with open(LOG_PATH, "a") as f:
            f.write(f"{NOW} [{status}] {ev['event_type']}: {ev['entity_name']}\n")

        if success:
            alerted_ids.append(ev["id"])

    mark_alerted(alerted_ids)
    print(f"Alerted: {len(alerted_ids)}/{len(leads)} leads")


if __name__ == "__main__":
    run()
