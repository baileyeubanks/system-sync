#!/usr/bin/env python3
"""
Blaze V4 — Overnight Contact Enrichment
Enriches contacts missing ai_summary with exponential backoff for rate limits.
Designed to run unattended overnight.
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error

SUPABASE_URL = "https://briokwdoonawhxisbydy.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJyaW9rd2Rvb25hd2h4aXNieWR5Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3MTU1Njc2MiwiZXhwIjoyMDg3MTMyNzYyfQ.5V1BsTrqIHGKUUHYJ3PBpL9re_WzKqOzKoQ94dc3me8")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", open(str(Path.home()/".blaze_env")).read().split("ANTHROPIC_API_KEY=",1)[-1].split()[0] if (Path.home()/".blaze_env").exists() and "ANTHROPIC_API_KEY=" in open(str(Path.home()/".blaze_env")).read() else "")

SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": "Bearer " + SUPABASE_KEY,
    "Content-Type": "application/json",
    "Prefer": "return=minimal",
}

# Rate limit config — conservative for overnight
BASE_DELAY = 2.0        # seconds between requests
MAX_BACKOFF = 300        # 5 min max wait on rate limit
BATCH_PAUSE = 10         # pause every N contacts
BATCH_PAUSE_SECS = 5     # seconds to pause between batches


def supabase_get(path):
    all_rows = []
    offset = 0
    limit = 1000
    while True:
        sep = "&" if "?" in path else "?"
        url = SUPABASE_URL + "/rest/v1/" + path + sep + "limit=" + str(limit) + "&offset=" + str(offset)
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


def supabase_patch(row_id, data):
    url = SUPABASE_URL + "/rest/v1/contacts?id=eq." + str(row_id)
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, headers=SB_HEADERS, method="PATCH")
    try:
        urllib.request.urlopen(req)
        return True
    except urllib.error.HTTPError as e:
        print("  PATCH error {}: {}".format(e.code, e.read().decode()[:200]))
        return False


def claude_enrich(name, context_lines, retry=0):
    """Call Claude with exponential backoff on 429."""
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
    except urllib.error.HTTPError as e:
        code = e.code
        body_text = ""
        try:
            body_text = e.read().decode()[:300]
        except Exception:
            pass

        if code == 429 and retry < 6:
            # Exponential backoff: 5, 10, 20, 40, 80, 160
            wait = min(5 * (2 ** retry), MAX_BACKOFF)
            print("    429 rate limited — waiting {}s (retry {}/6)".format(wait, retry + 1))
            time.sleep(wait)
            return claude_enrich(name, context_lines, retry + 1)
        elif code == 529 and retry < 4:
            # API overloaded
            wait = min(15 * (2 ** retry), MAX_BACKOFF)
            print("    529 overloaded — waiting {}s (retry {}/4)".format(wait, retry + 1))
            time.sleep(wait)
            return claude_enrich(name, context_lines, retry + 1)
        else:
            print("    Claude HTTP {}: {}".format(code, body_text[:150]))
            return None
    except Exception as e:
        print("    Claude error: {}".format(e))
        return None


def build_context(c):
    """Build context lines from contact data."""
    ctx = []
    meta = c.get("metadata") or {}

    if c.get("company"):
        ctx.append("Company: " + str(c["company"]))
    if c.get("email"):
        ctx.append("Email: " + str(c["email"]))
    if c.get("phone"):
        ctx.append("Phone: " + str(c["phone"]))
    tags = c.get("tags") or []
    if tags and tags != ["none"]:
        ctx.append("Tags: " + ", ".join(str(t) for t in tags))
    if meta.get("local_category") and meta["local_category"] != "unknown":
        ctx.append("Category: " + str(meta["local_category"]))
    if meta.get("client_status"):
        ctx.append("Status: " + str(meta["client_status"]))
    ic = meta.get("interaction_count", 0)
    if ic and int(ic) > 0:
        ctx.append("{} interactions".format(ic))
    if c.get("street_address"):
        addr = c["street_address"]
        if c.get("city"):
            addr += ", " + c["city"]
        ctx.append("Address: " + addr)
    if c.get("is_core"):
        ctx.append("Core contact (rank {})".format(c.get("core_rank", "?")))
    return ctx


def main():
    print("=" * 60)
    print("  OVERNIGHT CONTACT ENRICHMENT")
    print("  Started: " + time.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 60)

    # Only enrich the top build set (priority_score is set by master build)
    # Order by priority_score ASC so orbit 1 (closest) gets enriched first
    print("\nLoading top contacts without AI summaries...")
    contacts = supabase_get(
        "contacts?select=*&ai_summary=is.null&priority_score=not.is.null&order=priority_score.asc,core_rank.desc.nullslast"
    )
    print("  {} contacts need enrichment (from master build set)".format(len(contacts)))

    if not contacts:
        print("All contacts already enriched!")
        return

    enriched = 0
    errors = 0
    skipped = 0

    for i, c in enumerate(contacts):
        name = c.get("name", "")
        if not name or name.lower() in ("unknown", "none", ""):
            skipped += 1
            continue

        ctx = build_context(c)
        summary = claude_enrich(name, ctx)

        if summary:
            if supabase_patch(c["id"], {"ai_summary": summary}):
                enriched += 1
            else:
                errors += 1
        else:
            errors += 1

        # Progress
        done = enriched + errors + skipped
        if done % 10 == 0:
            print("  [{}/{}] enriched={}, errors={}, skipped={}  ({})".format(
                done, len(contacts), enriched, errors, skipped,
                time.strftime("%H:%M:%S")))

        # Rate limit: base delay between every request
        time.sleep(BASE_DELAY)

        # Extra pause every batch
        if (enriched + errors) > 0 and (enriched + errors) % BATCH_PAUSE == 0:
            print("    batch pause {}s...".format(BATCH_PAUSE_SECS))
            time.sleep(BATCH_PAUSE_SECS)

    print("\n" + "=" * 60)
    print("  ENRICHMENT COMPLETE")
    print("  Enriched: {}".format(enriched))
    print("  Errors:   {}".format(errors))
    print("  Skipped:  {}".format(skipped))
    print("  Finished: " + time.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 60)


if __name__ == "__main__":
    main()
