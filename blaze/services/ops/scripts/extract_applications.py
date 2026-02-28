"""
Job Application Extractor — One-time backfill
- Scans caio@astrocleanings.com for job applications
- Sources: Indeed, ZipRecruiter, LinkedIn forwarded emails, direct applicants
- Deduplicates by gmail_message_id
- Imports to Supabase job_applicants table
- State tracked in blaze-data/applicant_extraction.db
"""
import json, re, base64, sqlite3, time, urllib.request, urllib.parse
from datetime import datetime
from google.oauth2 import service_account
import google.auth.transport.requests

SA_FILE     = "/Users/_mxappservice/.gemini/antigravity/playground/perihelion-armstrong/service_account.json"
SUPA_URL    = "https://briokwdoonawhxisbydy.supabase.co"
SUPA_KEY    = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJyaW9rd2Rvb25hd2h4aXNieWR5Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3MTU1Njc2MiwiZXhwIjoyMDg3MTMyNzYyfQ.5V1BsTrqIHGKUUHYJ3PBpL9re_WzKqOzKoQ94dc3me8"
OPENAI_KEY  = json.loads(open("/Users/_mxappservice/.openclaw/agents/main/agent/auth.json").read())["openai"]["key"]
STATE_DB    = "/Users/_mxappservice/blaze-data/applicant_extraction.db"
ACS_BID     = "0ade82e3-ffe9-4c17-ae59-fc4bd198482b"
SCOPES      = ["https://www.googleapis.com/auth/gmail.readonly"]

# Search queries to run against caio@astrocleanings.com
# Each tuple: (query_string, source_label)
SEARCH_QUERIES = [
    ("from:indeed.com", "indeed"),
    ("from:ziprecruiter.com", "ziprecruiter"),
    ("from:linkedin.com apply", "linkedin"),
    ("subject:(job application) OR subject:(applying for)", "email"),
    ("subject:(resume) OR subject:(cover letter) cleaning", "email"),
    ("(I am interested in working) OR (I am applying) cleaning", "email"),
    ("(looking for work) OR (seeking employment) cleaning Houston", "email"),
    ("(housekeeper OR cleaner OR maid) (hire OR apply OR job)", "email"),
]

def get_token(email):
    creds = service_account.Credentials.from_service_account_file(SA_FILE, scopes=SCOPES)
    delegated = creds.with_subject(email)
    request = google.auth.transport.requests.Request()
    delegated.refresh(request)
    return delegated.token

def gmail_get(token, path):
    url = f"https://gmail.googleapis.com/gmail/v1/users/me/{path}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())

def get_message_full(token, msg_id):
    msg = gmail_get(token, f"messages/{msg_id}?format=full")
    headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
    parts = [msg.get("payload", {})]
    body = ""
    while parts:
        part = parts.pop()
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            body += base64.urlsafe_b64decode(part["body"]["data"] + "==").decode("utf-8", errors="replace")
        parts.extend(part.get("parts", []))
    date_ts = int(msg.get("internalDate", 0)) // 1000
    return headers, body[:5000], date_ts

def gpt_classify_application(from_name, from_email, subject, body_text, source):
    prompt = f"""Email received at caio@astrocleanings.com (a residential cleaning company in Houston TX).

From: {from_name} <{from_email}>
Subject: {subject}
Body:
{body_text[:2500]}

Task: Determine if this email is a job application or inquiry about employment.

If YES (it's a job application):
- Extract the applicant's name (use "From" name if not found in body)
- Extract phone number if present in body
- Determine position they're applying for (default "Crew Member" if unclear)
- Write a 1-2 sentence summary of the application

If NO (not a job application — spam, sales, client email, etc.):
- Return is_application: false

Return JSON only:
{{
  "is_application": true or false,
  "applicant_name": "Full Name" or null,
  "applicant_phone": "10-digit string" or null,
  "position": "Crew Member" or "Team Lead" or other,
  "summary": "brief summary" or null
}}"""

    body_req = {
        "model": "gpt-4.1-mini",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 300, "temperature": 0.1,
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(body_req).encode(),
        headers={"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        content = json.loads(r.read())["choices"][0]["message"]["content"].strip()
    if content.startswith("```"):
        content = re.sub(r"^```\w*\n?", "", content).rstrip("`").strip()
    return json.loads(content)

def sb_insert(record):
    url = f"{SUPA_URL}/rest/v1/job_applicants"
    data = json.dumps(record).encode()
    headers = {
        "apikey": SUPA_KEY, "Authorization": f"Bearer {SUPA_KEY}",
        "Content-Type": "application/json", "Prefer": "return=minimal"
    }
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status in (200, 201)
    except urllib.error.HTTPError as e:
        if e.code == 409:  # Duplicate gmail_message_id
            return False
        raise

def init_state_db():
    db = sqlite3.connect(STATE_DB)
    db.execute("""CREATE TABLE IF NOT EXISTS seen_messages (
        msg_id TEXT PRIMARY KEY,
        processed_at TEXT
    )""")
    db.commit()
    return db

def is_seen(db, msg_id):
    return db.execute("SELECT 1 FROM seen_messages WHERE msg_id=?", (msg_id,)).fetchone() is not None

def mark_seen(db, msg_id):
    db.execute("INSERT OR IGNORE INTO seen_messages VALUES (?, datetime('now'))", (msg_id,))
    db.commit()

# ── Main ───────────────────────────────────────────────────────────────────
print("=== Job Application Extractor ===")
print(f"Time: {datetime.now()}\n")

db = init_state_db()
token = get_token("caio@astrocleanings.com")
print("Gmail token OK\n")

seen_msg_ids = set()
all_message_refs = []  # (msg_id, source)

# Collect message IDs across all queries (deduplicate)
for query, source in SEARCH_QUERIES:
    try:
        q = urllib.parse.urlencode({"maxResults": "100", "q": query})
        resp = gmail_get(token, f"messages?{q}")
        msgs = resp.get("messages", [])
        print(f"  Query '{query[:50]}': {len(msgs)} messages")
        for m in msgs:
            if m["id"] not in seen_msg_ids and not is_seen(db, m["id"]):
                seen_msg_ids.add(m["id"])
                all_message_refs.append((m["id"], source))
        time.sleep(0.3)
    except Exception as e:
        print(f"  Query error: {e}")

print(f"\n{len(all_message_refs)} new messages to process\n")

imported = 0
skipped = 0
errors = 0

for msg_id, source in all_message_refs:
    try:
        headers, body, date_ts = get_message_full(token, msg_id)
        from_addr = headers.get("From", "")
        subject = headers.get("Subject", "(no subject)")

        # Extract sender name + email
        from_match = re.match(r"^(.*?)\s*<([\w.+\-]+@[\w.\-]+)>$", from_addr.strip())
        if from_match:
            from_name = from_match.group(1).strip().strip('"')
            from_email = from_match.group(2).lower()
        else:
            from_name = from_addr
            from_email_m = re.search(r"[\w.+\-]+@[\w.\-]+", from_addr)
            from_email = from_email_m.group(0).lower() if from_email_m else from_addr

        # Skip obvious noise
        skip_domains = ["noreply", "no-reply", "mailer-daemon", "notifications@", "donotreply",
                        "bounce", "@indeed.com", "@ziprecruiter.com"]  # platforms send as ATS wrappers
        # For platform emails (indeed/ziprecruiter) we want to keep them — they contain applicant data
        is_platform = any(d in from_email for d in ["@indeed.com", "@ziprecruiter.com", "@linkedin.com"])

        if not is_platform and any(d in from_email for d in ["noreply", "no-reply", "mailer-daemon", "donotreply", "bounce"]):
            mark_seen(db, msg_id)
            skipped += 1
            continue

        if not body or len(body) < 30:
            mark_seen(db, msg_id)
            skipped += 1
            continue

        # GPT classify
        result = gpt_classify_application(from_name, from_email, subject, body, source)
        mark_seen(db, msg_id)

        if not result.get("is_application"):
            skipped += 1
            continue

        # Clean phone
        phone = None
        if result.get("applicant_phone"):
            digits = re.sub(r"\D", "", str(result["applicant_phone"]))
            if len(digits) in (10, 11):
                phone = digits[-10:]

        # Applied date
        applied_at = datetime.fromtimestamp(date_ts).isoformat() if date_ts else datetime.now().isoformat()

        record = {
            "business_id": ACS_BID,
            "name": (result.get("applicant_name") or from_name or "Unknown").strip()[:100],
            "email": from_email[:200],
            "phone": phone,
            "position": (result.get("position") or "Crew Member")[:100],
            "application_text": (result.get("summary") or "")[:2000] + (f"\n\n---\n{body[:1500]}" if body else ""),
            "source": source,
            "status": "pending",
            "gmail_message_id": msg_id,
            "applied_at": applied_at,
            "metadata": {"subject": subject[:200], "from_raw": from_addr[:200]},
        }

        success = sb_insert(record)
        if success:
            imported += 1
            print(f"  ✓ {record['name']} ({from_email}) — {record['position']} [{source}]")
        else:
            skipped += 1

        time.sleep(0.5)

    except Exception as e:
        errors += 1
        print(f"  ✗ Error on {msg_id}: {e}")
        try:
            mark_seen(db, msg_id)
        except:
            pass
        time.sleep(1)

db.close()
print(f"\n=== DONE ===")
print(f"Imported: {imported} | Skipped: {skipped} | Errors: {errors}")
print(f"\nView at: https://briokwdoonawhxisbydy.supabase.co/project/default/editor -> job_applicants")
