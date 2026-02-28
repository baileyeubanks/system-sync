#!/usr/bin/env python3
"""
Contact Brain AI Profile Generator
- Generates AI profiles for all tiered contacts via OpenClaw CLI
- T10: deep live profile (ai_profile_deep) — full research prompt
- T25: enriched profile (ai_profile_enriched) — detailed prompt
- T100: standard profile (ai_profile) — medium prompt
- T500: basic profile (ai_profile) — short prompt
- Skips contacts that already have a profile at their tier level
- Designed to run as background process: nohup python3 ... &
"""
import sys, os, sqlite3, time, json
from datetime import datetime
sys.path.insert(0, os.path.dirname(__file__))
from blaze_helper import ask_blaze, log_cron

DB = "/Users/_mxappservice/blaze-data/blaze.db"

TIER_CONFIG = {
    10: {
        "column": "ai_profile_deep",
        "agent": "research-worker",
        "timeout": 90,
        "prompt": (
            "Build a comprehensive relationship intelligence profile for {name}. "
            "Known info: email={email}, phone={phone}, company={company}, title={title}, "
            "category={category}, client_status={client_status}, "
            "last contact={last_contacted}, interaction_count={interaction_count}, "
            "initiator={initiator}, business_tags={business_tags}. "
            "Research this person online. Return a structured profile with: "
            "1) Professional background and current role "
            "2) Company details and what they do "
            "3) How we know them and relationship context "
            "4) Communication style insights (based on initiator pattern) "
            "5) Business opportunities or collaboration potential "
            "6) Recommended next action to strengthen relationship "
            "7) Key topics to bring up in next conversation "
            "Be specific and actionable. This is a TOP 10 priority contact."
        ),
    },
    25: {
        "column": "ai_profile_enriched",
        "agent": "research-worker",
        "timeout": 75,
        "prompt": (
            "Build an enriched contact profile for {name}. "
            "Known info: email={email}, company={company}, title={title}, "
            "category={category}, client_status={client_status}, "
            "last contact={last_contacted}, interaction_count={interaction_count}. "
            "Return: 1) Who they are professionally "
            "2) Company and role context "
            "3) Relationship summary and how we connect "
            "4) Business relevance "
            "5) Suggested next touchpoint "
            "Keep it concise but useful — 3-5 sentences per section."
        ),
    },
    100: {
        "column": "ai_profile",
        "agent": "research-worker",
        "timeout": 60,
        "prompt": (
            "Write a brief contact profile for {name}. "
            "Known: email={email}, company={company}, category={category}, "
            "client_status={client_status}, last contact={last_contacted}, "
            "interactions={interaction_count}. "
            "Return: who they are, how we know them, and one actionable insight. "
            "3-4 sentences max."
        ),
    },
    500: {
        "column": "ai_profile",
        "agent": "research-worker",
        "timeout": 45,
        "prompt": (
            "One-line contact summary for {name}. "
            "Known: email={email}, company={company}, category={category}. "
            "Return a single sentence: who they are and how we likely know them."
        ),
    },
}


def get_contacts_for_tier(conn, tier):
    """Get contacts needing profiles at this tier level."""
    config = TIER_CONFIG[tier]
    col = config["column"]
    rows = conn.execute(f"""
        SELECT id, name, email, phone, company, title, category,
               client_status, last_contacted, interaction_count,
               initiator, business_tags
        FROM contacts
        WHERE enrichment_tier = ?
          AND ({col} IS NULL OR {col} = '')
        ORDER BY priority_score DESC
    """, (tier,)).fetchall()
    return rows


def generate_profile(contact, tier):
    """Generate AI profile for a single contact."""
    config = TIER_CONFIG[tier]
    fields = {
        "name": contact[1] or "Unknown",
        "email": contact[2] or "none",
        "phone": contact[3] or "none",
        "company": contact[4] or "unknown",
        "title": contact[5] or "unknown",
        "category": contact[6] or "unknown",
        "client_status": contact[7] or "none",
        "last_contacted": contact[8] or "unknown",
        "interaction_count": contact[9] or 0,
        "initiator": contact[10] or "unknown",
        "business_tags": contact[11] or "none",
    }
    prompt = config["prompt"].format(**fields)
    result = ask_blaze(prompt, agent=config["agent"], timeout=config["timeout"])
    if not result:
        return None
    if result.startswith("CLI_ERROR"):
        return None
    if "rate limit" in result.lower():
        return "RATE_LIMITED"
    return result.strip()


def run():
    conn = sqlite3.connect(DB)
    start = datetime.now()
    total_generated = 0
    total_errors = 0

    print(f"=== Contact Brain Profile Generator ===")
    print(f"Started: {start.isoformat()}")
    print()

    for tier in [10, 25, 100, 500]:
        config = TIER_CONFIG[tier]
        col = config["column"]
        contacts = get_contacts_for_tier(conn, tier)

        if not contacts:
            print(f"T{tier}: all profiles already generated, skipping")
            continue

        print(f"T{tier}: {len(contacts)} contacts need {col} profiles")

        for i, contact in enumerate(contacts):
            cid = contact[0]
            name = contact[1] or "Unknown"
            print(f"  T{tier} [{i+1}/{len(contacts)}] {name}...", end=" ", flush=True)

            profile = generate_profile(contact, tier)

            if profile == "RATE_LIMITED":
                print("RATE LIMITED — backing off 120s")
                time.sleep(120)
                # Retry once after backoff
                profile = generate_profile(contact, tier)
                if profile == "RATE_LIMITED":
                    print("  Still rate limited — sleeping 300s")
                    time.sleep(300)
                    profile = generate_profile(contact, tier)

            if profile and profile != "RATE_LIMITED":
                conn.execute(
                    f"UPDATE contacts SET {col} = ?, last_enriched = ? WHERE id = ?",
                    (profile, datetime.utcnow().isoformat(), cid)
                )
                conn.commit()
                total_generated += 1
                # Truncate for log display
                preview = profile[:80].replace('\n', ' ')
                print(f"OK ({len(profile)} chars) — {preview}...")
            else:
                total_errors += 1
                print("FAILED")

            # Rate limit: longer pause between calls to stay under quota
            if config["agent"] == "research-worker":
                time.sleep(10)
            else:
                time.sleep(5)

        print(f"T{tier}: complete")
        print()

    elapsed = (datetime.now() - start).total_seconds()
    print(f"=== Profile Generation Complete ===")
    print(f"Generated: {total_generated}")
    print(f"Errors: {total_errors}")
    print(f"Elapsed: {elapsed:.0f}s ({elapsed/60:.1f}m)")

    log_cron("contact_brain_profiles", "success",
             f"{total_generated} profiles generated, {total_errors} errors, {elapsed:.0f}s")

    conn.close()


if __name__ == "__main__":
    run()
