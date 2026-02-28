#!/usr/bin/env python3
"""
Blaze V4 — Contact Enrichment Engine
Merges local SQLite enrichment data into Supabase, then AI-enriches remaining contacts.
"""
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
import urllib.error

# ── Config ──────────────────────────────────────────────────────────────────
SUPABASE_URL = "https://briokwdoonawhxisbydy.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJyaW9rd2Rvb25hd2h4aXNieWR5Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3MTU1Njc2MiwiZXhwIjoyMDg3MTMyNzYyfQ.5V1BsTrqIHGKUUHYJ3PBpL9re_WzKqOzKoQ94dc3me8"
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

ANTHROPIC_KEY = _load_anthropic_key()
LOCAL_DB = "/Users/_mxappservice/blaze-data/contacts/contacts.db"
ACS_BIZ_ID = "0ade82e3-ffe9-4c17-ae59-fc4bd198482b"

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": "Bearer " + SUPABASE_KEY,
    "Content-Type": "application/json",
    "Prefer": "return=minimal",
}


def normalize_phone(p):
    """Strip to digits, take last 10."""
    if not p:
        return None
    digits = re.sub(r"\D", "", str(p))
    if len(digits) >= 10:
        return digits[-10:]
    return digits if digits else None


def supabase_get(path, params=""):
    """GET from Supabase REST API with pagination."""
    all_rows = []
    offset = 0
    limit = 1000
    while True:
        sep = "&" if "?" in path else "?"
        url = SUPABASE_URL + "/rest/v1/" + path + sep + "limit=" + str(limit) + "&offset=" + str(offset)
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
    """PATCH a single row in Supabase."""
    url = SUPABASE_URL + "/rest/v1/" + table + "?id=eq." + row_id
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, headers=HEADERS, method="PATCH")
    try:
        urllib.request.urlopen(req)
        return True
    except urllib.error.HTTPError as e:
        print("  PATCH error {}: {}".format(e.code, e.read().decode()[:200]))
        return False


def claude_enrich(name, phone, email, company, tags, interactions_summary, jobs_summary):
    """Call Claude to generate a rich contact summary."""
    context_parts = []
    if company:
        context_parts.append("Company: " + str(company))
    if email:
        context_parts.append("Email: " + str(email))
    if phone:
        context_parts.append("Phone: " + str(phone))
    if tags and tags != ["none"]:
        context_parts.append("Tags: " + ", ".join(str(t) for t in tags))
    if interactions_summary:
        context_parts.append("Recent interactions: " + interactions_summary)
    if jobs_summary:
        context_parts.append("Job history: " + jobs_summary)

    context = "\n".join(context_parts) if context_parts else "No additional context available."

    prompt = (
        "Write a 1-2 sentence professional summary for this contact. "
        "Include their likely relationship to the business (client, lead, vendor, personal), "
        "key details, and any notable patterns. Be concise and factual.\n\n"
        "Contact: {}\n{}".format(name, context)
    )

    body = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 150,
        "messages": [{"role": "user", "content": prompt}],
        "system": (
            "You write brief, factual contact summaries for a CRM. "
            "Two businesses: Astro Cleaning Services (ACS, Houston commercial cleaning) "
            "and Content Co-op (B2B video production). Be direct, no fluff."
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
        print("  Claude error: {}".format(e))
        return None


def compute_orbit(contact, interaction_count=0, job_count=0, is_core=False, core_rank=0):
    """Compute orbit 1-5 based on engagement signals."""
    score = 0

    # Core contacts get a boost
    if is_core:
        score += 30
    if core_rank and core_rank > 0:
        score += min(20, core_rank // 5)

    # Interaction depth
    if interaction_count > 500:
        score += 40
    elif interaction_count > 100:
        score += 30
    elif interaction_count > 20:
        score += 20
    elif interaction_count > 5:
        score += 10

    # Job history (ACS clients)
    if job_count > 10:
        score += 30
    elif job_count > 5:
        score += 20
    elif job_count > 0:
        score += 10

    # Has email (more engaged)
    if contact.get("email"):
        score += 5

    # Has AI summary (we know them)
    if contact.get("ai_summary"):
        score += 5

    # Has company (business relationship)
    if contact.get("company"):
        score += 5

    # Recent contact
    if contact.get("last_contacted"):
        score += 10

    # Map score to orbit (1=closest, 5=distant)
    if score >= 60:
        return 1
    elif score >= 40:
        return 2
    elif score >= 25:
        return 3
    elif score >= 10:
        return 4
    else:
        return 5


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "report"

    print("=" * 60)
    print("BLAZE CONTACT ENRICHMENT ENGINE")
    print("=" * 60)

    # ── Step 1: Load local contacts ─────────────────────────────────────
    print("\n[1/5] Loading local SQLite contacts...")
    conn = sqlite3.connect(LOCAL_DB)
    conn.row_factory = sqlite3.Row
    local_rows = conn.execute("SELECT * FROM contacts").fetchall()
    local_by_phone = {}
    local_by_email = {}
    for r in local_rows:
        p = normalize_phone(r["phone"])
        if p:
            local_by_phone[p] = dict(r)
        e = (r["email"] or "").lower().strip()
        if e:
            local_by_email[e] = dict(r)
    conn.close()
    print("  Local: {} contacts ({} with phone, {} with email)".format(
        len(local_rows), len(local_by_phone), len(local_by_email)))

    # ── Step 2: Load Supabase contacts ──────────────────────────────────
    print("\n[2/5] Loading Supabase contacts...")
    sb_contacts = supabase_get("contacts?select=*")
    print("  Supabase: {} contacts".format(len(sb_contacts)))

    # ── Step 3: Match and merge ─────────────────────────────────────────
    print("\n[3/5] Matching contacts (phone + email)...")
    matched = 0
    enrichable = 0
    merge_updates = []

    for sb in sb_contacts:
        sb_phone = normalize_phone(sb.get("phone"))
        sb_email = (sb.get("email") or "").lower().strip()

        local = None
        if sb_phone and sb_phone in local_by_phone:
            local = local_by_phone[sb_phone]
        elif sb_email and sb_email in local_by_email:
            local = local_by_email[sb_email]

        if local:
            matched += 1
            update = {}
            meta = dict(sb.get("metadata") or {})

            # Merge interaction count
            ic = local.get("interaction_count") or 0
            if ic > 0:
                meta["interaction_count"] = ic

            # Merge category
            cat = local.get("category")
            if cat and cat != "unknown":
                meta["local_category"] = cat

            # Merge AI profile if Supabase lacks summary
            if not sb.get("ai_summary") and local.get("ai_profile"):
                update["ai_summary"] = local["ai_profile"]
                enrichable += 1

            # Merge client_status
            cs = local.get("client_status")
            if cs:
                meta["client_status"] = cs

            if meta != (sb.get("metadata") or {}):
                update["metadata"] = meta

            if update:
                merge_updates.append((sb["id"], update))

    print("  Matched: {}/{} Supabase contacts".format(matched, len(sb_contacts)))
    print("  Enrichable (local AI profile → Supabase): {}".format(enrichable))
    print("  Updates to push: {}".format(len(merge_updates)))

    # ── Step 4: Count contacts needing AI enrichment ────────────────────
    needs_ai = [c for c in sb_contacts if not c.get("ai_summary")]
    # Subtract the ones we'll fill from local
    still_needs_ai = len(needs_ai) - enrichable
    print("\n[4/5] Contacts needing AI enrichment: {}".format(still_needs_ai))

    # ── Step 5: Load job/interaction counts for orbit scoring ───────────
    print("\n[5/5] Loading job counts for orbit scoring...")
    jobs = supabase_get("jobs?select=client_profile_id")
    job_counts = {}
    for j in jobs:
        cp = j.get("client_profile_id")
        if cp:
            job_counts[cp] = job_counts.get(cp, 0) + 1

    interactions = supabase_get("interactions?select=contact_id")
    int_counts = {}
    for i in interactions:
        cid = i.get("contact_id")
        if cid:
            int_counts[cid] = int_counts.get(cid, 0) + 1
    print("  {} contacts with jobs, {} with interactions".format(
        len(job_counts), len(int_counts)))

    # ── Compute orbits for all contacts ─────────────────────────────────
    orbit_dist = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    orbit_updates = []
    for sb in sb_contacts:
        # Get interaction count from local merge or Supabase interactions
        sb_phone = normalize_phone(sb.get("phone"))
        local = local_by_phone.get(sb_phone) if sb_phone else None
        local_ic = (local or {}).get("interaction_count", 0) or 0
        sb_ic = int_counts.get(sb["id"], 0)
        total_ic = max(local_ic, sb_ic)

        jc = job_counts.get(sb["id"], 0)
        orbit = compute_orbit(
            sb,
            interaction_count=total_ic,
            job_count=jc,
            is_core=sb.get("is_core", False),
            core_rank=sb.get("core_rank") or 0,
        )
        orbit_dist[orbit] += 1

        # Store orbit in priority_score (repurposing: 1=highest priority)
        if sb.get("priority_score") != orbit:
            orbit_updates.append((sb["id"], {"priority_score": orbit}))

    print("\n  Orbit distribution:")
    for o in range(1, 6):
        bar = "#" * (orbit_dist[o] // 20)
        print("    Orbit {}: {:>5} {}".format(o, orbit_dist[o], bar))

    # ── Report mode: stop here ──────────────────────────────────────────
    if mode == "report":
        print("\n" + "=" * 60)
        print("DRY RUN COMPLETE — run with 'merge' to push local data")
        print("                   run with 'enrich' to AI-enrich + merge")
        print("                   run with 'full' for everything")
        print("=" * 60)
        return

    # ── Merge mode: push local enrichment to Supabase ───────────────────
    if mode in ("merge", "enrich", "full"):
        print("\n>>> Pushing {} merge updates to Supabase...".format(len(merge_updates)))
        ok = 0
        for row_id, update in merge_updates:
            if supabase_patch("contacts", row_id, update):
                ok += 1
            if ok % 50 == 0 and ok > 0:
                print("  ... {}/{} done".format(ok, len(merge_updates)))
        print("  Merge complete: {}/{} updated".format(ok, len(merge_updates)))

    # ── Push orbit scores ───────────────────────────────────────────────
    if mode in ("merge", "enrich", "full"):
        print("\n>>> Pushing {} orbit scores...".format(len(orbit_updates)))
        ok = 0
        for row_id, update in orbit_updates:
            if supabase_patch("contacts", row_id, update):
                ok += 1
            if ok % 100 == 0 and ok > 0:
                print("  ... {}/{} done".format(ok, len(orbit_updates)))
        print("  Orbits set: {}/{} updated".format(ok, len(orbit_updates)))

    # ── AI enrichment mode ──────────────────────────────────────────────
    if mode in ("enrich", "full"):
        # Get interaction summaries for context
        print("\n>>> AI-enriching contacts without summaries...")
        batch = [c for c in sb_contacts if not c.get("ai_summary")]
        # Skip ones that will be filled by local merge
        already_filled = set()
        for row_id, update in merge_updates:
            if "ai_summary" in update:
                already_filled.add(row_id)
        batch = [c for c in batch if c["id"] not in already_filled]

        print("  {} contacts to enrich".format(len(batch)))
        enriched = 0
        errors = 0
        max_enrich = 200  # cap per run to manage API costs

        for i, contact in enumerate(batch[:max_enrich]):
            name = contact.get("name", "Unknown")
            phone = contact.get("phone")
            email = contact.get("email")
            company = contact.get("company")
            tags = contact.get("tags") or []

            # Get interaction summary
            int_summary = ""
            ic = int_counts.get(contact["id"], 0)
            if ic > 0:
                int_summary = "{} recorded interactions".format(ic)

            # Get job summary
            job_summary = ""
            jc = job_counts.get(contact["id"], 0)
            if jc > 0:
                job_summary = "{} jobs on record".format(jc)

            summary = claude_enrich(name, phone, email, company, tags, int_summary, job_summary)
            if summary:
                if supabase_patch("contacts", contact["id"], {"ai_summary": summary}):
                    enriched += 1
            else:
                errors += 1

            if (i + 1) % 10 == 0:
                print("  ... {}/{} enriched ({} errors)".format(enriched, i + 1, errors))
                time.sleep(0.5)  # gentle rate limiting

        print("  AI enrichment complete: {} enriched, {} errors".format(enriched, errors))

    print("\n" + "=" * 60)
    print("ENRICHMENT ENGINE COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
