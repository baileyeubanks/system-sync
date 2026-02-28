#!/usr/bin/env python3
"""
Direct iMessage reader — queries chat.db directly, no Antigravity needed.
Blaze has full access since Messages runs as _mxappservice.

Fixes applied (Feb 20 2026):
- Fixed JOIN fan-out in group chats (was producing duplicate rows per participant)
- Added associated_message_type=0 filter (excludes tapbacks/reactions)
- Fixed short code boundary (< 8 digits, not < 7)
- Replaced f-strings for Python 3.9 compat
"""
import sqlite3, os
from datetime import datetime, timedelta

CHAT_DB = "/Users/_mxappservice/Library/Messages/chat.db"

NOISE_PATTERNS = [
    "fidelity investments", "card#", "acct#",
    "verification code", "security code", "one-time",
    "your code is", "your pin is",
    "delivery", "your order", "shipped",
    "appointment reminder", "scheduled for",
]

def is_noise(handle, text):
    """Filter short codes, URLs, and automated messages."""
    # Short codes (all digits, less than 8 chars — covers 5-7 digit codes)
    if handle.isdigit() and len(handle) < 8:
        return True
    # URL-only messages
    stripped = text.strip()
    if stripped.startswith("http") and " " not in stripped:
        return True
    # Automated message patterns
    text_lower = text.lower()
    for pattern in NOISE_PATTERNS:
        if pattern in text_lower:
            return True
    return False

def get_recent_messages(hours=24, limit=20):
    """Get recent incoming messages from real contacts."""
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % CHAT_DB, uri=True)
        conn.row_factory = sqlite3.Row

        cutoff = datetime.now() - timedelta(hours=hours)
        # Convert to Apple epoch (seconds since 2001-01-01)
        apple_cutoff = (cutoff.timestamp() - 978307200) * 1000000000

        # Use message.handle_id to join directly to handle table.
        # This avoids the chat_handle_join fan-out that produced
        # duplicate rows in group chats (one per participant).
        # Also filter associated_message_type=0 to exclude tapbacks/reactions.
        rows = conn.execute("""
            SELECT
                h.id as handle,
                m.text,
                datetime(m.date/1000000000 + 978307200, 'unixepoch', 'localtime') as sent_at,
                m.is_from_me,
                c.display_name as chat_name
            FROM message m
            JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
            JOIN chat c ON cmj.chat_id = c.ROWID
            JOIN handle h ON m.handle_id = h.ROWID
            WHERE m.text IS NOT NULL
            AND m.is_from_me = 0
            AND m.date > ?
            AND length(m.text) > 2
            AND m.associated_message_type = 0
            ORDER BY m.date DESC
            LIMIT ?
        """, (apple_cutoff, limit * 3)).fetchall()

        conn.close()
        # Filter noise, then trim to limit
        results = []
        for r in rows:
            d = dict(r)
            if not is_noise(d["handle"], d["text"]):
                results.append(d)
            if len(results) >= limit:
                break
        return results
    except Exception as e:
        return []

def get_unread_summary(hours=24):
    """Return formatted summary for morning briefing."""
    msgs = get_recent_messages(hours=hours, limit=10)
    if not msgs:
        return "No new messages."

    # Deduplicate by handle — show most recent per person
    seen = {}
    for m in msgs:
        h = m["handle"]
        if h not in seen:
            seen[h] = m

    lines = []
    for handle, m in list(seen.items())[:5]:
        # Resolve name from contacts.db if possible
        name = resolve_name(handle) or handle
        preview = (m["text"] or "")[:60].replace("\n", " ")
        if len(m["text"] or "") > 60:
            preview += "..."
        lines.append("  -> %s — %s" % (name, preview))

    return "\n".join(lines)

def resolve_name(handle):
    """Try to find name in contacts.db."""
    try:
        CONTACTS_DB = "/Users/_mxappservice/blaze-data/blaze.db"
        conn = sqlite3.connect(CONTACTS_DB)
        # Match by phone or email
        row = conn.execute(
            "SELECT name FROM contacts WHERE handle=? OR phone=? OR email=? LIMIT 1",
            (handle, handle, handle)
        ).fetchone()
        conn.close()
        return row[0] if row else None
    except:
        return None

def get_stats():
    """Quick stats for overnight report."""
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % CHAT_DB, uri=True)
        cutoff = (datetime.now() - timedelta(hours=24)).timestamp()
        apple_cutoff = (cutoff - 978307200) * 1000000000
        # Exclude tapbacks from count too
        count = conn.execute(
            "SELECT COUNT(*) FROM message WHERE date > ? AND is_from_me=0 AND text IS NOT NULL AND associated_message_type=0",
            (apple_cutoff,)
        ).fetchone()[0]
        conn.close()
        return "%d messages received (24hr)" % count
    except:
        return "iMessage stats unavailable"

if __name__ == "__main__":
    print("=== iMessage Test ===")
    print(get_unread_summary(hours=48))
    print("\n=== Stats ===")
    print(get_stats())
