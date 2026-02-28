"""
Gmail Signature Enrichment Pipeline
- Scans sent mail from caio@astrocleanings.com for ACS client data
- Scans sent/received mail from bailey@contentco-op.com for CC contact data
- Extracts: addresses, phones, LinkedIn URLs, job titles, company names
- Updates Supabase contacts
"""
import json, re, base64, time, urllib.request, urllib.parse
from google.oauth2 import service_account
import google.auth.transport.requests
import anthropic

SA_FILE = "/Users/_mxappservice/.gemini/antigravity/playground/perihelion-armstrong/service_account.json"
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
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

def get_gmail_token(impersonate):
    creds = service_account.Credentials.from_service_account_file(SA_FILE, scopes=SCOPES)
    delegated = creds.with_subject(impersonate)
    request = google.auth.transport.requests.Request()
    delegated.refresh(request)
    return delegated.token

def gmail_get(token, path):
    url = f"https://gmail.googleapis.com/gmail/v1/users/me/{path}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())

def get_message_body(token, msg_id):
    msg = gmail_get(token, f"messages/{msg_id}?format=full")
    parts = [msg.get("payload", {})]
    text = ""
    while parts:
        part = parts.pop()
        mime = part.get("mimeType", "")
        if mime == "text/plain" and part.get("body", {}).get("data"):
            data = part["body"]["data"]
            text += base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        parts.extend(part.get("parts", []))
    return text[:3000]

def get_headers(token, msg_id):
    msg = gmail_get(token, f"messages/{msg_id}?format=metadata&metadataHeaders=From&metadataHeaders=To&metadataHeaders=Subject")
    return {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}

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

def claude_extract(text, context):
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    sig_text = text[-1500:]
    nl = chr(10)
    user_msg = (
        "Context: " + context + nl + nl
        + "Email text (look for signatures at bottom):" + nl + sig_text + nl + nl
        + "Extract any: phone, address, linkedin_url, title, company. "
        + "Return JSON: {" + "\"phone\": null, "
        + "\"address\": null, \"linkedin_url\": null, "
        + "\"title\": null, \"company\": null}"
    )
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        system="Extract contact information from email signatures. Return compact JSON only, no markdown.",
        messages=[{"role": "user", "content": user_msg}],
        temperature=0.1,
    )
    return response.content[0].text.strip()

def extract_patterns(text):
    """Fast regex extraction without AI for obvious patterns."""
    results = {}
    # Phone
    phones = re.findall(r"(?<!\d)(?:\+1[\s\-.]?)?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}(?!\d)", text)
    if phones:
        clean = re.sub(r"\D", "", phones[0])
        if len(clean) in (10, 11):
            results["phone"] = clean[-10:]
    # LinkedIn
    li = re.findall(r"linkedin\.com/in/[\w\-]+", text, re.IGNORECASE)
    if li: results["linkedin_url"] = "https://www." + li[0]
    # Street address
    addr = re.findall(r"\d{2,5}\s+[A-Z][a-zA-Z].*?(?:St|Ave|Blvd|Dr|Ln|Rd|Way|Ct|Pl|Circle|Loop|Trail|Pkwy|Hwy)\b[^\n]{0,30}", text)
    if addr: results["address"] = addr[0].strip()
    return results

# ── Load Supabase contacts ─────────────────────────────────────────────────
print("Loading Supabase contacts...")
all_contacts = sb("GET", "contacts", params={
    "select": "id,name,phone,email,street_address,company,tags,metadata,ai_summary",
    "limit": 5000,
})
by_email = {}
for c in (all_contacts or []):
    email = (c.get("email") or "").lower().strip()
    if email and "@" in email:
        by_email[email] = c

print(f"  {len(all_contacts)} contacts, {len(by_email)} with email")

# ── CAIO ACCOUNT: Scan sent + inbox for ACS client data ───────────────────
print("\n=== Scanning caio@astrocleanings.com (sent + inbox) ===")
try:
    caio_token = get_gmail_token("caio@astrocleanings.com")
    print("  Token OK")

    # Search both sent and inbox
    sent_resp = gmail_get(caio_token, "messages?maxResults=200&q=in:sent")
    inbox_resp = gmail_get(caio_token, "messages?maxResults=200&q=in:inbox")
    seen_ids = set()
    messages = []
    for m in sent_resp.get("messages", []) + inbox_resp.get("messages", []):
        if m["id"] not in seen_ids:
            seen_ids.add(m["id"])
            messages.append(m)
    print(f"  {len(messages)} messages to scan (sent + inbox, deduped)")

    caio_updates = 0
    for msg_meta in messages[:300]:
        try:
            hdrs = get_headers(caio_token, msg_meta["id"])
            to_addr = hdrs.get("To", "").lower()
            # Extract email from "Name <email>" format
            to_email_match = re.search(r"[\w.+\-]+@[\w.\-]+", to_addr)
            if not to_email_match:
                continue
            to_email = to_email_match.group(0)

            contact = by_email.get(to_email)
            if not contact:
                continue

            # Only update if missing phone or address
            needs_phone = not contact.get("phone")
            needs_addr = not contact.get("street_address") and "Astro Cleanings" in (contact.get("tags") or [])
            if not (needs_phone or needs_addr):
                continue

            body = get_message_body(caio_token, msg_meta["id"])
            if not body or len(body) < 50:
                continue

            patterns = extract_patterns(body)
            update = {}
            if patterns.get("phone") and needs_phone:
                update["phone"] = patterns["phone"]
            if patterns.get("address") and needs_addr:
                update["street_address"] = patterns["address"]

            if update:
                sb("PATCH", f"contacts?id=eq.{contact['id']}", update)
                contact.update(update)
                print(f"  ✓ {contact['name']} ({to_email}): {list(update.keys())}")
                caio_updates += 1

            time.sleep(0.1)
        except Exception as e:
            pass

    print(f"  Caio account: {caio_updates} contacts updated")
except Exception as e:
    print(f"  Caio account error: {e}")

# ── BAILEY ACCOUNT: Scan for CC contact data (phones, titles, LinkedIn) ───
print("\n=== Scanning bailey@contentco-op.com ===")
try:
    bailey_token = get_gmail_token("bailey@contentco-op.com")
    print("  Token OK")

    # Search inbox for emails with signatures
    query = urllib.parse.urlencode({"maxResults": "300", "q": "in:inbox"})
    msgs_resp = gmail_get(bailey_token, f"messages?{query}")
    messages = msgs_resp.get("messages", [])
    print(f"  {len(messages)} messages to scan")

    bailey_updates = 0
    seen_senders = set()

    for msg_meta in messages[:250]:
        try:
            hdrs = get_headers(bailey_token, msg_meta["id"])
            from_addr = hdrs.get("From", "").lower()
            from_email_match = re.search(r"[\w.+\-]+@[\w.\-]+", from_addr)
            if not from_email_match:
                continue
            from_email = from_email_match.group(0)

            # Skip duplicates from same sender
            if from_email in seen_senders:
                continue
            seen_senders.add(from_email)

            contact = by_email.get(from_email)
            if not contact:
                continue

            needs_phone = not contact.get("phone")
            needs_linkedin = not (contact.get("metadata") or {}).get("linkedin_url")
            needs_title = not (contact.get("metadata") or {}).get("title")
            if not (needs_phone or needs_linkedin or needs_title):
                continue

            body = get_message_body(bailey_token, msg_meta["id"])
            if not body or len(body) < 100:
                continue

            # Fast regex first
            patterns = extract_patterns(body)

            # Use Claude only if regex found something or body looks like it has a sig
            has_sig_markers = any(x in body.lower() for x in ["linkedin", "www.", "tel:", "mobile:", "direct:", "c:", "o:"])
            update = {}
            meta_update = contact.get("metadata") or {}

            if patterns.get("phone") and needs_phone:
                update["phone"] = patterns["phone"]
            if patterns.get("linkedin_url") and needs_linkedin:
                meta_update["linkedin_url"] = patterns["linkedin_url"]
            if patterns.get("address"):
                update["street_address"] = patterns["address"]

            # Claude for titles/companies if signature markers found
            if has_sig_markers and (needs_linkedin or needs_title):
                try:
                    result = claude_extract(body, f"Email from {from_addr} to bailey@contentco-op.com")
                    result = result.strip()
                    if result.startswith("```"):
                        result = re.sub(r"^```\w*\n?", "", result).rstrip("`").strip()
                    extracted = json.loads(result)
                    if extracted.get("title") and needs_title:
                        meta_update["title"] = extracted["title"]
                    if extracted.get("company") and not contact.get("company"):
                        update["company"] = extracted["company"]
                    if extracted.get("linkedin_url") and needs_linkedin:
                        meta_update["linkedin_url"] = extracted["linkedin_url"]
                    if extracted.get("phone") and needs_phone and not update.get("phone"):
                        clean_ph = re.sub(r"\D", "", extracted["phone"])
                        if len(clean_ph) in (10, 11):
                            update["phone"] = clean_ph[-10:]
                    time.sleep(0.3)
                except:
                    pass

            if meta_update != (contact.get("metadata") or {}):
                update["metadata"] = meta_update

            if update:
                sb("PATCH", f"contacts?id=eq.{contact['id']}", update)
                contact.update(update)
                fields = [k for k in update if k != "metadata"] + (["linkedin/title"] if "metadata" in update else [])
                print(f"  ✓ {contact['name']} ({from_email}): {fields}")
                bailey_updates += 1

            time.sleep(0.15)
        except Exception as e:
            pass

    print(f"  Bailey account: {bailey_updates} contacts updated")
except Exception as e:
    print(f"  Bailey account error: {e}")

# ── Final stats ───────────────────────────────────────────────────────────
print("\n=== GMAIL ENRICHMENT COMPLETE ===")
final = sb("GET", "contacts", params={
    "select": "id,phone,street_address,tags",
    "limit": 5000,
})
acs = [c for c in (final or []) if "Astro Cleanings" in (c.get("tags") or [])]
cc  = [c for c in (final or []) if "Content Co-op" in (c.get("tags") or [])]
print(f"ACS: {sum(1 for c in acs if c.get('phone'))}/{len(acs)} phone | {sum(1 for c in acs if c.get('street_address'))}/{len(acs)} address")
print(f"CC:  {sum(1 for c in cc if c.get('phone'))}/{len(cc)} phone")
