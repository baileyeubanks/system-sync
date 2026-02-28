#!/usr/bin/env python3
"""
whois_brief.py — Instant pre-meeting brief for any contact.
Usage: python3 whois_brief.py "Tyler Day"
       python3 whois_brief.py "tyler@example.com"
       python3 whois_brief.py "+18325550100"

Output: Formatted contact brief ready for Blaze to surface via Telegram.
2026-02-22
"""
import sqlite3, sys, json
from datetime import datetime, date

DB = "/Users/_mxappservice/blaze-data/contacts/contacts.db"


def days_since(ts):
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(ts.replace("Z", "")).date()
        return (date.today() - d).days
    except Exception:
        return None


def format_days(n):
    if n is None: return "never"
    if n == 0: return "today"
    if n == 1: return "yesterday"
    if n < 7: return f"{n} days ago"
    if n < 30: return f"{n//7}w ago"
    if n < 365: return f"{n//30}mo ago"
    return f"{n//365}y ago"


def search(query):
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    q = f"%{query.strip()}%"

    # Try exact phone first
    results = conn.execute(
        "SELECT * FROM contacts WHERE phone = ? OR phone LIKE ? ORDER BY priority_score DESC LIMIT 5",
        (query.strip(), q)
    ).fetchall()

    # Then email
    if not results:
        results = conn.execute(
            "SELECT * FROM contacts WHERE email = ? OR email LIKE ? ORDER BY priority_score DESC LIMIT 5",
            (query.strip(), q)
        ).fetchall()

    # Then name
    if not results:
        results = conn.execute(
            "SELECT * FROM contacts WHERE name LIKE ? ORDER BY priority_score DESC LIMIT 5",
            (q,)
        ).fetchall()

    # Then company
    if not results:
        results = conn.execute(
            "SELECT * FROM contacts WHERE company LIKE ? ORDER BY priority_score DESC LIMIT 5",
            (q,)
        ).fetchall()

    conn.close()
    return results


def brief(row):
    """Return formatted brief string."""
    lines = []

    # Header
    name = row["name"] or "Unknown"
    company = row["company"] or ""
    role = row["role"] or ""
    header = name
    if role and company:
        header += f" — {role} @ {company}"
    elif company:
        header += f" @ {company}"
    lines.append(f"WHOIS: {header}")
    lines.append("─" * 48)

    # Contact info
    if row["email"]:   lines.append(f"  Email : {row['email']}")
    if row["phone"]:   lines.append(f"  Phone : {row['phone']}")
    if row["linkedin_url"]: lines.append(f"  LinkedIn: {row['linkedin_url']}")

    # Relationship
    lines.append("")
    last = format_days(days_since(row["last_contacted"]))
    first = format_days(days_since(row["first_contacted"]))
    interactions = row["interaction_count"] or 0
    score = row["priority_score"] or 0
    status = row["client_status"] or "unknown"

    lines.append(f"  Status      : {status} [priority {score:.0f}/100]")
    lines.append(f"  First met   : {first}")
    lines.append(f"  Last contact: {last}")
    lines.append(f"  Messages    : {interactions}")
    lines.append(f"  Category    : {row['category'] or 'unknown'}")

    if row["how_we_know_them"]:
        lines.append(f"  How known   : {row['how_we_know_them']}")

    # Follow-up
    if row["follow_up_due"]:
        fu_days = days_since(row["follow_up_due"])
        if fu_days is not None:
            overdue = fu_days > 0
            lines.append(f"  Follow-up   : {row['follow_up_due']} {'⚠ OVERDUE' if overdue else '→ upcoming'}")

    # AI Profile
    profile = row["ai_profile_deep"] or row["ai_profile_enriched"] or row["ai_profile"]
    if profile and len(profile.strip()) > 20:
        lines.append("")
        lines.append("  AI Profile:")
        # Trim to first 300 chars
        short = profile.strip()[:300]
        if len(profile.strip()) > 300:
            short += "..."
        for p_line in short.split("\n"):
            if p_line.strip():
                lines.append(f"    {p_line.strip()}")

    # Notes (first 200 chars)
    if row["notes"] and len(row["notes"].strip()) > 5:
        lines.append("")
        lines.append("  Notes:")
        notes_short = row["notes"].strip()[:200]
        lines.append(f"    {notes_short}{'...' if len(row['notes'].strip()) > 200 else ''}")

    # Tags
    tags = row["business_tags"] or row["tags"]
    if tags:
        lines.append(f"\n  Tags: {tags}")

    lines.append("─" * 48)
    return "\n".join(lines)


def run():
    if len(sys.argv) < 2:
        print("Usage: whois_brief.py <name|email|phone>")
        sys.exit(1)

    query = " ".join(sys.argv[1:])
    results = search(query)

    if not results:
        print(f"No contact found for: {query}")
        sys.exit(0)

    if len(results) == 1:
        print(brief(results[0]))
    else:
        print(f"Found {len(results)} matches for '{query}':\n")
        for i, r in enumerate(results, 1):
            print(f"  {i}. {r['name']} — {r['company'] or 'no company'} [{r['client_status'] or '?'}] score={r['priority_score']:.0f}")
        print("\nShowing top match:\n")
        print(brief(results[0]))


if __name__ == "__main__":
    run()
