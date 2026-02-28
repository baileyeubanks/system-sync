#!/usr/bin/env python3
"""
Contact Brain Full Re-Merge v3
- Loads all sources: contact-matches, contacts_merged, relationship_data, business_intel
- Smarter scoring: Gmail + iMessage + has_name + has_company + has_email all count
- Deduplicates by name AND by email/phone cross-reference
- Keeps top 500, assigns tiers
"""
import json, sqlite3, re
from datetime import datetime

DATA_ROOT = "/Users/_mxappservice/blaze-data"
RAW = "%s/contacts/raw" % DATA_ROOT
DB = "%s/blaze.db" % DATA_ROOT

conn = sqlite3.connect(DB)
conn.execute("PRAGMA journal_mode=WAL")
conn.row_factory = sqlite3.Row

# ── Preserve Gmail data already in DB before we clear it ────────
print("Preserving existing Gmail data...")
existing = {}
for row in conn.execute("SELECT * FROM contacts"):
    rec = dict(row)
    key = rec.get("email") or rec.get("phone") or rec.get("handle")
    if key:
        existing[key] = rec
print(f"Preserved {len(existing)} existing records")

# ── Clear and rebuild ────────────────────────────────────────────
conn.execute("DELETE FROM contacts")
conn.commit()

# ── Master dict: key = phone or email ───────────────────────────
master = {}

JUNK_PATTERNS = [
    r'iMessage sender', r'Zelle', r'account to account',
    r'Smoke Weed', r'Festival', r'Filler', r'Haircut',
    r'Reminder', r'TODO', r'Complete the', r'Your ',
    r'squad', r'crew', r'the boys', r'noreply', r'no-reply',
    r'automated', r'notification', r'alert', r'support@',
    r'info@', r'hello@', r'team@', r'billing@',
]

def is_junk(name):
    if not name: return False
    if len(name) < 2 or len(name) > 60: return True
    for p in JUNK_PATTERNS:
        if re.search(p, name, re.I):
            return True
    return False

def merge_into(key, **kwargs):
    if not key: return
    if key not in master:
        master[key] = {"_key": key}
    for k, v in kwargs.items():
        if v is not None and v != '' and not master[key].get(k):
            master[key][k] = v

def score_contact(rec):
    score = 0
    # Data completeness baseline (ensures named contacts rank above blanks)
    if rec.get("name"): score += 20
    if rec.get("email"): score += 10
    if rec.get("company"): score += 10
    # iMessage score
    score += float(rec.get("imessage_score") or 0) * 0.4
    # Gmail score
    score += float(rec.get("gmail_score") or 0) * 0.4
    # Business boost
    if rec.get("client_status") in ("active-client",): score += 15
    if rec.get("category") == "business": score += 5
    return round(min(100, score), 2)

# ── SOURCE 1: contact-matches.json ──────────────────────────────
try:
    with open(f"{RAW}/contact-matches.json") as f:
        matches = json.load(f)
    for handle, info in matches.items():
        name = info.get("name")
        if is_junk(name): continue
        merge_into(handle, name=name, source="contact-matches")
    print(f"contact-matches: {len(matches)} loaded")
except Exception as e:
    print(f"contact-matches error: {e}")

# ── SOURCE 2: contacts_merged.json ──────────────────────────────
try:
    with open(f"{RAW}/contacts_merged.json") as f:
        merged = json.load(f)
    count = 0
    for handle, info in merged.items():
        name = info.get("name")
        email = info.get("email")
        phone = info.get("phone")
        company = info.get("company")
        title = info.get("title")
        if is_junk(name): continue
        if not any([name, email, company]): continue
        key = phone if phone else (email if email else handle)
        merge_into(key, name=name, email=email, phone=phone,
                   company=company, title=title)
        # Cross-index email
        if email and email != key:
            merge_into(email, name=name, email=email, phone=phone,
                       company=company, title=title)
        count += 1
    print(f"contacts_merged: {count} processed")
except Exception as e:
    print(f"contacts_merged error: {e}")

# ── SOURCE 3: relationship_data.json (iMessage scores) ──────────
try:
    with open(f"{RAW}/relationship_data.json") as f:
        rel = json.load(f)
    count = 0
    for handle, info in rel.items():
        msg_count = info.get("msg_count", 0)
        last = info.get("last_contact")
        first = info.get("first_contact")
        initiator = info.get("initiator", "unknown")
        category = info.get("relationship_category", "unknown")

        recency = 0
        orbit = 5
        if last:
            try:
                dt = datetime.fromisoformat(last.replace('Z',''))
                days = (datetime.utcnow() - dt).days
                recency = max(0, 100 - days * 0.3)
                orbit = 1 if days<=7 else 2 if days<=30 else 3 if days<=90 else 4 if days<=180 else 5
            except: pass

        imessage_score = round((recency*0.6) + (min(100,msg_count*1.5)*0.4), 2)

        merge_into(handle,
            imessage_score=imessage_score,
            orbit=orbit,
            first_contacted=first,
            last_contacted=last,
            initiator=initiator,
            interaction_count=msg_count,
            category=category)
        count += 1
    print(f"relationship_data: {count} scored")
except Exception as e:
    print(f"relationship_data error: {e}")

# ── SOURCE 4: business_intelligence.json ────────────────────────
try:
    with open(f"{RAW}/business_intelligence.json") as f:
        biz = json.load(f)
    biz_map = {}
    for entity, info in biz.items():
        company = info.get("company", entity)
        biz_map[company.lower()] = {
            "client_status": info.get("client_status"),
            "business_tags": ",".join(info.get("tags",[])),
        }
    for key, rec in master.items():
        comp = str(rec.get("company","")).lower()
        if comp and comp in biz_map:
            rec.update(biz_map[comp])
            if rec.get("category","unknown") == "unknown":
                rec["category"] = "business"
    print(f"business_intelligence: {len(biz)} entities applied")
except Exception as e:
    print(f"business_intelligence error: {e}")

# ── SOURCE 5: Existing Gmail data from DB ───────────────────────
gmail_merged = 0
for key, rec in existing.items():
    gmail_score = rec.get("priority_score", 0)
    email = rec.get("email")
    name = rec.get("name")
    if is_junk(name): continue
    # Find match in master
    matched = False
    for mkey in [key, email, rec.get("phone")]:
        if mkey and mkey in master:
            master[mkey]["gmail_score"] = gmail_score
            if email: master[mkey]["email"] = master[mkey].get("email") or email
            if name: master[mkey]["name"] = master[mkey].get("name") or name
            matched = True
            break
    if not matched and (name or email):
        k = email or key
        merge_into(k, name=name, email=email,
                   phone=rec.get("phone"),
                   gmail_score=gmail_score,
                   company=rec.get("company"),
                   last_contacted=rec.get("last_contacted"),
                   category=rec.get("category","unknown"))
        gmail_merged += 1
print(f"Existing Gmail records merged: {gmail_merged} new + existing blended")

# ── Deduplicate by name ──────────────────────────────────────────
name_map = {}
dupes = set()
for key, rec in master.items():
    name = str(rec.get("name","")).lower().strip()
    if not name or len(name) < 2: continue
    if name in name_map:
        existing_key = name_map[name]
        # Keep phone handle over email handle
        if '@' in key:
            master[existing_key]["email"] = master[existing_key].get("email") or key
            dupes.add(key)
        else:
            master[key]["email"] = master[key].get("email") or existing_key
            dupes.add(existing_key)
            name_map[name] = key
    else:
        name_map[name] = key

for d in dupes:
    master.pop(d, None)
print(f"Deduplication: removed {len(dupes)} duplicate handles")

# ── Score everyone ───────────────────────────────────────────────
for key, rec in master.items():
    rec["priority_score"] = score_contact(rec)

# ── Top 500 ─────────────────────────────────────────────────────
ranked = sorted(master.values(),
    key=lambda x: x.get("priority_score",0), reverse=True)
top500 = [r for r in ranked if not is_junk(r.get("name")) ][:500]
print(f"Total unique: {len(master)} -> keeping top {len(top500)}")

# ── Assign tiers ────────────────────────────────────────────────
for i, rec in enumerate(top500):
    rank = i + 1
    rec["enrichment_tier"] = (10 if rank<=10 else 25 if rank<=25
                               else 100 if rank<=100 else 500)

# ── Insert into DB ───────────────────────────────────────────────
inserted = 0
for rec in top500:
    try:
        conn.execute("""
            INSERT OR REPLACE INTO contacts (
                handle, name, email, phone, company, title,
                category, orbit, priority_score, relationship_health_score,
                first_contacted, last_contacted, initiator,
                interaction_count, client_status, business_tags,
                enrichment_tier, tags
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            rec.get("_key"), rec.get("name"),
            rec.get("email"), rec.get("phone"),
            rec.get("company"), rec.get("title"),
            rec.get("category","unknown"),
            rec.get("orbit", 5),
            rec.get("priority_score", 0),
            min(100, (rec.get("priority_score",0)*0.7 +
                (50 if rec.get("initiator")=="them" else 30))),
            rec.get("first_contacted"),
            rec.get("last_contacted"),
            rec.get("initiator"),
            rec.get("interaction_count", 0),
            rec.get("client_status"),
            rec.get("business_tags"),
            rec.get("enrichment_tier", 500),
            rec.get("category","unknown")
        ))
        inserted += 1
    except Exception as e:
        print(f"Insert error {rec.get('_key')}: {e}")

conn.commit()

# ── Final stats ──────────────────────────────────────────────────
total = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
named = conn.execute("SELECT COUNT(*) FROM contacts WHERE name IS NOT NULL AND length(name)>2").fetchone()[0]
t10  = conn.execute("SELECT COUNT(*) FROM contacts WHERE enrichment_tier=10").fetchone()[0]
t25  = conn.execute("SELECT COUNT(*) FROM contacts WHERE enrichment_tier=25").fetchone()[0]
t100 = conn.execute("SELECT COUNT(*) FROM contacts WHERE enrichment_tier=100").fetchone()[0]
t500 = conn.execute("SELECT COUNT(*) FROM contacts WHERE enrichment_tier=500").fetchone()[0]
biz  = conn.execute("SELECT COUNT(*) FROM contacts WHERE category='business'").fetchone()[0]
active = conn.execute("SELECT COUNT(*) FROM contacts WHERE client_status='active-client'").fetchone()[0]

print(f"""
=== CONTACT BRAIN v3 ===
Total:           {total}
Named:           {named}
Business:        {biz}
Active clients:  {active}

Tiers:
  T10  (live/hourly):   {t10}
  T25  (deep):          {t25}
  T100 (enriched):      {t100}
  T500 (basic AI):      {t500}
""")

print("TOP 15:")
rows = conn.execute("""
    SELECT name, phone, email, priority_score, orbit, enrichment_tier, category, client_status
    FROM contacts ORDER BY priority_score DESC LIMIT 15
""").fetchall()
for r in rows:
    print(f"  [{r[5]}] {r[0] or r[1] or r[2]} | score={r[3]} | orbit={r[4]} | {r[6]} | {r[7] or ''}")

conn.close()
print("Done.")
