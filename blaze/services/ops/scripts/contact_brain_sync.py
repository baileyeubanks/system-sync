#!/usr/bin/env python3
"""
contact_brain_sync.py — Unified Contact Brain batch process
Pulls all sources, merges, deduplicates, scores, tiers, and AI-enriches.

Run as:  nohup python3 contact_brain_sync.py &
Or:      python3 contact_brain_sync.py --skip-enrich   (fast: sync only, no AI)

Phases:
  1. PULL    — Gmail People API (4 accounts) + iMessage chat.db handles
  2. MERGE   — deduplicate by email then name, fill blanks
  3. SCORE   — recalculate priority scores, assign tiers
  4. ENRICH  — AI profiles for contacts missing them at their tier level
  5. EXIT    — log results, clean exit
"""
import warnings
warnings.filterwarnings("ignore")

import sys, os, sqlite3, re, time, json, logging
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

DB = "/Users/_mxappservice/blaze-data/blaze.db"
CHAT_DB = "/Users/_mxappservice/Library/Messages/chat.db"
LOG_DIR = "/Users/_mxappservice/blaze-logs"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(LOG_DIR, "contact_brain_sync.log")),
    ]
)
log = logging.getLogger("brain_sync")

SKIP_ENRICH = "--skip-enrich" in sys.argv

# ── Phase 1: PULL ──────────────────────────────────────────────────────────

def pull_gmail_contacts():
    """Pull contacts from Google People API for all Workspace accounts."""
    try:
        from google_api_manager import get_api
    except ImportError:
        log.warning("google_api_manager not importable, skipping Gmail pull")
        return []

    api = get_api()
    contacts = []

    for account in ["bailey@contentco-op.com", "caio@astrocleanings.com", "blaze@contentco-op.com"]:
        try:
            ws = api.workspace(account)
            result = ws.execute(
                ws.people.people().connections().list(
                    resourceName="people/me",
                    pageSize=200,
                    personFields="names,emailAddresses,phoneNumbers,organizations",
                )
            )
            for person in result.get("connections", []):
                names = person.get("names", [{}])
                name = names[0].get("displayName", "") if names else ""
                emails = person.get("emailAddresses", [])
                email = emails[0].get("value", "").lower() if emails else ""
                phones = person.get("phoneNumbers", [])
                phone = phones[0].get("value", "") if phones else ""
                orgs = person.get("organizations", [])
                company = orgs[0].get("name", "") if orgs else ""
                title = orgs[0].get("title", "") if orgs else ""

                if name and (email or phone):
                    contacts.append({
                        "name": name,
                        "email": email,
                        "phone": clean_phone(phone),
                        "company": company,
                        "title": title,
                        "source": "gmail:%s" % account,
                    })
            log.info("Gmail %s: %d connections" % (account.split("@")[0], len(result.get("connections", []))))
        except Exception as e:
            log.warning("Gmail pull failed for %s: %s" % (account, e))

    return contacts


def pull_imessage_handles():
    """Pull unique handles from iMessage chat.db."""
    contacts = []
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % CHAT_DB, uri=True)
        cutoff = (datetime.now() - timedelta(days=90)).timestamp()
        apple_cutoff = (cutoff - 978307200) * 1000000000

        rows = conn.execute("""
            SELECT DISTINCT h.id, COUNT(m.ROWID) as msg_count
            FROM handle h
            JOIN chat_handle_join chj ON h.ROWID = chj.handle_id
            JOIN chat c ON chj.chat_id = c.ROWID
            JOIN chat_message_join cmj ON c.ROWID = cmj.chat_id
            JOIN message m ON cmj.message_id = m.ROWID
            WHERE m.date > ?
            AND m.text IS NOT NULL
            AND length(m.text) > 2
            GROUP BY h.id
            HAVING msg_count >= 2
        """, (apple_cutoff,)).fetchall()
        conn.close()

        for handle, count in rows:
            h = handle.strip()
            # Skip short codes and automated
            if h.isdigit() and len(h) < 7:
                continue
            phone = clean_phone(h) if not "@" in h else ""
            email = h.lower() if "@" in h else ""
            contacts.append({
                "name": "",  # Will be resolved during merge
                "email": email,
                "phone": phone,
                "handle": h,
                "company": "",
                "title": "",
                "source": "imessage",
                "interaction_count": count,
            })
        log.info("iMessage: %d active handles (90 days, 2+ messages)" % len(contacts))
    except Exception as e:
        log.warning("iMessage pull failed: %s" % e)

    return contacts


def clean_phone(p):
    if not p:
        return ""
    digits = re.sub(r"\D", "", p)
    if len(digits) == 10:
        return "+1%s" % digits
    if len(digits) == 11 and digits[0] == "1":
        return "+%s" % digits
    return p.strip()


# ── Phase 2: MERGE ─────────────────────────────────────────────────────────

def merge_contacts(pulled):
    """Merge pulled contacts into contacts.db. Dedup by email then name."""
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    # Build lookup indexes
    existing_by_email = {}
    existing_by_name = {}
    existing_by_handle = {}
    for row in conn.execute("SELECT id, name, email, phone, handle FROM contacts"):
        if row["email"]:
            existing_by_email[row["email"].lower()] = row["id"]
        if row["name"]:
            existing_by_name[row["name"].lower()] = row["id"]
        if row["handle"]:
            existing_by_handle[row["handle"].lower()] = row["id"]

    inserted = 0
    updated = 0
    skipped = 0

    for c in pulled:
        name = c.get("name", "").strip()
        email = c.get("email", "").strip().lower()
        phone = c.get("phone", "").strip()
        handle = c.get("handle", "").strip()
        company = c.get("company", "").strip()
        title = c.get("title", "").strip()

        # Find existing match
        existing_id = None
        if email and email in existing_by_email:
            existing_id = existing_by_email[email]
        elif handle and handle.lower() in existing_by_handle:
            existing_id = existing_by_handle[handle.lower()]
        elif name and name.lower() in existing_by_name:
            existing_id = existing_by_name[name.lower()]

        if existing_id:
            # Fill blanks only — skip phone/email/handle if they'd cause UNIQUE collision
            try:
                conn.execute("""
                    UPDATE contacts SET
                        company = CASE WHEN (company IS NULL OR company = '') AND ? != '' THEN ? ELSE company END,
                        title = CASE WHEN (title IS NULL OR title = '') AND ? != '' THEN ? ELSE title END,
                        updated_at = ?
                    WHERE id = ?
                """, (
                    company, company,
                    title, title,
                    datetime.now().isoformat(),
                    existing_id,
                ))
                # Try UNIQUE fields individually so one failure doesn't block the rest
                for col, val in [("handle", handle or phone), ("phone", phone), ("email", email)]:
                    if val:
                        try:
                            conn.execute(
                                "UPDATE contacts SET %s = ? WHERE id = ? AND (%s IS NULL OR %s = '')" % (col, col, col),
                                (val, existing_id)
                            )
                        except sqlite3.IntegrityError:
                            pass  # Value exists on another contact
                updated += 1
            except sqlite3.IntegrityError:
                skipped += 1
        else:
            if not name and not email:
                skipped += 1
                continue
            display = name or email or handle
            try:
                conn.execute("""
                    INSERT INTO contacts (
                        name, handle, email, phone, company, title,
                        category, enrichment_tier, priority_score, orbit,
                        client_status, source, interaction_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    display,
                    handle or phone or None,
                    email or None,
                    phone or None,
                    company,
                    title,
                    "business" if company else "unknown",
                    500, 10.0, 5,
                    "none",
                    c.get("source", "sync"),
                    c.get("interaction_count", 0),
                ))
                if email:
                    existing_by_email[email] = True
                if name:
                    existing_by_name[name.lower()] = True
                inserted += 1
            except sqlite3.IntegrityError:
                skipped += 1

    conn.commit()
    conn.close()
    log.info("Merge: %d inserted, %d updated, %d skipped" % (inserted, updated, skipped))
    return inserted, updated


# ── Phase 3: SCORE ─────────────────────────────────────────────────────────

def score_and_tier():
    """Recalculate priority scores and assign tiers."""
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT id, name, email, phone, company, title, category,
               client_status, interaction_count, last_contacted,
               business_tags, handle, source
        FROM contacts
    """).fetchall()

    # Junk name patterns — automated messages parsed as contacts
    JUNK_NAMES = [
        "deposited", "payment", "verification", "security code",
        "your order", "shipped", "delivered", "scheduled",
        "reminder", "alert", "notification", "no-reply",
        "noreply", "do not reply", "automated",
    ]

    scores = []
    for row in rows:
        score = 0.0

        # Junk filter — penalize automated/bot contacts
        name_lower = (row["name"] or "").lower()
        if any(junk in name_lower for junk in JUNK_NAMES):
            score = -100  # Will sink to bottom
            scores.append((row["id"], score))
            continue

        # Name quality
        name = row["name"] or ""
        if name and len(name) > 2 and " " in name:
            score += 20
        elif name:
            score += 5

        # Contact info
        if row["email"]:
            score += 10
        if row["phone"] or row["handle"]:
            score += 8

        # Company / professional
        if row["company"]:
            score += 10
        if row["title"]:
            score += 5

        # Business signals
        category = (row["category"] or "").lower()
        if category == "business":
            score += 15
        client_status = (row["client_status"] or "").lower()
        if client_status in ("active_client", "active_lead"):
            score += 25
        elif client_status in ("past_client", "warm_lead"):
            score += 15

        # Interaction count
        interactions = row["interaction_count"] or 0
        if interactions >= 50:
            score += 20
        elif interactions >= 20:
            score += 15
        elif interactions >= 5:
            score += 10
        elif interactions >= 1:
            score += 5

        # Recency
        last = row["last_contacted"] or ""
        if last:
            try:
                last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
                days_ago = (datetime.now() - last_dt.replace(tzinfo=None)).days
                if days_ago <= 7:
                    score += 15
                elif days_ago <= 30:
                    score += 10
                elif days_ago <= 90:
                    score += 5
            except (ValueError, TypeError):
                pass

        # Business tags boost
        tags = row["business_tags"] or ""
        if "video_client" in tags:
            score += 10
        if "active_quote" in tags:
            score += 10
        if "referral" in tags:
            score += 5

        scores.append((row["id"], score))

    # Sort and assign tiers
    scores.sort(key=lambda x: x[1], reverse=True)

    tier_map = {}
    for i, (cid, sc) in enumerate(scores):
        if i < 10:
            tier = 10
            orbit = 1
        elif i < 25:
            tier = 25
            orbit = 2
        elif i < 100:
            tier = 100
            orbit = 3
        elif i < 500:
            tier = 500
            orbit = 4
        else:
            tier = 500
            orbit = 5
        tier_map[cid] = (sc, tier, orbit)

    # Write back
    for cid, (sc, tier, orbit) in tier_map.items():
        conn.execute(
            "UPDATE contacts SET priority_score=?, enrichment_tier=?, orbit=? WHERE id=?",
            (round(sc, 2), tier, orbit, cid)
        )

    conn.commit()
    total = len(scores)
    t10 = sum(1 for _, (_, t, _) in tier_map.items() if t == 10)
    t25 = sum(1 for _, (_, t, _) in tier_map.items() if t == 25)
    t100 = sum(1 for _, (_, t, _) in tier_map.items() if t == 100)
    t500 = sum(1 for _, (_, t, _) in tier_map.items() if t == 500)

    log.info("Scored %d contacts: T10=%d T25=%d T100=%d T500=%d" % (total, t10, t25, t100, t500))

    # Log top 10
    top10 = scores[:10]
    for cid, sc in top10:
        row = conn.execute("SELECT name FROM contacts WHERE id=?", (cid,)).fetchone()
        log.info("  T10: %s (%.1f)" % (row[0] if row else "?", sc))

    conn.close()
    return total


# ── Phase 4: ENRICH ────────────────────────────────────────────────────────

TIER_PROMPTS = {
    10: {
        "column": "ai_profile_deep",
        "prompt": (
            "Build a comprehensive relationship profile for {name}. "
            "Known: email={email}, phone={phone}, company={company}, title={title}, "
            "category={category}, client_status={client_status}, "
            "interactions={interaction_count}, business_tags={business_tags}. "
            "Return: 1) Professional background 2) Company context "
            "3) How we know them 4) Business opportunities "
            "5) Recommended next action 6) Key conversation topics. "
            "Be specific. This is a TOP 10 priority contact."
        ),
    },
    25: {
        "column": "ai_profile_enriched",
        "prompt": (
            "Build an enriched contact profile for {name}. "
            "Known: email={email}, company={company}, title={title}, "
            "category={category}, client_status={client_status}, "
            "interactions={interaction_count}. "
            "Return: 1) Who they are 2) Company and role "
            "3) Relationship summary 4) Business relevance "
            "5) Suggested next touchpoint. 3-5 sentences per section."
        ),
    },
    100: {
        "column": "ai_profile",
        "prompt": (
            "Brief contact profile for {name}. "
            "Known: email={email}, company={company}, category={category}, "
            "client_status={client_status}, interactions={interaction_count}. "
            "Return: who they are, how we know them, one actionable insight. "
            "3-4 sentences max."
        ),
    },
    500: {
        "column": "ai_profile",
        "prompt": (
            "One-line contact summary for {name}. "
            "Known: email={email}, company={company}, category={category}. "
            "Return a single sentence: who they are and how we likely know them."
        ),
    },
}


def enrich_contacts():
    """Generate AI profiles for contacts missing them at their tier level."""
    try:
        from google_api_manager import ai_generate
    except ImportError:
        log.warning("google_api_manager not importable, trying blaze_helper")
        try:
            from blaze_helper import ask_blaze
            def ai_generate(prompt, system=None):
                result = ask_blaze(prompt, agent="research-worker", timeout=90)
                if not result or result.startswith("CLI_ERROR"):
                    return None
                return result
        except ImportError:
            log.error("No AI backend available, skipping enrichment")
            return 0

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    total_generated = 0
    total_errors = 0

    for tier in [10, 25, 100, 500]:
        config = TIER_PROMPTS[tier]
        col = config["column"]

        rows = conn.execute("""
            SELECT id, name, email, phone, company, title, category,
                   client_status, interaction_count, business_tags
            FROM contacts
            WHERE enrichment_tier = ?
              AND (%s IS NULL OR %s = '')
            ORDER BY priority_score DESC
        """ % (col, col), (tier,)).fetchall()

        if not rows:
            log.info("T%d: all profiles complete" % tier)
            continue

        log.info("T%d: %d contacts need %s profiles" % (tier, len(rows), col))

        for i, row in enumerate(rows):
            name = row["name"] or "Unknown"
            log.info("  T%d [%d/%d] %s..." % (tier, i + 1, len(rows), name))

            fields = {
                "name": name,
                "email": row["email"] or "none",
                "phone": row["phone"] or "none",
                "company": row["company"] or "unknown",
                "title": row["title"] or "unknown",
                "category": row["category"] or "unknown",
                "client_status": row["client_status"] or "none",
                "interaction_count": row["interaction_count"] or 0,
                "business_tags": row["business_tags"] or "none",
            }

            prompt = config["prompt"].format(**fields)

            try:
                result = ai_generate(prompt=prompt)
                if not result or len(result) < 10:
                    log.warning("    EMPTY response")
                    total_errors += 1
                    continue

                if "rate limit" in result.lower():
                    log.warning("    RATE LIMITED — backing off 120s")
                    time.sleep(120)
                    result = ai_generate(prompt=prompt)
                    if not result or "rate limit" in (result or "").lower():
                        log.warning("    Still limited — skipping")
                        total_errors += 1
                        continue

                conn.execute(
                    "UPDATE contacts SET %s = ?, last_enriched = ? WHERE id = ?" % col,
                    (result.strip(), datetime.utcnow().isoformat(), row["id"])
                )
                conn.commit()
                total_generated += 1

                preview = result[:60].replace("\n", " ")
                log.info("    OK (%d chars) %s..." % (len(result), preview))

            except Exception as e:
                log.warning("    ERROR: %s" % e)
                total_errors += 1

            # Rate limit between AI calls
            time.sleep(8)

    conn.close()
    log.info("Enrichment: %d generated, %d errors" % (total_generated, total_errors))
    return total_generated


# ── Phase 5: RUN ───────────────────────────────────────────────────────────

def run():
    start = datetime.now()
    log.info("=" * 50)
    log.info("CONTACT BRAIN SYNC — started %s" % start.strftime("%Y-%m-%d %H:%M"))
    log.info("=" * 50)

    # Phase 1: Pull
    log.info("")
    log.info("── PHASE 1: PULL ──")
    gmail_contacts = pull_gmail_contacts()
    imessage_contacts = pull_imessage_handles()
    all_pulled = gmail_contacts + imessage_contacts
    log.info("Total pulled: %d (Gmail=%d, iMessage=%d)" % (
        len(all_pulled), len(gmail_contacts), len(imessage_contacts)
    ))

    # Phase 2: Merge
    log.info("")
    log.info("── PHASE 2: MERGE ──")
    inserted, updated = merge_contacts(all_pulled)

    # Phase 3: Score
    log.info("")
    log.info("── PHASE 3: SCORE ──")
    total = score_and_tier()

    # Phase 4: Enrich (unless --skip-enrich)
    if SKIP_ENRICH:
        log.info("")
        log.info("── PHASE 4: ENRICH (skipped via --skip-enrich) ──")
        generated = 0
    else:
        log.info("")
        log.info("── PHASE 4: ENRICH ──")
        generated = enrich_contacts()

    # Phase 5: Summary
    elapsed = (datetime.now() - start).total_seconds()
    log.info("")
    log.info("=" * 50)
    log.info("CONTACT BRAIN SYNC — complete")
    log.info("  Pulled: %d contacts from sources" % len(all_pulled))
    log.info("  Inserted: %d new" % inserted)
    log.info("  Updated: %d enriched" % updated)
    log.info("  Scored: %d total" % total)
    log.info("  AI profiles: %d generated" % generated)
    log.info("  Elapsed: %.0fs (%.1fm)" % (elapsed, elapsed / 60))
    log.info("=" * 50)

    # Log to cron-log.db
    try:
        from blaze_helper import log_cron
        log_cron("contact_brain_sync", "success",
                 "%d pulled, %d new, %d updated, %d scored, %d enriched, %.0fs" % (
                     len(all_pulled), inserted, updated, total, generated, elapsed
                 ))
    except ImportError:
        pass


if __name__ == "__main__":
    run()
