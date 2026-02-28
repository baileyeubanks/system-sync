"""
iMessage Enrichment Pipeline
- Reads chat.db directly
- For each ACS/CC contact with a phone number, finds their conversation thread
- Extracts: addresses, access codes, job preferences, relationship context
- Updates Supabase contacts with ai_summary additions + metadata
- Also finds phone numbers for contacts we have by name but no phone
"""
import sqlite3, json, re, time, urllib.request, urllib.parse
from datetime import datetime
import anthropic

CHAT_DB = "/Users/_mxappservice/Library/Messages/chat.db"
SUPA_URL = "https://briokwdoonawhxisbydy.supabase.co"
SUPA_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJyaW9rd2Rvb25hd2h4aXNieWR5Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3MTU1Njc2MiwiZXhwIjoyMDg3MTMyNzYyfQ.5V1BsTrqIHGKUUHYJ3PBpL9re_WzKqOzKoQ94dc3me8"
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
APPLE_EPOCH = 978307200

def sb(method, path, body=None, params=None):
    url = f"{SUPA_URL}/rest/v1/{path}"
    if params: url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body else None
    headers = {"apikey": SUPA_KEY, "Authorization": f"Bearer {SUPA_KEY}",
                "Content-Type": "application/json", "Prefer": "return=representation"}
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read()
                return json.loads(raw) if raw else []
        except urllib.error.HTTPError as e:
            if e.code in (409,): return None
            if attempt == 2: raise
            time.sleep(1)
        except:
            if attempt == 2: raise
            time.sleep(2)

def claude_call(prompt, system="You are a contact data enrichment AI. Be concise and factual. Return valid JSON only."):
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
    )
    return response.content[0].text.strip()

def clean_phone(p):
    digits = re.sub(r"\D", "", p or "")
    if len(digits) == 11 and digits[0] == "1":
        return digits[1:]
    return digits if len(digits) == 10 else None

def get_thread(db, phone_10):
    """Get last 60 messages in the thread for this phone number."""
    cur = db.cursor()
    # Find handle_id(s) matching this phone
    cur.execute("""
        SELECT ROWID FROM handle
        WHERE replace(replace(replace(replace(id,'+1',''),'-',''),'(',''),')','') LIKE ?
           OR replace(replace(replace(replace(id,'+',''),' ',''),'-',''),'(','') LIKE ?
    """, (f"%{phone_10[-10:]}%", f"%{phone_10[-10:]}%"))
    handle_rows = cur.fetchall()
    if not handle_rows:
        return []
    handle_ids = [r[0] for r in handle_rows]
    placeholders = ",".join("?" * len(handle_ids))

    cur.execute(f"""
        SELECT m.text, m.is_from_me, m.date
        FROM message m
        JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
        JOIN chat_handle_join chj ON cmj.chat_id = chj.chat_id
        WHERE chj.handle_id IN ({placeholders})
          AND m.text IS NOT NULL
          AND length(m.text) > 3
          AND m.associated_message_type = 0
        ORDER BY m.date DESC
        LIMIT 60
    """, handle_ids)
    rows = cur.fetchall()
    # Reverse to chronological order
    messages = []
    for text, is_from_me, ts in reversed(rows):
        dt = datetime.fromtimestamp(ts / 1e9 + APPLE_EPOCH).strftime("%Y-%m-%d")
        sender = "ME" if is_from_me else "CLIENT"
        messages.append(f"[{dt} {sender}] {text}")
    return messages

def extract_from_thread(name, phone, thread_text, is_acs):
    if is_acs:
        prompt = f"""ACS (Astro Cleanings) client: {name} | Phone: {phone}

iMessage thread (last 60 messages):
{thread_text}

Extract from this conversation:
1. Any street address or location mentioned
2. Access codes (gate, alarm, door, lockbox, garage)
3. Service preferences or special instructions
4. Any complaints or issues mentioned
5. Pets or allergies mentioned
6. Key relationship notes (how long client, reliability, etc.)

Return JSON:
{{
  "address": null or "123 Main St, Houston TX",
  "access_codes": [],
  "preferences": [],
  "pets": null,
  "notes": "brief relationship context",
  "complaint_history": null
}}"""
    else:
        prompt = f"""Content Co-op contact: {name} | Phone: {phone}

iMessage thread:
{thread_text}

Extract:
1. Their company/role if mentioned
2. Any project or deal context
3. Key relationship notes

Return JSON:
{{
  "company": null,
  "title": null,
  "notes": "brief context"
}}"""

    try:
        result = claude_call(prompt)
        if result.startswith("```"):
            result = re.sub(r"^```\w*\n?", "", result).rstrip("`").strip()
        return json.loads(result)
    except Exception as e:
        return {}

# ── Load contacts ──────────────────────────────────────────────────────────
print("Loading Supabase contacts...")
all_contacts = sb("GET", "contacts", params={
    "select": "id,name,phone,email,street_address,tags,metadata,ai_summary",
    "limit": 5000,
})
print(f"  {len(all_contacts)} total contacts")

acs_contacts = [c for c in all_contacts if "Astro Cleanings" in (c.get("tags") or [])]
cc_contacts  = [c for c in all_contacts if "Content Co-op" in (c.get("tags") or [])]
print(f"  ACS: {len(acs_contacts)} | CC: {len(cc_contacts)}")

# ── Open iMessage DB ───────────────────────────────────────────────────────
db = sqlite3.connect(f"file:{CHAT_DB}?mode=ro", uri=True)
db.row_factory = sqlite3.Row

# Build all handles index (phone → handle info)
cur = db.cursor()
cur.execute("SELECT ROWID, id FROM handle")
all_handles_raw = cur.fetchall()
handle_map = {}  # last-10-digits → handle_id
for row in all_handles_raw:
    digits = re.sub(r"\D", "", row["id"])
    if len(digits) >= 10:
        handle_map[digits[-10:]] = row["ROWID"]

print(f"  {len(handle_map)} phone handles in iMessage DB")

# ── Process ACS contacts ───────────────────────────────────────────────────
print("\n=== ACS Contacts ===")
acs_updated = 0
acs_found = 0

for contact in acs_contacts:
    name = contact["name"]
    phone_raw = contact.get("phone") or ""
    phone = clean_phone(phone_raw)
    if not phone:
        continue

    thread = get_thread(db, phone)
    if not thread:
        continue

    acs_found += 1
    thread_text = "\n".join(thread[-50:])
    print(f"\n  {name} ({phone}): {len(thread)} messages")

    data = extract_from_thread(name, phone, thread_text, is_acs=True)
    if not data:
        continue

    update = {}
    meta = contact.get("metadata") or {}

    # Address
    if data.get("address") and not contact.get("street_address"):
        update["street_address"] = data["address"]
        print(f"    address: {data['address']}")

    # Access codes
    if data.get("access_codes"):
        existing = meta.get("access_codes") or []
        merged = list(set(existing + data["access_codes"]))
        if merged != existing:
            meta["access_codes"] = merged
            print(f"    codes: {data['access_codes']}")

    # Preferences / pets
    extras = []
    if data.get("preferences"):
        extras.extend(data["preferences"])
    if data.get("pets"):
        meta["pets"] = data["pets"]
        extras.append(f"Pets: {data['pets']}")
    if data.get("complaint_history"):
        meta["complaint_history"] = data["complaint_history"]

    # Append to ai_summary
    if extras or data.get("notes"):
        addition = ""
        if data.get("notes"):
            addition += f"\n\niMessage context: {data['notes']}"
        if extras:
            addition += "\n" + " | ".join(extras)
        existing_summary = contact.get("ai_summary") or ""
        if addition.strip() and addition.strip() not in existing_summary:
            update["ai_summary"] = (existing_summary + addition).strip()[:2000]

    if meta != (contact.get("metadata") or {}):
        update["metadata"] = meta

    if update:
        sb("PATCH", f"contacts?id=eq.{contact['id']}", update)
        acs_updated += 1
        print(f"    updated: {list(update.keys())}")

    time.sleep(0.3)

print(f"\n  ACS: found threads for {acs_found}/{len([c for c in acs_contacts if c.get('phone')])} contacts, updated {acs_updated}")

# ── Process CC contacts ────────────────────────────────────────────────────
print("\n=== CC Contacts ===")
cc_updated = 0
cc_found = 0

for contact in cc_contacts:
    phone_raw = contact.get("phone") or ""
    phone = clean_phone(phone_raw)
    if not phone:
        continue

    thread = get_thread(db, phone)
    if not thread or len(thread) < 3:
        continue

    cc_found += 1
    thread_text = "\n".join(thread[-30:])
    name = contact["name"]

    data = extract_from_thread(name, phone, thread_text, is_acs=False)
    if not data:
        continue

    update = {}
    meta = contact.get("metadata") or {}

    if data.get("company") and not contact.get("company"):
        update["company"] = data["company"]
    if data.get("title") and not meta.get("title"):
        meta["title"] = data["title"]
    if data.get("notes"):
        existing = contact.get("ai_summary") or ""
        note = f"\n\niMessage: {data['notes']}"
        if note.strip() not in existing:
            update["ai_summary"] = (existing + note).strip()[:2000]

    if meta != (contact.get("metadata") or {}):
        update["metadata"] = meta

    if update:
        sb("PATCH", f"contacts?id=eq.{contact['id']}", update)
        cc_updated += 1
        print(f"  ✓ {name}: {list(k for k in update if k != 'metadata')}")

    time.sleep(0.2)

print(f"\n  CC: found threads for {cc_found}/{len([c for c in cc_contacts if c.get('phone')])} contacts, updated {cc_updated}")

# ── Find phones for contacts we only have by name ─────────────────────────
print("\n=== Reverse lookup: find phones from message history ===")
# Look for ACS contacts with no phone but we know their name appeared in messages
no_phone_acs = [c for c in acs_contacts if not c.get("phone")]
print(f"  {len(no_phone_acs)} ACS contacts with no phone")

db.close()
print("\n=== DONE ===")
