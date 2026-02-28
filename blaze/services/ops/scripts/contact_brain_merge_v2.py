#!/usr/bin/env python3
"""
Contact Brain Merge v2
- Deduplicates phone + email for same person
- Loads top 1000 contacts
- Assigns enrichment tiers: 10 / 25 / 100 / 500 / 1000
"""
import json, sqlite3, os, re
from datetime import datetime

DATA_ROOT = "/Users/_mxappservice/blaze-data"
RAW = "%s/contacts/raw" % DATA_ROOT
DB = "%s/blaze.db" % DATA_ROOT

conn = sqlite3.connect(DB)
conn.execute("PRAGMA journal_mode=WAL")
conn.row_factory = sqlite3.Row

# ── STEP 1: Upgrade schema (ignore errors for existing columns) ──
new_columns = [
    ("handle", "TEXT"),
    ("email", "TEXT"),
    ("company", "TEXT"),
    ("title", "TEXT"),
    ("category", "TEXT DEFAULT 'unknown'"),
    ("orbit", "INTEGER DEFAULT 5"),
    ("priority_score", "REAL DEFAULT 0"),
    ("relationship_health_score", "REAL DEFAULT 0"),
    ("first_contacted", "TEXT"),
    ("last_contacted", "TEXT"),
    ("initiator", "TEXT"),
    ("interaction_count", "INTEGER DEFAULT 0"),
    ("ai_profile", "TEXT"),
    ("ai_profile_enriched", "TEXT"),
    ("ai_profile_deep", "TEXT"),
    ("ai_profile_live", "TEXT"),
    ("enrichment_tier", "INTEGER DEFAULT 0"),
    ("notes", "TEXT"),
    ("client_status", "TEXT"),
    ("business_tags", "TEXT"),
    ("linkedin_url", "TEXT"),
    ("last_enriched", "TEXT"),
]
for col, coltype in new_columns:
    try:
        conn.execute(f"ALTER TABLE contacts ADD COLUMN {col} {coltype}")
        conn.commit()
    except:
        pass

# Add unique index on handle if not exists
try:
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_handle ON contacts(handle)")
    conn.commit()
except:
    pass

print("Schema ready")

# ── STEP 2: Build master dict (handle -> record) ─────────────────
# Keyed by normalized phone or email — deduplicates as we go
master = {}

def normalize_phone(p):
    digits = re.sub(r'\D', '', str(p))
    if len(digits) == 10:
        return '+1' + digits
    if len(digits) == 11 and digits.startswith('1'):
        return '+' + digits
    return '+' + digits

def merge_into(key, **kwargs):
    if key not in master:
        master[key] = {"handle": key}
    for k, v in kwargs.items():
        if v and not master[key].get(k):
            master[key][k] = v

# Load contact-matches.json
try:
    with open(f"{RAW}/contact-matches.json") as f:
        matches = json.load(f)
    for handle, info in matches.items():
        merge_into(handle, name=info.get("name"), source="contact-matches")
    print(f"contact-matches: {len(matches)} loaded")
except Exception as e:
    print(f"contact-matches error: {e}")

# Load contacts_merged.json
try:
    with open(f"{RAW}/contacts_merged.json") as f:
        merged = json.load(f)
    for handle, info in merged.items():
        name = info.get("name")
        email = info.get("email")
        company = info.get("company")
        title = info.get("title")
        if not any([name, email, company, title]):
            continue
        merge_into(handle, name=name, email=email, company=company, title=title)
        if email and '@' in str(handle):
            merge_into(handle, email=handle)
    print(f"contacts_merged: {len(merged)} processed")
except Exception as e:
    print(f"contacts_merged error: {e}")

# Load relationship_data.json — scoring
try:
    with open(f"{RAW}/relationship_data.json") as f:
        rel = json.load(f)
    for handle, info in rel.items():
        msg_count = info.get("msg_count", 0)
        first = info.get("first_contact")
        last = info.get("last_contact")
        initiator = info.get("initiator", "unknown")
        category = info.get("relationship_category", "unknown")

        recency_score = 0
        orbit = 5
        if last:
            try:
                dt = datetime.fromisoformat(last.replace('Z',''))
                days_ago = (datetime.utcnow() - dt).days
                recency_score = max(0, 100 - days_ago * 0.3)
                orbit = (1 if days_ago <= 7 else
                         2 if days_ago <= 30 else
                         3 if days_ago <= 90 else
                         4 if days_ago <= 180 else 5)
            except:
                pass

        freq_score = min(100, msg_count * 1.5)
        priority = round((recency_score * 0.6) + (freq_score * 0.4), 2)
        health = min(100, round(priority * 0.7 + (50 if initiator == "them" else 30), 2))

        merge_into(handle,
            category=category, orbit=orbit,
            priority_score=priority,
            relationship_health_score=health,
            first_contacted=first, last_contacted=last,
            initiator=initiator, interaction_count=msg_count)
    print(f"relationship_data: {len(rel)} scored")
except Exception as e:
    print(f"relationship_data error: {e}")

# Load business_intelligence.json
try:
    with open(f"{RAW}/business_intelligence.json") as f:
        biz = json.load(f)
    biz_lookup = {}
    for entity, info in biz.items():
        company = info.get("company", entity)
        biz_lookup[company.lower()] = {
            "client_status": info.get("client_status"),
            "business_tags": ",".join(info.get("tags", []))
        }
    # Apply to master records where company matches
    for handle, rec in master.items():
        comp = str(rec.get("company","")).lower()
        if comp in biz_lookup:
            rec.update(biz_lookup[comp])
            if rec.get("category") == "unknown":
                rec["category"] = "business"
    print(f"business_intelligence: {len(biz)} entities applied")
except Exception as e:
    print(f"business_intelligence error: {e}")

# ── STEP 3: Deduplicate by name ──────────────────────────────────
name_to_handle = {}
to_delete = set()
for handle, rec in master.items():
    name = rec.get("name")
    if not name:
        continue
    name_lower = name.lower().strip()
    if name_lower in name_to_handle:
        existing = name_to_handle[name_lower]
        is_email = '@' in handle
        if is_email:
            existing_rec = master[existing]
            if not existing_rec.get("email"):
                existing_rec["email"] = handle
            to_delete.add(handle)
        else:
            existing_is_email = '@' in existing
            if existing_is_email:
                rec["email"] = existing
                to_delete.add(existing)
                name_to_handle[name_lower] = handle
    else:
        name_to_handle[name_lower] = handle

for handle in to_delete:
    del master[handle]
print(f"Deduplication: removed {len(to_delete)} duplicate handles")

# ── STEP 4: Score and rank — keep top 1000 ──────────────────────
scored = sorted(master.values(),
    key=lambda x: float(x.get("priority_score") or 0),
    reverse=True)
top1000 = scored[:1000]
print(f"Total unique contacts: {len(master)} -> keeping top {len(top1000)}")

# ── STEP 5: Assign enrichment tiers ─────────────────────────────
for i, rec in enumerate(top1000):
    rank = i + 1
    if rank <= 10:
        tier = 10
    elif rank <= 25:
        tier = 25
    elif rank <= 100:
        tier = 100
    elif rank <= 500:
        tier = 500
    else:
        tier = 1000
    rec["enrichment_tier"] = tier

# ── STEP 6: Write to contacts.db ────────────────────────────────
conn.execute("DELETE FROM contacts")
conn.commit()

inserted = 0
for rec in top1000:
    try:
        conn.execute("""
            INSERT OR REPLACE INTO contacts (
                handle, name, email, company, title,
                category, orbit, priority_score, relationship_health_score,
                first_contacted, last_contacted, initiator,
                interaction_count, client_status, business_tags,
                enrichment_tier, tags
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            rec.get("handle"),
            rec.get("name"),
            rec.get("email"),
            rec.get("company"),
            rec.get("title"),
            rec.get("category", "unknown"),
            rec.get("orbit", 5),
            rec.get("priority_score", 0),
            rec.get("relationship_health_score", 0),
            rec.get("first_contacted"),
            rec.get("last_contacted"),
            rec.get("initiator"),
            rec.get("interaction_count", 0),
            rec.get("client_status"),
            rec.get("business_tags"),
            rec.get("enrichment_tier", 1000),
            rec.get("category", "unknown")
        ))
        inserted += 1
    except Exception as e:
        print(f"Insert error {rec.get('handle')}: {e}")

conn.commit()

# ── STEP 7: Final stats ──────────────────────────────────────────
total = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
named = conn.execute("SELECT COUNT(*) FROM contacts WHERE name IS NOT NULL AND name != handle").fetchone()[0]
t10 = conn.execute("SELECT COUNT(*) FROM contacts WHERE enrichment_tier=10").fetchone()[0]
t25 = conn.execute("SELECT COUNT(*) FROM contacts WHERE enrichment_tier=25").fetchone()[0]
t100 = conn.execute("SELECT COUNT(*) FROM contacts WHERE enrichment_tier=100").fetchone()[0]
t500 = conn.execute("SELECT COUNT(*) FROM contacts WHERE enrichment_tier=500").fetchone()[0]
biz_count = conn.execute("SELECT COUNT(*) FROM contacts WHERE category='business'").fetchone()[0]
personal = conn.execute("SELECT COUNT(*) FROM contacts WHERE category='personal'").fetchone()[0]

print(f"""
=== CONTACT BRAIN v2 LOADED ===
Total:              {total}
Named:              {named}
Business:           {biz_count}
Personal:           {personal}

Enrichment tiers:
  Tier 10  (hourly live):     {t10}
  Tier 25  (deep enriched):   {t25}
  Tier 100 (AI enriched):     {t100}
  Tier 500 (basic AI):        {t500}
  Tier 1000 (data only):      {total - t10 - t25 - t100 - t500}
""")

print("TOP 10 PRIORITY CONTACTS:")
rows = conn.execute("""
    SELECT name, handle, orbit, priority_score, category, last_contacted, enrichment_tier
    FROM contacts
    ORDER BY priority_score DESC
    LIMIT 10
""").fetchall()
for r in rows:
    print(f"  [{r[6]}] {r[0] or r[1]} | orbit={r[2]} | score={r[3]} | {r[4]} | last={r[5]}")

conn.close()
print("Done.")
