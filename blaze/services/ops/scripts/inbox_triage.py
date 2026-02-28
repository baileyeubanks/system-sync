"""
inbox_triage.py — One-shot inbox cleanup
- Scans ALL current inbox messages across all 3 accounts (up to 150 each)
- Archives spam, noise, newsletters, completed items
- Keeps genuine action items in inbox
- Sends Bailey a Telegram summary of what's left + what was archived
Run once: python3 inbox_triage.py
"""
import json, re, base64, sqlite3, time, os, subprocess, urllib.request, urllib.parse

SA_FILE  = "/Users/_mxappservice/.gemini/antigravity/playground/perihelion-armstrong/service_account.json"
STATE_DB = "/Users/_mxappservice/blaze-data/gmail_monitor.db"
OPENAI_KEY = json.loads(open("/Users/_mxappservice/.openclaw/agents/main/agent/auth.json").read())["openai"]["key"]

from google.oauth2 import service_account
import google.auth.transport.requests

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.modify",
]

ACCOUNTS = [
    {"email": "bailey@contentco-op.com",  "name": "Bailey",                "business": "Content Co-op"},
    {"email": "blaze@contentco-op.com",   "name": "Blaze (Content Co-op)", "business": "Content Co-op"},
    {"email": "caio@astrocleanings.com",  "name": "Caio",                  "business": "Astro Cleaning Services"},
]

SKIP_SENDERS = [
    "noreply", "no-reply", "mailer-daemon", "notifications@", "notify@",
    "bounce", "donotreply", "newsletter", "marketing@", "subscriptions@",
    "updates@", "automated@", "info@mailchimp", "healthcare.gov",
    "linkedin.com", "indeed.com", "ziprecruiter.com",
]
SKIP_SUBJECTS = [
    "unsubscribe", "receipt", "invoice #", "order confirmation",
    "your subscription", "password reset", "verify your", "confirm your",
    "weekly digest", "daily digest", "monthly newsletter",
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

def gmail_post(token, path, body):
    url = f"https://gmail.googleapis.com/gmail/v1/users/me/{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())

def gmail_archive(token, msg_id):
    try:
        gmail_post(token, f"messages/{msg_id}/modify", {"removeLabelIds": ["INBOX", "UNREAD"]})
    except Exception as e:
        print(f"    Archive error: {e}")

def get_message_headers(token, msg_id):
    msg = gmail_get(token, f"messages/{msg_id}?format=metadata&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date")
    headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
    return headers

def get_message_snippet(token, msg_id):
    msg = gmail_get(token, f"messages/{msg_id}?format=minimal")
    return msg.get("snippet", "")

def quick_skip(from_addr, subject):
    from_lower = from_addr.lower()
    subj_lower = (subject or "").lower()
    for skip in SKIP_SENDERS:
        if skip in from_lower:
            return True
    for s in SKIP_SUBJECTS:
        if s in subj_lower:
            return True
    return False

def gpt_triage(account, from_name, from_email_addr, subject, snippet):
    prompt = f"""You are triaging the inbox for {account['name']} at {account['business']}.

Email:
From: {from_name} <{from_email_addr}>
Subject: {subject}
Preview: {snippet[:500]}

Classify this email:
- "action_required": Needs a reply or specific action from {account['name']} that has NOT been completed
- "fyi_keep": Important to know about, no action needed but worth keeping
- "spam_or_noise": Promotional, automated, irrelevant, or already-handled notification
- "job_application": Applicant for a job position

Priority if not noise: "high" (from known contact, urgent), "medium", "low"

Return JSON only:
{{"classification": "...", "priority": "...", "summary": "one sentence", "archive": true/false}}

Archive = true if: spam, promotional, automated notification, receipt/confirmation already handled, newsletter
Archive = false if: actual human email that needs attention or is informative"""

    body_req = {
        "model": "gpt-4.1-mini",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 200, "temperature": 0.2,
    }
    req = urllib.request.Request("https://api.openai.com/v1/chat/completions",
        data=json.dumps(body_req).encode(),
        headers={"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        content = json.loads(r.read())["choices"][0]["message"]["content"].strip()
    if content.startswith("```"):
        content = re.sub(r"^```\w*\n?", "", content).rstrip("`").strip()
    return json.loads(content)

def send_telegram(account_id, target, message):
    env = dict(os.environ)
    env["PATH"] = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
    subprocess.Popen(
        ["/usr/local/bin/openclaw", "message", "send",
         "--channel", "telegram", "--account", account_id,
         "--target", target, "--message", message],
        env=env, close_fds=True,
    )

# ── Main ────────────────────────────────────────────────────────────────────
print("=== INBOX TRIAGE — Blaze V4 ===\n")

db = sqlite3.connect(STATE_DB)
db.execute("""CREATE TABLE IF NOT EXISTS seen_messages (
    msg_id TEXT PRIMARY KEY, account TEXT, processed_at TEXT, had_draft INTEGER DEFAULT 0
)""")
db.commit()

all_action_items = []
total_archived = 0
total_kept = 0

for account in ACCOUNTS:
    email = account["email"]
    print(f"\n=== {email} ===")
    try:
        token = get_token(email)
        query = urllib.parse.urlencode({"maxResults": "150", "q": "in:inbox", "labelIds": "INBOX"})
        msgs_resp = gmail_get(token, f"messages?{query}")
        messages = msgs_resp.get("messages", [])
        print(f"  {len(messages)} messages in inbox")

        action_items = []
        archived_count = 0

        for msg_meta in messages[:150]:
            msg_id = msg_meta["id"]
            try:
                headers = get_message_headers(token, msg_id)
                from_addr = headers.get("From", "")
                subject   = headers.get("Subject", "(no subject)")
                date      = headers.get("Date", "")

                from_match = re.match(r"^(.*?)\s*<([\w.+\-]+@[\w.\-]+)>$", from_addr.strip())
                if from_match:
                    from_name      = from_match.group(1).strip().strip('"')
                    from_email_addr = from_match.group(2)
                else:
                    from_name      = from_addr
                    from_email_addr = re.search(r"[\w.+\-]+@[\w.\-]+", from_addr)
                    from_email_addr = from_email_addr.group(0) if from_email_addr else from_addr

                # Quick pattern match — archive without GPT
                if quick_skip(from_email_addr, subject):
                    gmail_archive(token, msg_id)
                    archived_count += 1
                    continue

                # GPT classify
                snippet = get_message_snippet(token, msg_id)
                result = gpt_triage(account, from_name, from_email_addr, subject, snippet)

                should_archive = result.get("archive", False)
                classification = result.get("classification", "other")
                priority       = result.get("priority", "low")
                summary        = result.get("summary", "")

                if should_archive or classification == "spam_or_noise":
                    gmail_archive(token, msg_id)
                    archived_count += 1
                    print(f"  [ARCHIVED] {from_name[:30]} — {subject[:50]}")
                else:
                    total_kept += 1
                    action_items.append({
                        "email": email,
                        "from_name": from_name,
                        "from_email": from_email_addr,
                        "subject": subject,
                        "priority": priority,
                        "classification": classification,
                        "summary": summary,
                    })
                    print(f"  [KEEP/{priority.upper()}] {from_name[:30]} — {subject[:50]}")

                time.sleep(0.3)

            except Exception as e:
                print(f"  Error on {msg_id}: {e}")

        total_archived += archived_count
        all_action_items.extend(action_items)
        print(f"  → Archived {archived_count}, kept {len(action_items)} action items")

    except Exception as e:
        print(f"  Account error: {e}")

db.close()

# ── Send Bailey a summary ───────────────────────────────────────────────────
print(f"\n=== SUMMARY ===")
print(f"Total archived: {total_archived}")
print(f"Action items remaining: {len(all_action_items)}")

if all_action_items:
    lines = [f"🔥 [BLAZE]\n📥 Inbox Triage Complete — {total_archived} archived, {len(all_action_items)} action items remain:\n"]
    # Sort by priority
    priority_order = {"high": 0, "medium": 1, "low": 2}
    sorted_items = sorted(all_action_items, key=lambda x: priority_order.get(x["priority"], 3))
    for item in sorted_items[:15]:
        emoji = "🔴" if item["priority"] == "high" else "🟡" if item["priority"] == "medium" else "⚪"
        lines.append(f"{emoji} [{item['email'].split('@')[0].upper()}] {item['from_name']} — {item['subject'][:50]}\n   {item['summary']}")
    if len(all_action_items) > 15:
        lines.append(f"... and {len(all_action_items) - 15} more.")
    summary_msg = "\n".join(lines)
    send_telegram("main", "telegram:7747110667", summary_msg)
    print("Summary sent to Bailey via Telegram")
else:
    send_telegram("main", "telegram:7747110667", f"🔥 [BLAZE]\n📥 Inbox Triage Complete — {total_archived} emails archived. Inbox is clean — no pending action items!")
    print("Clean inbox message sent")

print("\nDone.")
