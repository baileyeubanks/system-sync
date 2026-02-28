"""
Gmail Monitor & Draft Responder
- Monitors bailey@ and blaze@ (contentco-op.com) + caio@ (astrocleanings.com)
- Reads new emails, classifies them via Claude Sonnet
- Writes AI-drafted reply to Drafts (never sends — human reviews)
- Job applications → inserted directly to Supabase job_applicants table
- Tracks seen message IDs in SQLite to avoid re-processing
- Designed to run every 5 minutes via LaunchAgent
"""
import json, re, base64, sqlite3, time, os, urllib.request, urllib.parse
import anthropic
from google.oauth2 import service_account
import google.auth.transport.requests

SA_FILE = "/Users/_mxappservice/.gemini/antigravity/playground/perihelion-armstrong/service_account.json"
STATE_DB = "/Users/_mxappservice/blaze-data/gmail_monitor.db"
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
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly",
          "https://www.googleapis.com/auth/gmail.compose"]

SUPA_URL = "https://briokwdoonawhxisbydy.supabase.co"
SUPA_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJyaW9rd2Rvb25hd2h4aXNieWR5Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3MTU1Njc2MiwiZXhwIjoyMDg3MTMyNzYyfQ.5V1BsTrqIHGKUUHYJ3PBpL9re_WzKqOzKoQ94dc3me8"
ACS_BID  = "0ade82e3-ffe9-4c17-ae59-fc4bd198482b"

ACCOUNTS = [
    {
        "email": "bailey@contentco-op.com",
        "name": "Bailey",
        "business": "Content Co-op",
        "persona": "You are Bailey Eubanks, CMO and co-owner of Content Co-op, a creative agency. You're direct, warm, and professional. Content Co-op helps brands with content strategy, YouTube, and social media.",
    },
    {
        "email": "blaze@contentco-op.com",
        "name": "Blaze (Content Co-op AI)",
        "business": "Content Co-op",
        "persona": "You are Blaze, the AI assistant for Content Co-op. You handle intake, scheduling, and initial client communication professionally.",
    },
    {
        "email": "caio@astrocleanings.com",
        "name": "Caio",
        "business": "Astro Cleaning Services",
        "persona": "You are Caio Gustin, owner of Astro Cleaning Services in Houston TX. You're professional, direct, and run a residential cleaning business. Job applications should be captured — do not draft replies to them.",
        "recruitment": True,  # Job applications → Supabase, not drafts
    },
]

SKIP_SENDERS = [
    "noreply", "no-reply", "mailer-daemon", "notifications@", "notify@",
    "bounce", "support@", "donotreply", "newsletter", "marketing@",
    "subscriptions@", "updates@", "automated@", "info@mailchimp",
    "healthcare.gov", "voeazul", "linkedin.com",
]

def init_db():
    db = sqlite3.connect(STATE_DB)
    db.execute("""CREATE TABLE IF NOT EXISTS seen_messages (
        msg_id TEXT PRIMARY KEY,
        account TEXT,
        processed_at TEXT,
        had_draft INTEGER DEFAULT 0
    )""")
    db.commit()
    return db

def is_seen(db, msg_id):
    return db.execute("SELECT 1 FROM seen_messages WHERE msg_id=?", (msg_id,)).fetchone() is not None

def mark_seen(db, msg_id, account, had_draft=False):
    db.execute("INSERT OR IGNORE INTO seen_messages VALUES (?,?,datetime('now'),?)",
               (msg_id, account, 1 if had_draft else 0))
    db.commit()

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

def gmail_post(token, path, body):
    url = f"https://gmail.googleapis.com/gmail/v1/users/me/{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
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
    return headers, body[:4000]

def should_skip(from_addr, subject):
    from_lower = from_addr.lower()
    subj_lower = (subject or "").lower()
    for skip in SKIP_SENDERS:
        if skip in from_lower:
            return True
    skip_subjects = ["unsubscribe", "receipt", "invoice #", "order confirmation",
                     "your subscription", "password reset", "verify your", "confirm your"]
    for s in skip_subjects:
        if s in subj_lower:
            return True
    return False

def claude_classify_and_draft(account, from_name, from_email, subject, body, persona):
    prompt = f"""You are helping {account['name']} manage their inbox for {account['business']}.

Email received:
From: {from_name} <{from_email}>
Subject: {subject}
Body:
{body[:2000]}

Task:
1. Classify: ["client_inquiry", "partnership", "vendor", "job_application", "existing_client", "spam_or_noise", "other"]
2. Priority: ["high", "medium", "low", "skip"]
3. If priority is NOT "skip", draft a professional reply (200 words max).
4. If priority is "skip" or classification is "spam_or_noise", set draft to null.

{persona}

Return JSON only:
{{
  "classification": "...",
  "priority": "...",
  "summary": "one sentence of what this email is about",
  "draft_subject": "Re: {subject}",
  "draft_body": "..." or null
}}"""

    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )
    content = response.content[0].text.strip()
    if content.startswith("```"):
        content = re.sub(r"^```\w*\n?", "", content).rstrip("`").strip()
    return json.loads(content)

def create_draft(token, to_email, to_name, from_email, subject, body_text):
    """Create a Gmail draft."""
    msg_lines = [
        f"From: {from_email}",
        f"To: {to_name} <{to_email}>",
        f"Subject: {subject}",
        "Content-Type: text/plain; charset=utf-8",
        "MIME-Version: 1.0",
        "",
        body_text,
    ]
    raw_msg = "\r\n".join(msg_lines)
    encoded = base64.urlsafe_b64encode(raw_msg.encode("utf-8")).decode("utf-8")
    result = gmail_post(token, "drafts", {"message": {"raw": encoded}})
    return result.get("id")

def insert_applicant(from_name, from_email, subject, body_text, msg_id, source="email"):
    """Insert a job application into Supabase job_applicants table."""
    # Clean phone from body
    phone = None
    phone_match = re.search(r"(?<!\d)(?:\+1[\s\-.]?)?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}(?!\d)", body_text)
    if phone_match:
        digits = re.sub(r"\D", "", phone_match.group(0))
        if len(digits) in (10, 11):
            phone = digits[-10:]

    record = {
        "business_id": ACS_BID,
        "name": (from_name or "Unknown").strip()[:100],
        "email": from_email.lower()[:200],
        "phone": phone,
        "position": "Crew Member",
        "application_text": (f"Subject: {subject}\n\n{body_text}")[:2000],
        "source": source,
        "status": "pending",
        "gmail_message_id": msg_id,
        "metadata": {"subject": subject[:200]},
    }

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
        if e.code == 409:
            return False  # Duplicate — already imported
        raise


def gmail_archive(token, msg_id):
    """Remove message from INBOX + mark as read. Inbox = action items only."""
    url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}/modify"
    body = {"removeLabelIds": ["INBOX", "UNREAD"]}
    try:
        gmail_post(token, f"messages/{msg_id}/modify", body)
    except Exception as e:
        print(f"    [ARCHIVE] Failed: {e}")


def send_high_priority_alert(account_email, from_name, from_email_addr, subject, summary, classification):
    """Push real-time Telegram alert for high-priority emails (fires within 5 min of arrival)."""
    import subprocess, os
    emoji = "🔴" if classification in ("client_inquiry", "existing_client") else "📧"
    label = account_email.split("@")[0].upper()
    msg = (
        f"{emoji} [BLAZE] HIGH-PRIORITY [{label}]\n"
        f"From: {from_name} <{from_email_addr}>\n"
        f"Subject: {subject}\n"
        f"{summary}"
    )
    openclaw = "/usr/local/bin/openclaw"
    env = dict(os.environ)
    env["PATH"] = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
    try:
        subprocess.Popen(
            [openclaw, "message", "send", "--channel", "telegram",
             "--account", "main", "--target", "telegram:7747110667",
             "--message", msg],
            env=env, close_fds=True,
        )
        if "astrocleanings" in account_email:
            caio_msg = (
                f"{emoji} [ASTRO] New email from {from_name}\n"
                f"Subject: {subject}\n{summary}"
            )
            subprocess.Popen(
                [openclaw, "message", "send", "--channel", "telegram",
                 "--account", "astro", "--target", "telegram:7124538299",
                 "--message", caio_msg],
                env=env, close_fds=True,
            )
        print(f"    [ALERT] Telegram fired for {from_name}")
    except Exception as e:
        print(f"    [ALERT] Telegram failed: {e}")

# ── Main loop ─────────────────────────────────────────────────────────────
db = init_db()
total_drafted = 0
total_seen = 0
total_applicants = 0

for account in ACCOUNTS:
    email = account["email"]
    is_recruitment = account.get("recruitment", False)
    print(f"\n=== {email} ===")
    try:
        token = get_token(email)
        query = urllib.parse.urlencode({"maxResults": "50", "q": "in:inbox is:unread newer_than:7d"})
        msgs_resp = gmail_get(token, f"messages?{query}")
        messages = msgs_resp.get("messages", [])
        new_msgs = [m for m in messages if not is_seen(db, m["id"])]
        print(f"  {len(messages)} unread, {len(new_msgs)} new to process")

        for msg_meta in new_msgs:
            msg_id = msg_meta["id"]
            try:
                headers, body = get_message_full(token, msg_id)
                from_addr = headers.get("From", "")
                subject = headers.get("Subject", "(no subject)")

                from_match = re.match(r"^(.*?)\s*<([\w.+\-]+@[\w.\-]+)>$", from_addr.strip())
                if from_match:
                    from_name = from_match.group(1).strip().strip('"')
                    from_email_addr = from_match.group(2)
                else:
                    from_name = from_addr
                    from_email_addr = re.search(r"[\w.+\-]+@[\w.\-]+", from_addr)
                    from_email_addr = from_email_addr.group(0) if from_email_addr else from_addr

                if should_skip(from_email_addr, subject):
                    gmail_archive(token, msg_id)
                    mark_seen(db, msg_id, email)
                    total_seen += 1
                    continue

                print(f"  → {from_name} | {subject[:50]}")

                result = claude_classify_and_draft(account, from_name, from_email_addr, subject, body, account["persona"])

                priority = result.get("priority", "low")
                classification = result.get("classification", "other")
                summary = result.get("summary", "")
                draft_body = result.get("draft_body")
                draft_subject = result.get("draft_subject", f"Re: {subject}")

                had_draft = False

                # Job applications → Supabase (all accounts)
                if classification == "job_application":
                    src = "indeed" if "indeed" in from_email_addr else \
                          "ziprecruiter" if "ziprecruiter" in from_email_addr else \
                          "linkedin" if "linkedin" in from_email_addr else "email"
                    inserted = insert_applicant(from_name, from_email_addr, subject, body, msg_id, src)
                    print(f"    [JOB APP] inserted={inserted} src={src} from {from_email_addr}")
                    total_applicants += 1
                    # Draft only for direct applicants on non-recruitment accounts — not platform relays
                    is_platform_relay = src in ("indeed", "ziprecruiter", "linkedin")
                    if not is_recruitment and not is_platform_relay and draft_body and priority not in ("skip",):
                        draft_id = create_draft(token, from_email_addr, from_name, email, draft_subject, draft_body)
                        had_draft = True

                elif draft_body and priority not in ("skip",) and not is_recruitment:
                    draft_id = create_draft(token, from_email_addr, from_name, email, draft_subject, draft_body)
                    had_draft = True
                    print(f"    [{priority.upper()}] {classification} — draft created (id={draft_id})")
                else:
                    print(f"    [{priority.upper()}] {classification} — skipped ({summary[:60]})")

                # Real-time alert for high-priority emails
                if priority == "high":
                    send_high_priority_alert(email, from_name, from_email_addr, subject, summary, classification)

                # Auto-archive spam/noise — inbox stays clean (action items only)
                if classification == "spam_or_noise" or priority == "skip":
                    gmail_archive(token, msg_id)
                    print(f"    [ARCHIVED] {classification} / {priority}")

                mark_seen(db, msg_id, email, had_draft)
                if had_draft:
                    total_drafted += 1
                time.sleep(0.5)

            except Exception as e:
                print(f"    Error on {msg_id}: {e}")
                mark_seen(db, msg_id, email)

    except Exception as e:
        print(f"  Account error: {e}")

db.close()
print(f"\n=== DONE: {total_drafted} drafts created, {total_seen} auto-skipped, {total_applicants} applicants captured ===")
