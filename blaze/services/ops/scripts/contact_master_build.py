#!/usr/bin/env python3
"""
Blaze V4 — Master Contact Build
Merges local + Supabase, deduplicates, normalizes, AI-enriches, scores.
Outputs the top 1,500 contacts — clean, orchestrated, beautiful.
"""
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
import urllib.error
from collections import defaultdict

# ── Config ──────────────────────────────────────────────────────────────────
SUPABASE_URL = "https://briokwdoonawhxisbydy.supabase.co"
SUPABASE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJyaW9rd2Rvb25hd2h4aXNieWR5Iiwicm9sZ"
    "SI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3MTU1Njc2MiwiZXhwIjoyMDg3MTMyNzYyfQ."
    "5V1BsTrqIHGKUUHYJ3PBpL9re_WzKqOzKoQ94dc3me8"
)
import os as _os
from pathlib import Path as _Path

def _load_anthropic_key():
    env_file = _Path.home() / ".blaze_env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                return line.split("=", 1)[1].strip()
    return _os.environ.get("ANTHROPIC_API_KEY", "")

ANTHROPIC_KEY = _load_anthropic_key()
LOCAL_DB = "/Users/_mxappservice/blaze-data/contacts/contacts.db"

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": "Bearer " + SUPABASE_KEY,
    "Content-Type": "application/json",
    "Prefer": "return=minimal",
}
HEADERS_UPSERT = {
    "apikey": SUPABASE_KEY,
    "Authorization": "Bearer " + SUPABASE_KEY,
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates,return=minimal",
}


# ── Helpers ─────────────────────────────────────────────────────────────────

def normalize_phone(p):
    if not p:
        return None
    digits = re.sub(r"\D", "", str(p))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return digits
    return None


def format_phone(digits):
    """Format 10-digit phone as +1XXXXXXXXXX."""
    if not digits or len(digits) != 10:
        return None
    return "+1" + digits


def normalize_name(name):
    """Clean and title-case a name."""
    if not name:
        return None
    # Remove email-as-name patterns
    if "@" in name:
        return None
    # Remove phone-as-name patterns
    if re.match(r"^[\d\s\-\+\(\)]+$", name.strip()):
        return None
    # Clean up
    name = name.strip()
    # Title case, but preserve common patterns
    parts = name.split()
    cleaned = []
    for part in parts:
        low = part.lower()
        # Skip noise words that aren't names
        if low in ("none", "null", "n/a", "na", "unknown", "test"):
            return None
        # Preserve capitalization if already mixed case
        if part != part.upper() and part != part.lower():
            cleaned.append(part)
        else:
            cleaned.append(part.capitalize())
    result = " ".join(cleaned)
    return result if len(result) > 1 else None


def normalize_email(e):
    if not e:
        return None
    e = e.strip().lower()
    if "@" not in e or "." not in e:
        return None
    # Filter out obviously fake/system emails
    if any(x in e for x in ["noreply", "no-reply", "donotreply", "mailer-daemon"]):
        return None
    return e


def supabase_get(path, params=""):
    all_rows = []
    offset = 0
    limit = 1000
    while True:
        sep = "&" if "?" in path else "?"
        url = (SUPABASE_URL + "/rest/v1/" + path + sep +
               "limit=" + str(limit) + "&offset=" + str(offset))
        if params:
            url += "&" + params
        req = urllib.request.Request(url, headers={
            "apikey": SUPABASE_KEY,
            "Authorization": "Bearer " + SUPABASE_KEY,
        })
        resp = urllib.request.urlopen(req)
        rows = json.loads(resp.read())
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < limit:
            break
        offset += limit
    return all_rows


def supabase_patch(table, row_id, data):
    url = SUPABASE_URL + "/rest/v1/" + table + "?id=eq." + str(row_id)
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, headers=HEADERS, method="PATCH")
    try:
        urllib.request.urlopen(req)
        return True
    except urllib.error.HTTPError as e:
        err = e.read().decode()[:200]
        print("  PATCH error {}: {}".format(e.code, err))
        return False


def supabase_batch_patch(table, updates, label=""):
    """Patch a list of (id, data) tuples with progress."""
    ok = 0
    fail = 0
    total = len(updates)
    for i, (row_id, data) in enumerate(updates):
        if supabase_patch(table, row_id, data):
            ok += 1
        else:
            fail += 1
        if (i + 1) % 100 == 0:
            print("    {} {}/{} (ok={}, fail={})".format(label, i + 1, total, ok, fail))
    print("    {} DONE: {}/{} updated ({} failed)".format(label, ok, total, fail))
    return ok


def claude_enrich(name, phone, email, company, tags, context_lines):
    """Generate AI summary for a contact."""
    ctx = "\n".join(context_lines) if context_lines else "Minimal data available."
    prompt = (
        "Write a 1-2 sentence CRM summary for this contact. "
        "State their likely relationship type (client, lead, vendor, personal, "
        "colleague, service provider) and any key facts. Be concise.\n\n"
        "Name: {}\n{}".format(name or "Unknown", ctx)
    )
    body = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 120,
        "messages": [{"role": "user", "content": prompt}],
        "system": (
            "You write 1-2 sentence contact summaries for a CRM. "
            "Businesses: Astro Cleaning Services (ACS, Houston commercial cleaning) "
            "and Content Co-op (B2B video production). Direct and factual only."
        ),
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        data = json.loads(resp.read())
        return data["content"][0]["text"].strip()
    except Exception as e:
        print("    Claude error: {}".format(e))
        return None


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "report"

    print("=" * 70)
    print("  BLAZE MASTER CONTACT BUILD — Top 1,500")
    print("=" * 70)

    # ── Load everything ─────────────────────────────────────────────────
    print("\n[1/7] Loading local SQLite...")
    conn = sqlite3.connect(LOCAL_DB)
    conn.row_factory = sqlite3.Row
    local_rows = [dict(r) for r in conn.execute("SELECT * FROM contacts").fetchall()]
    conn.close()
    print("  {} local contacts".format(len(local_rows)))

    print("\n[2/7] Loading Supabase contacts...")
    sb_contacts = supabase_get("contacts?select=*")
    print("  {} Supabase contacts".format(len(sb_contacts)))

    print("\n[3/7] Loading Supabase jobs + interactions...")
    jobs = supabase_get("jobs?select=client_profile_id,contact_id,status")
    interactions = supabase_get("interactions?select=contact_id,type")
    quotes = supabase_get("quotes?select=contact_id,status,estimated_total")

    # Build lookup maps
    job_counts = defaultdict(int)
    for j in jobs:
        for key in ("client_profile_id", "contact_id"):
            cid = j.get(key)
            if cid:
                job_counts[cid] += 1

    int_counts = defaultdict(int)
    for i in interactions:
        cid = i.get("contact_id")
        if cid:
            int_counts[cid] += 1

    quote_data = defaultdict(lambda: {"count": 0, "total": 0})
    for q in quotes:
        cid = q.get("contact_id")
        if cid:
            quote_data[cid]["count"] += 1
            quote_data[cid]["total"] += (q.get("estimated_total") or 0)

    print("  {} jobs, {} interactions, {} quotes".format(
        len(jobs), len(interactions), len(quotes)))

    # ── Deduplicate and merge ───────────────────────────────────────────
    print("\n[4/7] Deduplicating and merging...")

    # Build a unified contact registry keyed by normalized phone
    # Supabase is source of truth — local enriches it
    registry = {}  # phone -> merged contact dict

    # Index local by phone
    local_by_phone = {}
    local_by_email = {}
    for r in local_rows:
        p = normalize_phone(r.get("phone"))
        if p:
            local_by_phone[p] = r
        e = normalize_email(r.get("email"))
        if e:
            local_by_email[e] = r

    # Process Supabase contacts first (source of truth)
    seen_phones = set()
    seen_emails = set()
    dupes_removed = 0

    for sb in sb_contacts:
        phone = normalize_phone(sb.get("phone"))
        email = normalize_email(sb.get("email"))
        name = normalize_name(sb.get("name"))

        # Skip contacts with no usable identifier
        if not phone and not email and not name:
            dupes_removed += 1
            continue

        # Skip if we already have this phone (duplicate)
        if phone and phone in seen_phones:
            dupes_removed += 1
            continue

        # Skip if we already have this email (duplicate)
        if email and email in seen_emails:
            dupes_removed += 1
            continue

        # Skip non-name entries (emails-as-names, numbers-as-names)
        if not name:
            dupes_removed += 1
            continue

        # Merge with local data
        local = None
        if phone and phone in local_by_phone:
            local = local_by_phone[phone]
        elif email and email in local_by_email:
            local = local_by_email[email]

        # Build unified record
        contact = {
            "sb_id": sb["id"],
            "name": name,
            "phone": format_phone(phone),
            "email": email,
            "company": sb.get("company") or (local or {}).get("company"),
            "street_address": sb.get("street_address"),
            "city": sb.get("city"),
            "state": sb.get("state"),
            "zip": sb.get("zip"),
            "tags": sb.get("tags") or [],
            "is_core": sb.get("is_core", False),
            "core_rank": sb.get("core_rank") or 0,
            "preferred_channel": sb.get("preferred_channel"),
            "telegram_chat_id": sb.get("telegram_chat_id"),
            "telegram_username": sb.get("telegram_username"),
            "lat": sb.get("lat"),
            "lng": sb.get("lng"),
            # Enrichment data
            "ai_summary": sb.get("ai_summary"),
            "last_contacted": sb.get("last_contacted") or (local or {}).get("last_contacted"),
            # Scoring inputs
            "sb_interaction_count": int_counts.get(sb["id"], 0),
            "local_interaction_count": (local or {}).get("interaction_count") or 0,
            "job_count": job_counts.get(sb["id"], 0),
            "quote_count": quote_data.get(sb["id"], {}).get("count", 0),
            "quote_value": quote_data.get(sb["id"], {}).get("total", 0),
            # Local enrichment
            "local_category": (local or {}).get("category"),
            "local_ai_profile": (local or {}).get("ai_profile"),
            "local_client_status": (local or {}).get("client_status"),
            "local_linkedin": (local or {}).get("linkedin_url"),
        }

        # Use local AI profile if Supabase has none
        if not contact["ai_summary"] and contact["local_ai_profile"]:
            contact["ai_summary"] = contact["local_ai_profile"]

        if phone:
            seen_phones.add(phone)
        if email:
            seen_emails.add(email)

        # Score for ranking
        score = 0
        if contact["is_core"]:
            score += 40
        cr = contact["core_rank"] or 0
        if cr > 100:
            score += 25
        elif cr > 50:
            score += 15
        elif cr > 0:
            score += 5

        total_ic = max(contact["local_interaction_count"],
                       contact["sb_interaction_count"])
        if total_ic > 500:
            score += 50
        elif total_ic > 100:
            score += 35
        elif total_ic > 20:
            score += 20
        elif total_ic > 5:
            score += 10
        elif total_ic > 0:
            score += 5

        jc = contact["job_count"]
        if jc > 10:
            score += 40
        elif jc > 5:
            score += 25
        elif jc > 0:
            score += 15

        qv = contact["quote_value"]
        if qv > 1000:
            score += 20
        elif qv > 0:
            score += 10

        if contact["email"]:
            score += 5
        if contact["company"]:
            score += 5
        if contact["street_address"]:
            score += 5
        if contact["ai_summary"]:
            score += 3
        if contact["last_contacted"]:
            score += 10

        contact["score"] = score

        # Compute orbit
        if score >= 60:
            contact["orbit"] = 1
        elif score >= 40:
            contact["orbit"] = 2
        elif score >= 25:
            contact["orbit"] = 3
        elif score >= 10:
            contact["orbit"] = 4
        else:
            contact["orbit"] = 5

        key = phone or email or name
        registry[key] = contact

    print("  Unique contacts after dedup: {}".format(len(registry)))
    print("  Duplicates/junk removed: {}".format(dupes_removed))

    # ── Rank and select top 1500 ────────────────────────────────────────
    print("\n[5/7] Ranking and selecting top 1,500...")
    ranked = sorted(registry.values(), key=lambda c: c["score"], reverse=True)
    top_1500 = ranked[:1500]

    orbit_dist = defaultdict(int)
    has_summary = 0
    needs_summary = 0
    for c in top_1500:
        orbit_dist[c["orbit"]] += 1
        if c["ai_summary"]:
            has_summary += 1
        else:
            needs_summary += 1

    print("  Top 1,500 orbit distribution:")
    for o in range(1, 6):
        pct = orbit_dist[o] / 15  # percentage
        bar = "#" * int(pct)
        print("    Orbit {}: {:>5} ({:>5.1f}%) {}".format(
            o, orbit_dist[o], orbit_dist[o] / 15, bar))

    print("  With AI summary: {}".format(has_summary))
    print("  Need AI summary: {}".format(needs_summary))

    # Score range
    scores = [c["score"] for c in top_1500]
    print("  Score range: {} - {}".format(min(scores), max(scores)))

    # ── Report mode ─────────────────────────────────────────────────────
    if mode == "report":
        print("\n  Top 20 contacts:")
        print("  {:<4} {:<25} {:<20} {:<6} {:<5}".format(
            "Rank", "Name", "Company", "Score", "Orbit"))
        print("  " + "-" * 64)
        for i, c in enumerate(top_1500[:20]):
            comp = (c["company"] or "")[:18]
            print("  {:<4} {:<25} {:<20} {:<6} {:<5}".format(
                i + 1, (c["name"] or "?")[:23], comp, c["score"], c["orbit"]))

        print("\n" + "=" * 70)
        print("  DRY RUN — run with 'execute' to push to Supabase")
        print("  AI enrichment will run on {} contacts".format(needs_summary))
        print("=" * 70)
        return

    # ── Execute mode ────────────────────────────────────────────────────
    if mode == "execute":
        # Step 1: AI-enrich contacts missing summaries
        print("\n[6/7] AI-enriching {} contacts...".format(needs_summary))
        enriched = 0
        errors = 0
        for i, c in enumerate(top_1500):
            if c["ai_summary"]:
                continue

            ctx = []
            if c["company"]:
                ctx.append("Company: " + str(c["company"]))
            if c["email"]:
                ctx.append("Email: " + str(c["email"]))
            if c["phone"]:
                ctx.append("Phone: " + str(c["phone"]))
            tags = c.get("tags") or []
            if tags and tags != ["none"]:
                ctx.append("Tags: " + ", ".join(str(t) for t in tags))
            if c["local_category"] and c["local_category"] != "unknown":
                ctx.append("Category: " + str(c["local_category"]))
            if c["local_client_status"]:
                ctx.append("Status: " + str(c["local_client_status"]))
            total_ic = max(c["local_interaction_count"], c["sb_interaction_count"])
            if total_ic > 0:
                ctx.append("{} total interactions".format(total_ic))
            if c["job_count"] > 0:
                ctx.append("{} jobs on record".format(c["job_count"]))
            if c["quote_value"] > 0:
                ctx.append("Quote value: ${}".format(c["quote_value"]))
            if c["street_address"]:
                addr = c["street_address"]
                if c["city"]:
                    addr += ", " + c["city"]
                ctx.append("Address: " + addr)

            summary = claude_enrich(
                c["name"], c["phone"], c["email"], c["company"], tags, ctx
            )
            if summary:
                c["ai_summary"] = summary
                enriched += 1
            else:
                errors += 1

            if (enriched + errors) % 25 == 0:
                print("    {}/{} done ({} enriched, {} errors)".format(
                    enriched + errors, needs_summary, enriched, errors))
                time.sleep(0.3)

        print("    AI enrichment: {} enriched, {} errors".format(enriched, errors))

        # Step 2: Push updates to Supabase
        print("\n[7/7] Pushing 1,500 normalized contacts to Supabase...")
        updates = []
        for c in top_1500:
            # Build clean metadata
            meta = {}
            if c["local_category"] and c["local_category"] != "unknown":
                meta["category"] = c["local_category"]
            if c["local_client_status"]:
                meta["client_status"] = c["local_client_status"]
            if c["local_linkedin"]:
                meta["linkedin_url"] = c["local_linkedin"]
            total_ic = max(c["local_interaction_count"], c["sb_interaction_count"])
            if total_ic > 0:
                meta["interaction_count"] = total_ic
            if c["job_count"] > 0:
                meta["job_count"] = c["job_count"]
            if c["quote_value"] > 0:
                meta["quote_value"] = c["quote_value"]
            meta["engagement_score"] = c["score"]
            meta["orbit"] = c["orbit"]

            update_data = {
                "name": c["name"],
                "phone": c["phone"],
                "email": c["email"],
                "company": c["company"],
                "ai_summary": c["ai_summary"],
                "is_core": c["orbit"] <= 2,
                "core_rank": c["score"],
                "priority_score": c["orbit"],
                "metadata": meta,
                "last_contacted": c["last_contacted"],
            }
            # Remove None values
            update_data = {k: v for k, v in update_data.items() if v is not None}
            updates.append((c["sb_id"], update_data))

        ok = supabase_batch_patch("contacts", updates, label="contacts")

        # Mark non-top-1500 as non-core
        print("\n  Marking remaining contacts as non-core...")
        top_ids = set(c["sb_id"] for c in top_1500)
        demote = []
        for sb in sb_contacts:
            if sb["id"] not in top_ids and sb.get("is_core"):
                demote.append((sb["id"], {"is_core": False, "priority_score": 5}))
        if demote:
            supabase_batch_patch("contacts", demote, label="demote")

        print("\n" + "=" * 70)
        print("  MASTER BUILD COMPLETE")
        print("  {} contacts normalized and pushed".format(ok))
        print("  {} AI-enriched".format(enriched))
        print("  Top 1,500 scored, orbited, and ready")
        print("=" * 70)


if __name__ == "__main__":
    main()
