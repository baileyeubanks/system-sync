#!/usr/bin/env python3
"""
event_router.py — Intelligence Router for Blaze V4

Scores every inbound event: contact_tier x channel_weight x content_signals
x goal_relevance = priority (0-100)
Returns (score, context, business_unit).

business_unit values:
  "CC"   — Content Co-op (Bailey)
  "ACS"  — Astro Cleanings (Caio)
  "BOTH" — applies to both businesses
"""
import sqlite3

CONTACTS_DB = "/Users/_mxappservice/blaze-data/blaze.db"
KNOWLEDGE_DB = "/Users/_mxappservice/blaze-data/blaze.db"

# Cache goals per process lifetime (refreshed each cron run)
_goals_cache = None

# Email-to-business-unit mapping for Gmail accounts
ACCOUNT_BU_MAP = {
    "bailey@contentco-op.com": "CC",
    "caio@astrocleanings.com": "ACS",
    "blaze@contentco-op.com": "CC",
}


def classify_business_unit(contact, source, sender, subject, body):
    """Determine business_unit from contact data and event content.

    Priority:
      1. Contact's company/tags (most reliable)
      2. Sender email domain
      3. Content keywords
      4. Default to BOTH
    """
    # --- From contact record ---
    if contact:
        company = (contact.get("company") or "").lower()
        tags = (contact.get("business_tags") or "").lower()

        # Explicit ACS match
        if "astro clean" in company or "astro-cleanings" in tags or "acs" in tags:
            return "ACS"
        # Explicit CC match
        if "content co" in company or "content-co-op" in tags or "kallaway" in tags:
            return "CC"

    # --- From sender email domain ---
    sender_lower = (sender or "").lower()
    if "astrocleanings" in sender_lower or "astro-cleanings" in sender_lower:
        return "ACS"
    if "contentco-op" in sender_lower or "content-co-op" in sender_lower:
        return "CC"

    # --- From content keywords ---
    text = ("%s %s" % (subject or "", body or "")).lower()
    acs_words = ["cleaning", "astro", "acs", "janitorial", "pressure wash",
                 "window clean", "carpet clean", "move-out", "move out"]
    cc_words = ["content co-op", "content co op", "kallaway", "youtube",
                "creator", "video edit", "thumbnail", "channel"]

    acs_hits = sum(1 for w in acs_words if w in text)
    cc_hits = sum(1 for w in cc_words if w in text)

    if acs_hits > 0 and cc_hits == 0:
        return "ACS"
    if cc_hits > 0 and acs_hits == 0:
        return "CC"
    if acs_hits > 0 and cc_hits > 0:
        return "BOTH"

    # --- Default ---
    return "BOTH"


def lookup_contact(sender, contacts_db=CONTACTS_DB):
    """Find contact by email, phone, or handle. Falls back to name match."""
    if not sender:
        return None
    try:
        conn = sqlite3.connect(contacts_db)
        conn.row_factory = sqlite3.Row
        # Exact match on email, phone, or handle
        row = conn.execute(
            "SELECT name, enrichment_tier, company, client_status, "
            "business_tags, sent_to, email, phone, handle "
            "FROM contacts WHERE email=? OR phone=? OR handle=? LIMIT 1",
            (sender, sender, sender)
        ).fetchone()
        if row:
            conn.close()
            return dict(row)

        # Fuzzy: try matching by name (for gmail display names)
        row = conn.execute(
            "SELECT name, enrichment_tier, company, client_status, "
            "business_tags, sent_to, email, phone, handle "
            "FROM contacts WHERE name LIKE ? LIMIT 1",
            ("%" + sender + "%",)
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception:
        return None


def get_active_goals(knowledge_db=KNOWLEDGE_DB):
    """Load active goals from knowledge.db. Cached per process."""
    global _goals_cache
    if _goals_cache is not None:
        return _goals_cache
    try:
        conn = sqlite3.connect(knowledge_db)
        rows = conn.execute(
            "SELECT goal FROM goals WHERE status='active'"
        ).fetchall()
        conn.close()
        _goals_cache = [r[0] for r in rows]
        return _goals_cache
    except Exception:
        return []


def score_event(source, sender, subject, body, channel=None, gmail_account=None):
    """
    Score an event 0-100.
    Returns (score, context, business_unit).

    Args:
        source: Event source (gmail, imessage, calendar, market, x_news).
        sender: Sender email, phone, or handle.
        subject: Event subject line.
        body: Event body text.
        channel: Channel override (email, imessage, calendar, etc).
        gmail_account: The Gmail account that received this email
                       (e.g. "bailey@contentco-op.com"). Used to help
                       classify business_unit for email events.
    """
    score = 0
    context = ""

    # Resolve channel from source if not given
    if not channel:
        channel = source

    # --- WHO: contact tier drives base priority ---
    contact = lookup_contact(sender)
    if contact:
        tier = contact.get("enrichment_tier") or 999
        if tier <= 10:
            score += 40    # inner circle
        elif tier <= 25:
            score += 30    # key relationships
        elif tier <= 100:
            score += 20    # active network
        elif tier <= 500:
            score += 10    # known

        # Revenue source: Content Co-op or Astro Cleanings
        company = (contact.get("company") or "").lower()
        tags = (contact.get("business_tags") or "").lower()
        status = (contact.get("client_status") or "").lower()
        if "content co" in company or "astro clean" in company:
            score += 5
        elif "content-co-op" in tags:
            score += 5
        elif status in ("active-client", "active_lead"):
            score += 5

        if contact.get("sent_to"):
            score += 5     # we've engaged before

    # --- WHAT: channel urgency ---
    channel_weight = {
        "imessage": 15,
        "calendar": 20,
        "email": 10,
        "gmail": 10,
        "market": 5,
        "news": 3,
        "x_news": 3,
    }
    score += channel_weight.get(channel, 0)

    # --- CONTENT: what does it say? ---
    text = ("%s %s" % (subject or "", body or "")).lower()

    if any(w in text for w in ["urgent", "asap", "deadline", "today"]):
        score += 15
    if any(w in text for w in ["$", "quote", "invoice", "payment", "price"]):
        score += 12
    if "?" in (body or ""):
        score += 8  # they're asking something

    # --- GOAL RELEVANCE ---
    goals = get_active_goals()
    for goal in goals:
        goal_words = set(goal.lower().split())
        text_words = set(text.split())
        if len(goal_words & text_words) >= 2:
            score += 10
            context = "Connects to goal: %s" % goal
            break

    # --- BUSINESS UNIT CLASSIFICATION ---
    business_unit = classify_business_unit(contact, source, sender, subject, body)

    # Gmail account override: if we know which mailbox received it,
    # that's the strongest signal for routing
    if gmail_account:
        account_bu = ACCOUNT_BU_MAP.get(gmail_account)
        if account_bu:
            business_unit = account_bu

    return min(100, score), context, business_unit


if __name__ == "__main__":
    print("=== Event Router Test ===")
    s, c, bu = score_event(
        "gmail", "bailey@contentco-op.com",
        "Invoice from vendor", "Please pay by Friday $500",
        channel="email"
    )
    print("Gmail CC test    — Score: %d  BU: %s  Context: %s" % (s, bu, c or "none"))

    s2, c2, bu2 = score_event(
        "gmail", "client@example.com",
        "Cleaning quote", "Need a pressure wash quote for office",
        channel="email", gmail_account="caio@astrocleanings.com"
    )
    print("Gmail ACS test   — Score: %d  BU: %s  Context: %s" % (s2, bu2, c2 or "none"))

    s3, c3, bu3 = score_event(
        "imessage", "+15551234567",
        "", "Hey are you free today?",
        channel="imessage"
    )
    print("iMsg test        — Score: %d  BU: %s  Context: %s" % (s3, bu3, c3 or "none"))

    s4, c4, bu4 = score_event(
        "calendar", "calendar",
        "Client meeting with Tyler", "10:00 AM",
        channel="calendar"
    )
    print("Calendar test    — Score: %d  BU: %s  Context: %s" % (s4, bu4, c4 or "none"))

    s5, c5, bu5 = score_event(
        "market", "BTC", "BTC +6.2%", "BTC moved +6.2% to $95,000",
        channel="market"
    )
    print("Market test      — Score: %d  BU: %s  Context: %s" % (s5, bu5, c5 or "none"))
