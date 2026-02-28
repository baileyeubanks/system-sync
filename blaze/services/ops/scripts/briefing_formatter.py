"""
briefing_formatter.py
Formats the morning briefing for Telegram.
Design principle: editorial, not terminal. Clean lines, clear hierarchy.
Telegram parse_mode: HTML (more reliable than MarkdownV2)
"""

from datetime import datetime

def format_briefing(data):
    """
    data keys: date, calendar, email_cc, email_acs, imessage,
               markets, goals_short, goals_long, overnight, alerts
    Returns HTML-formatted string for Telegram sendMessage.
    """

    d = datetime.now()
    day = d.strftime("%A").upper()
    date = d.strftime("%B %-d, %Y")

    lines = []

    # -- HEADER --
    lines += [
        "<b>BLAZE  ·  %s</b>" % day,
        "<i>%s</i>" % date,
        "─────────────────────────",
        "",
    ]

    # -- ALERTS (if any urgent items) --
    alerts = data.get("alerts", [])
    if alerts:
        lines.append("<b>⚑  NEEDS ATTENTION</b>")
        for a in alerts:
            lines.append("  %s" % a)
        lines += ["", "─────────────────────────", ""]

    # -- TODAY --
    events = data.get("calendar", [])
    lines.append("<b>TODAY</b>")
    if events:
        for e in events:
            t = e.get("time", "")
            title = e.get("title", "")
            lines.append("  %-9s%s" % (t, title))
    else:
        lines.append("  No events scheduled")
    lines.append("")

    # -- EMAIL --
    cc_emails = data.get("email_cc", [])
    acs_emails = data.get("email_acs", [])
    lines.append("<b>EMAIL</b>")

    if cc_emails:
        for e in cc_emails[:4]:
            sender = e.get("from", "")[:20]
            subject = e.get("subject", "")[:38]
            lines.append("  %-20s  %s" % (sender, subject))

    if acs_emails:
        lines.append("  <i>— ACS —</i>")
        for e in acs_emails[:2]:
            sender = e.get("from", "")[:20]
            subject = e.get("subject", "")[:38]
            lines.append("  %-20s  %s" % (sender, subject))

    if not cc_emails and not acs_emails:
        lines.append("  Clear")
    lines.append("")

    # -- MESSAGES --
    messages = data.get("imessage", [])
    lines.append("<b>MESSAGES</b>")
    if messages:
        for m in messages[:5]:
            name = m.get("name", "")[:22]
            preview = m.get("preview", "")[:35]
            lines.append("  %-22s  %s" % (name, preview))
    else:
        lines.append("  Clear")
    lines.append("")

    # -- MARKETS --
    tickers = data.get("markets", [])
    lines.append("<b>MARKETS</b>")
    if tickers:
        alerts_only = [t for t in tickers if abs(float(t.get("change_pct", 0))) >= 3]
        movers = [t for t in tickers if abs(float(t.get("change_pct", 0))) < 3]

        if alerts_only:
            for t in alerts_only:
                pct = float(t.get("change_pct", 0))
                arrow = "▲" if pct > 0 else "▼"
                lines.append("  <b>%-6s</b>  %s %.2f%%  ← MOVE" % (t["ticker"], arrow, abs(pct)))
        for t in movers[:6]:
            pct = float(t.get("change_pct", 0))
            arrow = "▲" if pct > 0 else "▼"
            lines.append("  %-6s  %s %.2f%%" % (t["ticker"], arrow, abs(pct)))
    else:
        lines.append("  Unavailable")
    lines.append("")

    # -- GOALS --
    short = data.get("goals_short", [])
    long_ = data.get("goals_long", [])
    lines.append("<b>IN MOTION</b>")
    for g in short[:3]:
        lines.append("  → %s" % g)
    if long_:
        lines.append("  <i>Long term:</i>")
        for g in long_[:2]:
            lines.append("  ↗ %s" % g)
    lines.append("")

    # -- OVERNIGHT --
    overnight = data.get("overnight", [])
    lines.append("<b>OVERNIGHT</b>")
    if overnight:
        for item in overnight[:5]:
            status = item.get("status", "")
            name = item.get("name", "")[:28]
            note = item.get("note", "")[:30]
            icon = "✓" if status == "ok" else "✗"
            lines.append("  %s  %-28s  %s" % (icon, name, note))
    else:
        lines.append("  No logs")
    lines.append("")

    # -- FOOTER --
    lines += [
        "─────────────────────────",
        "<i>What's the one thing today?</i>",
    ]

    return "\n".join(lines)


# -- SAMPLE RENDER (for testing layout) --

SAMPLE_DATA = {
    "alerts": [
        "Tyler Day — Nashville quote, respond today",
        "CITGO Early Payment Program — review thread",
    ],
    "calendar": [
        {"time": "8:00 AM", "title": "Calendar Audit"},
        {"time": "8:30 AM", "title": "Gym"},
        {"time": "10:00 AM", "title": "Deep Work Block 1"},
        {"time": "1:00 PM", "title": "Deep Work Block 2"},
        {"time": "3:00 PM", "title": "Admin / Follow-ups"},
    ],
    "email_cc": [
        {"from": "Jamie Guzman", "subject": "Re: Vehicle Inspection Tutorial"},
        {"from": "Tyler Day", "subject": "Nashville Kickoff — follow up"},
        {"from": "Schneider Electric", "subject": "CRA Prep — schedule confirm"},
        {"from": "National Fencing", "subject": "NEW CLIENT inquiry"},
    ],
    "email_acs": [
        {"from": "Caio", "subject": "Schedule update for Monday"},
    ],
    "imessage": [
        {"name": "Caio Gustin", "preview": "I will fix when I get back"},
        {"name": "Eric Bission", "preview": "Laughed at an image"},
        {"name": "Tyler Day", "preview": "Let me know on the Nashville quote"},
    ],
    "markets": [
        {"ticker": "KRKNF", "change_pct": "7.73"},
        {"ticker": "GSIT",  "change_pct": "6.09"},
        {"ticker": "BBAI",  "change_pct": "-0.48"},
        {"ticker": "GME",   "change_pct": "-0.02"},
        {"ticker": "INTC",  "change_pct": "-0.25"},
        {"ticker": "NVDA",  "change_pct": "1.20"},
    ],
    "goals_short": [
        "Close Nashville (Tyler Day)",
        "Schneider CRA prep",
        "Buzz Houston ad campaign",
    ],
    "goals_long": [
        "ACS website + marketing automation",
        "Content Co-op client pipeline",
    ],
    "overnight": [
        {"status": "ok",   "name": "gmail_contact_sync",     "note": "91 contacts merged"},
        {"status": "ok",   "name": "contact_brain_profiles",  "note": "412 done, 53 errors"},
        {"status": "ok",   "name": "business_council",        "note": "Tyler Day = priority 1"},
        {"status": "ok",   "name": "database_backup",         "note": "Complete"},
    ],
}

if __name__ == "__main__":
    result = format_briefing(SAMPLE_DATA)
    print(result)
    print()
    print("Character count: %d (Telegram limit: 4096)" % len(result))
