#!/usr/bin/env python3
"""
contact_brain_activate.py — Phase 1 Contact Brain Activation
Runs: priority scoring, follow-up dates, category inference
Safe to re-run. All updates are incremental (only writes if score changes).

2026-02-22
"""
import sqlite3, os, math, re
from datetime import datetime, date, timedelta

DB_PATH = "/Users/_mxappservice/blaze-data/contacts/contacts.db"
LOG_PATH = "/Users/_mxappservice/blaze-logs/contact-brain-activate.log"
TODAY = date.today().isoformat()
NOW = datetime.now().isoformat()


def open_db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.row_factory = sqlite3.Row
    return conn


# ─────────────────────────────────────────────
# 1A. Priority Scoring Formula
# ─────────────────────────────────────────────

def score_recency(last_contacted):
    """Score 0-40 based on days since last contact."""
    if not last_contacted:
        return 0
    try:
        last = datetime.fromisoformat(last_contacted.replace("Z", "")).date()
        days_ago = (date.today() - last).days
        if days_ago <= 7:    return 40
        if days_ago <= 30:   return 30
        if days_ago <= 90:   return 20
        if days_ago <= 180:  return 10
        if days_ago <= 365:  return 5
        return 1
    except Exception:
        return 0


def score_volume(interaction_count):
    """Score 0-30 based on message volume (log-normalized)."""
    if not interaction_count or interaction_count == 0:
        return 0
    # Log normalize: 100 msgs = 10pts, 1000 = 20pts, 10000 = 30pts
    score = min(30, int(math.log10(max(1, interaction_count)) * 10))
    return score


def score_relationship(category, client_status, how_we_know_them):
    """Score 0-20 based on relationship type and business relevance."""
    score = 0
    cat = (category or "").lower()
    status = (client_status or "").lower()
    how = (how_we_know_them or "").lower()

    # Client status
    if status == "active-client":  score += 20
    elif status == "prospect":     score += 12
    elif status == "lead":         score += 6
    # Don't boost generic 'lead' heavily — it's bulk-tagged

    # Category
    if cat == "business":   score += 5
    elif cat == "mixed":    score += 3
    elif cat == "personal": score += 2
    elif cat == "family":   score += 8  # family matters

    # How we know them signals
    if any(w in how for w in ["client", "customer", "contract"]):  score += 5
    if any(w in how for w in ["referral", "intro", "mutual"]):     score += 4
    if any(w in how for w in ["imessage", "message", "text"]):     score += 2

    return min(20, score)


def score_completeness(email, phone, ai_profile):
    """Score 0-10 based on data completeness — richer = more actionable."""
    score = 0
    if email and email.strip(): score += 5
    if phone and phone.strip(): score += 2
    if ai_profile and len(ai_profile) > 100: score += 3
    return score


def compute_priority(row):
    """Composite score 0-100."""
    s = (
        score_recency(row["last_contacted"])
        + score_volume(row["interaction_count"])
        + score_relationship(row["category"], row["client_status"], row["how_we_know_them"])
        + score_completeness(row["email"], row["phone"], row["ai_profile"])
    )
    return min(100.0, float(s))


# ─────────────────────────────────────────────
# 1B. Follow-Up Date Assignment
# ─────────────────────────────────────────────

def compute_follow_up(row, priority_score):
    """Set follow-up date based on status and recency. Don't overwrite existing."""
    if row["follow_up_due"]:
        return None  # Already set — respect it

    status = (row["client_status"] or "").lower()
    last = row["last_contacted"]

    # Active clients — weekly touch
    if status == "active-client":
        return (date.today() + timedelta(days=7)).isoformat()

    # Hot leads (high priority, recent contact)
    if status in ("lead", "prospect") and priority_score >= 50:
        return (date.today() + timedelta(days=3)).isoformat()

    # Warm contacts going cold (interacted before, priority > 30, silent 30+ days)
    if priority_score >= 30 and last:
        try:
            last_date = datetime.fromisoformat(last.replace("Z", "")).date()
            days_silent = (date.today() - last_date).days
            if days_silent > 30:
                return (date.today() + timedelta(days=7)).isoformat()
        except Exception:
            pass

    return None


# ─────────────────────────────────────────────
# 1C. Category Inference
# ─────────────────────────────────────────────

BUSINESS_SIGNALS = [
    "inc", "llc", "corp", "ltd", "group", "co ", "co.", "company", "consulting",
    "solutions", "services", "agency", "media", "studio", "labs", "ventures",
    "astro cleaning", "content co", "schneider", "wix", "crunch", "montrose"
]
PERSONAL_SIGNALS = ["friend", "personal", "college", "school", "gym", "neighbor"]
FAMILY_SIGNALS = ["mom", "dad", "sister", "brother", "aunt", "uncle", "grandma",
                  "grandpa", "eubanks", "family", "cousin"]
ACS_SIGNALS = ["cleaning", "janitorial", "maintenance", "facility", "property", "commercial"]
CC_SIGNALS = ["video", "production", "content", "media", "creative", "youtube",
              "podcast", "blog", "marketing", "brand"]


def infer_category(row):
    """Infer category if currently unknown."""
    if row["category"] and row["category"].lower() not in ("unknown", ""):
        return None  # Already categorized

    text = " ".join(filter(None, [
        row["company"] or "",
        row["role"] or "",
        row["how_we_know_them"] or "",
        row["tags"] or "",
        row["business_tags"] or "",
        row["notes"] or ""
    ])).lower()

    name_lower = (row["name"] or "").lower()

    # Family first (check full name)
    if any(s in name_lower for s in FAMILY_SIGNALS):
        return "family"

    if any(s in text for s in FAMILY_SIGNALS):
        return "family"

    if any(s in text for s in ACS_SIGNALS):
        return "business"

    if any(s in text for s in CC_SIGNALS):
        return "business"

    if any(s in text for s in BUSINESS_SIGNALS):
        return "business"

    if any(s in text for s in PERSONAL_SIGNALS):
        return "personal"

    # Has a company → business
    if row["company"] and len(row["company"].strip()) > 2:
        return "business"

    # Has interaction data → mixed (we talked to them for a reason)
    if (row["interaction_count"] or 0) > 10:
        return "mixed"

    return None  # Leave unknown if no signal


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def run():
    conn = open_db()
    rows = conn.execute("SELECT * FROM contacts").fetchall()

    scored = 0
    follow_ups_set = 0
    categorized = 0
    updates = []

    for row in rows:
        priority = compute_priority(row)
        follow_up = compute_follow_up(row, priority)
        category = infer_category(row)

        changed = False
        update_fields = []
        update_vals = []

        if abs((row["priority_score"] or 0) - priority) > 0.1:
            update_fields.append("priority_score = ?")
            update_vals.append(round(priority, 2))
            scored += 1
            changed = True

        if follow_up:
            update_fields.append("follow_up_due = ?")
            update_vals.append(follow_up)
            follow_ups_set += 1
            changed = True

        if category:
            update_fields.append("category = ?")
            update_vals.append(category)
            categorized += 1
            changed = True

        if changed:
            update_fields.append("updated_at = ?")
            update_vals.append(NOW)
            update_vals.append(row["id"])
            conn.execute(
                f"UPDATE contacts SET {', '.join(update_fields)} WHERE id = ?",
                update_vals
            )

    conn.commit()

    # Report results
    top25 = conn.execute("""
        SELECT name, company, priority_score, client_status, last_contacted, follow_up_due
        FROM contacts
        ORDER BY priority_score DESC
        LIMIT 25
    """).fetchall()

    follow_ups_due = conn.execute("""
        SELECT name, company, client_status, follow_up_due
        FROM contacts
        WHERE follow_up_due IS NOT NULL
          AND follow_up_due <= date('now', '+30 days')
        ORDER BY follow_up_due
        LIMIT 20
    """).fetchall()

    category_counts = conn.execute("""
        SELECT category, COUNT(*) FROM contacts GROUP BY category ORDER BY COUNT(*) DESC
    """).fetchall()

    conn.close()

    # Write log
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "w") as f:
        f.write(f"Contact Brain Activation — {NOW}\n")
        f.write(f"Scores updated: {scored}\n")
        f.write(f"Follow-ups set: {follow_ups_set}\n")
        f.write(f"Contacts categorized: {categorized}\n\n")

        f.write("=== TOP 25 BY PRIORITY ===\n")
        for r in top25:
            f.write(f"  {r['name']:<30} {r['company'] or '':<25} score={r['priority_score']:.0f} status={r['client_status'] or ''} last={r['last_contacted'] or 'never'[:10]} followup={r['follow_up_due'] or '-'}\n")

        f.write("\n=== FOLLOW-UPS DUE (next 30 days) ===\n")
        for r in follow_ups_due:
            f.write(f"  {r['follow_up_due']} | {r['name']:<30} | {r['client_status'] or ''}\n")

        f.write("\n=== CATEGORY BREAKDOWN ===\n")
        for r in category_counts:
            f.write(f"  {r[0] or 'unknown':<20} {r[1]}\n")

    print(f"✅ Scores updated: {scored}")
    print(f"✅ Follow-ups set: {follow_ups_set}")
    print(f"✅ Categorized: {categorized}")
    print(f"\nTop 5:")
    for r in top25[:5]:
        print(f"  [{r['priority_score']:.0f}] {r['name']} — {r['company'] or 'no company'} | followup={r['follow_up_due'] or 'none'}")
    print(f"\nLog: {LOG_PATH}")


if __name__ == "__main__":
    run()
