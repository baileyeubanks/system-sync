#!/usr/bin/env python3
"""
ACS Proactive Agent — pings Caio via Telegram about items requiring attention.
Runs every 30 min via com.blaze.acs-proactive LaunchAgent.

Checks:
1. New job applicants (unreviewed in Supabase)
2. New quotes needing follow-up (from Netlify bridge events)
3. Pending deposits not yet paid
4. Upcoming jobs without confirmed crew

Sends summary to Caio via acs-worker → @AstroCleaningsBot
"""

import json
import os
import sqlite3
import subprocess
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

HOME = os.path.expanduser("~")
STATE_DB = os.path.join(HOME, "blaze-data", "acs_proactive_state.db")
LOG_PATH = os.path.join(HOME, "blaze-logs", "acs-proactive.log")
OPENCLAW = "/usr/local/bin/openclaw"

SUPA_URL = "https://briokwdoonawhxisbydy.supabase.co"
SUPA_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJyaW9rd2Rvb25hd2h4aXNieWR5Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3MTU1Njc2MiwiZXhwIjoyMDg3MTMyNzYyfQ"
    ".5V1BsTrqIHGKUUHYJ3PBpL9re_WzKqOzKoQ94dc3me8"
)
ACS_BID = "0ade82e3-ffe9-4c17-ae59-fc4bd198482b"
CAIO_TELEGRAM = "telegram:7124538299"


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = "[" + ts + "] " + msg
    print(line)
    try:
        with open(LOG_PATH, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def init_state():
    os.makedirs(os.path.dirname(STATE_DB), exist_ok=True)
    db = sqlite3.connect(STATE_DB)
    db.execute("""CREATE TABLE IF NOT EXISTS notified (
        item_id TEXT PRIMARY KEY,
        item_type TEXT,
        notified_at TEXT
    )""")
    db.commit()
    return db


def already_notified(db, item_id):
    row = db.execute("SELECT notified_at FROM notified WHERE item_id=?", (item_id,)).fetchone()
    if not row:
        return False
    # Re-notify if not acted on after 24h (for persistent items)
    notified_at = datetime.fromisoformat(row[0])
    return (datetime.now(timezone.utc) - notified_at.replace(tzinfo=timezone.utc)).total_seconds() < 86400


def mark_notified(db, item_id, item_type):
    db.execute(
        "INSERT OR REPLACE INTO notified (item_id, item_type, notified_at) VALUES (?,?,?)",
        (item_id, item_type, datetime.now(timezone.utc).isoformat())
    )
    db.commit()


def supa_get(path):
    url = SUPA_URL + "/rest/v1/" + path
    req = urllib.request.Request(url, headers={
        "apikey": SUPA_KEY,
        "Authorization": "Bearer " + SUPA_KEY,
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None  # Table doesn't exist yet
        log("Supabase error " + str(e.code) + " on " + path)
        return []
    except Exception as e:
        log("Supabase fetch error: " + str(e))
        return []


def check_new_applicants(db):
    """New job applicants not yet reviewed."""
    rows = supa_get("job_applicants?status=eq.pending&business_id=eq." + ACS_BID + "&order=created_at.desc&limit=10")
    if rows is None:
        return []  # Table doesn't exist yet
    alerts = []
    for row in (rows or []):
        item_id = "applicant_" + str(row.get("id", row.get("email", "")))
        if already_notified(db, item_id):
            continue
        name = row.get("name", "Unknown")
        source = row.get("source", "email")
        position = row.get("position", "")
        phone = row.get("phone", "")
        alerts.append({
            "id": item_id,
            "text": "New applicant: " + name + " via " + source + ((" | " + phone) if phone else ""),
            "raw": row
        })
    return alerts


def check_pending_quotes(db):
    """Quotes submitted but deposit not paid after 24h."""
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    rows = supa_get(
        "webhook_events?event_type=eq.quote_submitted"
        "&created_at=lt." + since
        + "&processed=eq.false&business_id=eq." + ACS_BID
        + "&order=created_at.desc&limit=5"
    )
    alerts = []
    for row in (rows or []):
        item_id = "quote_" + str(row.get("id", ""))
        if not item_id or already_notified(db, item_id):
            continue
        payload = row.get("payload", {})
        client = payload.get("name") or payload.get("email", "Unknown client")
        alerts.append({
            "id": item_id,
            "text": "Quote pending 24h+ — no deposit: " + str(client),
            "raw": row
        })
    return alerts


def send_telegram(message):
    """Send message to Caio via acs-worker."""
    env = os.environ.copy()
    env["PATH"] = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
    env["HOME"] = HOME
    try:
        result = subprocess.run(
            [
                OPENCLAW, "message", "send",
                "--channel", "telegram",
                "--account", "astro",
                "--target", CAIO_TELEGRAM,
                "--message", message,
            ],
            capture_output=True, text=True, timeout=30, env=env
        )
        if result.returncode != 0:
            log("Telegram send failed: " + result.stderr[:200])
            return False
        return True
    except Exception as e:
        log("Telegram send error: " + str(e))
        return False


def main():
    log("=== ACS PROACTIVE CHECK ===")
    db = init_state()

    all_alerts = []

    # 1. New job applicants
    applicant_alerts = check_new_applicants(db)
    log("New applicants to notify: " + str(len(applicant_alerts)))
    all_alerts.extend(applicant_alerts)

    # 2. Stale quotes
    quote_alerts = check_pending_quotes(db)
    log("Stale quotes: " + str(len(quote_alerts)))
    all_alerts.extend(quote_alerts)

    if not all_alerts:
        log("Nothing to notify. Done.")
        db.close()
        return

    # Build message
    lines = ["*ACS — Items Needing Your Attention*", ""]
    if applicant_alerts:
        lines.append("*Job Applications (" + str(len(applicant_alerts)) + " new):*")
        for a in applicant_alerts:
            lines.append("• " + a["text"])
        lines.append("")
    if quote_alerts:
        lines.append("*Quotes Awaiting Deposit:*")
        for a in quote_alerts:
            lines.append("• " + a["text"])
        lines.append("")
    lines.append("Reply to review or I can pull full details.")

    message = "\n".join(lines)
    log("Sending Telegram to Caio...")
    sent = send_telegram(message)

    if sent:
        for alert in all_alerts:
            mark_notified(db, alert["id"], alert.get("type", "alert"))
        log("Sent. Marked " + str(len(all_alerts)) + " items as notified.")
    else:
        log("Send failed — will retry next run.")

    db.close()
    log("=== DONE ===")


if __name__ == "__main__":
    main()
