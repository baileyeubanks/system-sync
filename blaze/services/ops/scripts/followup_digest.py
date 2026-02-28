#!/usr/bin/env python3
"""
followup_digest.py — 9am proactive relationship digest via Telegram.
Shows: overdue follow-ups, contacts due today, any priority alerts.

Runs: 9am daily via LaunchAgent (com.blaze.followup-digest)
2026-02-22
"""
import sqlite3, json, os, urllib.request
from datetime import datetime, date, timedelta
import sys; sys.path.insert(0, os.path.dirname(__file__)); import blaze_telegram as _tg

CONTACTS_DB = "/Users/_mxappservice/blaze-data/contacts/contacts.db"
BLAZE_API   = "http://127.0.0.1:8899"
LOG_PATH    = "/Users/_mxappservice/blaze-logs/followup-digest.log"
NOW = datetime.now().isoformat()
TODAY = date.today().isoformat()


def get_digest_data():
    conn = sqlite3.connect(CONTACTS_DB, timeout=10)
    conn.row_factory = sqlite3.Row

    # Overdue follow-ups (priority >= 50)
    overdue = conn.execute("""
        SELECT name, company, follow_up_due, priority_score, client_status, deal_stage
        FROM contacts
        WHERE follow_up_due < date('now')
          AND priority_score >= 50
        ORDER BY priority_score DESC, follow_up_due
        LIMIT 5
    """).fetchall()

    # Due today (priority >= 40)
    due_today = conn.execute("""
        SELECT name, company, follow_up_due, priority_score, client_status, deal_stage
        FROM contacts
        WHERE follow_up_due = date('now')
          AND priority_score >= 40
        ORDER BY priority_score DESC
        LIMIT 5
    """).fetchall()

    # Open proposals (proposal_sent)
    proposals = conn.execute("""
        SELECT name, company, deal_close_by, priority_score
        FROM contacts
        WHERE deal_stage = 'proposal_sent'
        ORDER BY deal_close_by, priority_score DESC
        LIMIT 3
    """).fetchall()

    # New Wix leads not yet contacted (created in last 48h)
    new_leads = conn.execute("""
        SELECT COUNT(*) FROM contacts
        WHERE source LIKE 'wix%'
          AND deal_stage = 'new_lead'
          AND created_at >= datetime('now', '-48 hours')
    """).fetchone()[0]

    # High-priority contacts not contacted in 21+ days (going cold)
    going_cold = conn.execute("""
        SELECT name, company, last_contacted, priority_score
        FROM contacts
        WHERE priority_score >= 65
          AND last_contacted IS NOT NULL
          AND julianday('now') - julianday(last_contacted) >= 21
          AND (follow_up_due IS NULL OR follow_up_due > date('now'))
        ORDER BY priority_score DESC
        LIMIT 3
    """).fetchall()

    conn.close()
    return overdue, due_today, proposals, new_leads, going_cold


def format_days_since(ts):
    if not ts: return "?"
    try:
        d = datetime.fromisoformat(ts.replace("Z","")).date()
        n = (date.today() - d).days
        return f"{n}d"
    except: return "?"


def build_message(overdue, due_today, proposals, new_leads, going_cold):
    lines = [f"☀️ RELATIONSHIP DIGEST — {date.today().strftime('%b %-d')}"]
    lines.append("─" * 32)

    if overdue:
        lines.append("\n⚠️ OVERDUE:")
        for r in overdue:
            co = f" @ {r['company']}" if r['company'] else ""
            lines.append(f"  → {r['name']}{co} [{r['follow_up_due']}]")

    if due_today:
        lines.append("\n📋 DUE TODAY:")
        for r in due_today:
            co = f" @ {r['company']}" if r['company'] else ""
            deal = f" [{r['deal_stage'].upper()}]" if r['deal_stage'] else ""
            lines.append(f"  → {r['name']}{co}{deal}")

    if proposals:
        lines.append("\n📤 OPEN PROPOSALS:")
        for r in proposals:
            co = f" @ {r['company']}" if r['company'] else ""
            close = f" → close {r['deal_close_by']}" if r['deal_close_by'] else ""
            lines.append(f"  → {r['name']}{co}{close}")

    if going_cold:
        lines.append("\n🥶 GOING COLD:")
        for r in going_cold:
            co = f" @ {r['company']}" if r['company'] else ""
            since = format_days_since(r['last_contacted'])
            lines.append(f"  → {r['name']}{co} [{since} silent]")

    if new_leads:
        lines.append(f"\n📥 {new_leads} new Wix leads from last 48h — run WHOIS")

    if not any([overdue, due_today, proposals, going_cold, new_leads]):
        lines.append("\n✅ All clear. No follow-ups needed today.")

    return "\n".join(lines)


def send(message):
    try:
        payload = json.dumps({
            "message": message,
            "channel": "telegram",
            "priority": "normal"
        }).encode()
        req = urllib.request.Request(
            f"{BLAZE_API}/api/notify",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status == 200
    except Exception as e:
        print(f"Send failed: {e}")
        return False


def run():
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    overdue, due_today, proposals, new_leads, going_cold = get_digest_data()
    message = build_message(overdue, due_today, proposals, new_leads, going_cold)

    print(message)
    success = _tg.send(message)
    status = "SENT" if success else "FAILED"
    print(f"\n[{status}]")

    with open(LOG_PATH, "a") as f:
        f.write(f"\n[{NOW}] [{status}] Digest sent\n")
        f.write(f"  overdue={len(overdue)} today={len(due_today)} proposals={len(proposals)} "
                f"cold={len(going_cold)} wix_leads={new_leads}\n")


if __name__ == "__main__":
    run()
