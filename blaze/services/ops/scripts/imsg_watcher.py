"""
imsg_watcher.py — Context-aware iMessage monitor + ACS bot responder.
Polls chat.db every 3s via SSH loopback (sshd has Full Disk Access).
LaunchAgent: com.blaze.imsg-watcher (KeepAlive, gui/502).

Routing pipeline:
  1. quality_gate()     — drop spam, short codes, trivial messages
  2. classify_sender()  — employee | client | prospect
  3. route_message()    — dispatch to correct agent + Telegram notify
"""
import subprocess, json, sys, os, time

OPENCLAW = "/usr/local/bin/openclaw"
OPENCLAW_ENV = {**os.environ, "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"}

STATE_FILE = "/Users/_mxappservice/blaze-data/imsg_watcher_state.json"
CONTACTS_DB = "/Users/_mxappservice/blaze-data/contacts/contacts.db"
POLL_INTERVAL = 3  # seconds

# ── Supabase (ACS CRM) ────────────────────────────────────────────────────────
SUPABASE_URL = "https://briokwdoonawhxisbydy.supabase.co"

def _load_supabase_key():
    env_file = __import__("pathlib").Path.home() / ".blaze_env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("SUPABASE_SERVICE_KEY="):
                return line.split("=", 1)[1].strip()
    return __import__("os").environ.get("SUPABASE_SERVICE_KEY", "")

SUPABASE_KEY = _load_supabase_key()
SUPABASE_HDRS = {
    "apikey": SUPABASE_KEY,
    "Authorization": "Bearer " + SUPABASE_KEY,
}

SSH_BASE = [
    "ssh", "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=no",
    "-o", "ConnectTimeout=5",
    "localhost",
]

# ── Apple ID accounts that trigger ACS bot responses ──────────────────────────
ACS_BOT_ACCOUNTS = {
    "E:caio@astrocleanings.com",
    "E:acs_customerservice@icloud.com",
}

# ── Employee registry ─────────────────────────────────────────────────────────
# Messages FROM employees to ACS accounts → Telegram relay only (no bot reply)
KNOWN_EMPLOYEES = {
    "+13464015841": {"name": "Caio",    "role": "president", "telegram": "7124538299"},
    "+15048581959": {"name": "Caio",    "role": "president", "telegram": "7124538299"},  # personal
    "+15013515927": {"name": "Bailey",  "role": "owner",     "telegram": "7747110667"},
    "caio@astrocleanings.com": {"name": "Caio", "role": "president", "telegram": "7124538299"},
    # Crew — phones confirmed Feb 27 2026
    "+18502471622": {"name": "Kailany", "role": "crew",      "telegram": ""},
    "+18575402386": {"name": "Diego",   "role": "crew",      "telegram": ""},
    "+17137322140": {"name": "Thiala",  "role": "crew",      "telegram": ""},
    # FUTURE — Caio's sister when phone known
}

# ── Telegram rate limiter ────────────────────────────────────────────────────
TG_COOLDOWN_SECS = 900    # 15 min per sender — prevents flood on catch-up
MSG_MAX_AGE_SECS = 7200   # 2 hours — older msgs skipped for Telegram (silent catch-up)
_tg_last_sent = {}        # {phone: float timestamp}

def should_telegram(sender, msg_ts):
    """Return True if we should send a Telegram for this message."""
    age = time.time() - msg_ts
    if age > MSG_MAX_AGE_SECS:
        return False  # historical catch-up — process silently, no ping
    last = _tg_last_sent.get(sender, 0)
    if time.time() - last < TG_COOLDOWN_SECS:
        return False  # already notified for this sender recently
    _tg_last_sent[sender] = time.time()
    return True

# ── Spam / noise filters ──────────────────────────────────────────────────────
SPAM_PATTERNS = [
    "your code is", "your otp", "verification code",
    "reply stop", "txt stop", "text stop", "opt out",
    "free msg", "free message", "offer expires",
    "claim your", "you've been selected", "won a",
]

IGNORE_SENDER_PATTERNS = [
    "alerts@", "noreply", "no-reply", "notify@", "info@", "support@",
]

# ── Urgency keywords → priority escalation ────────────────────────────────────
URGENT_KEYWORDS = [
    "urgent", "emergency", "accident", "cancel", "cancellation",
    "complaint", "hurt", "injured", "broken", "flood", "fire",
    "call me", "call us", "refund", "angry", "terrible", "never coming back",
]

LOG = "/Users/_mxappservice/logs/imsg_watcher.log"


# ── Utilities ──────────────────────────────────────────────────────────────────

def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print("[%s] %s" % (ts, msg), flush=True)


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception:
        pass


def ssh_query(sql):
    """Run sqlite3 query via SSH loopback so sshd FDA allows reading system DBs."""
    cmd = "sqlite3 %s \"%s\"" % (CONTACTS_DB, sql.replace('"', '\\"'))
    result = subprocess.run(
        SSH_BASE + [cmd],
        capture_output=True, text=True, timeout=10
    )
    return result.stdout.strip()


def ssh_chat_query(sql):
    """Run sqlite3 query against chat.db via SSH loopback."""
    cmd = "sqlite3 ~/Library/Messages/chat.db \"%s\"" % sql.replace('"', '\\"')
    result = subprocess.run(
        SSH_BASE + [cmd],
        capture_output=True, text=True, timeout=10
    )
    return result.stdout.strip()


def get_max_rowid():
    try:
        out = ssh_chat_query("SELECT MAX(ROWID) FROM message")
        return int(out) if out else 0
    except Exception:
        return 0


def poll_new_messages(last_rowid):
    """Returns list of (rowid, text, sender, chat_id, account_login, msg_ts)."""
    sql = (
        "SELECT m.ROWID, "
        "REPLACE(REPLACE(m.text, '|', '[PIPE]'), char(10), '[NL]'), "
        "h.id, c.ROWID, c.account_login, "
        "CAST(m.date AS REAL)/1000000000.0 + 978307200.0 "
        "FROM message m "
        "JOIN chat_message_join cmj ON cmj.message_id = m.ROWID "
        "JOIN chat c ON c.ROWID = cmj.chat_id "
        "LEFT JOIN handle h ON h.ROWID = m.handle_id "
        "WHERE m.ROWID > %d "
        "AND m.is_from_me = 0 "
        "AND m.text IS NOT NULL "
        "AND m.text != '' "
        "ORDER BY m.ROWID ASC "
        "LIMIT 20" % last_rowid
    )
    try:
        out = ssh_chat_query(sql)
        if not out:
            return []
        rows = []
        for line in out.splitlines():
            parts = line.split("|", 5)
            if len(parts) < 5:
                continue
            rowid   = int(parts[0])
            text    = parts[1].replace("[PIPE]", "|").replace("[NL]", "\n")
            sender  = parts[2] or "unknown"
            chat_id = int(parts[3]) if parts[3] else 0
            account_login = parts[4] or ""
            try:
                msg_ts = float(parts[5]) if len(parts) > 5 and parts[5] else time.time()
            except ValueError:
                msg_ts = time.time()
            rows.append((rowid, text, sender, chat_id, account_login, msg_ts))
        return rows
    except Exception as e:
        log("POLL ERROR: %s" % e)
        return []


def openclaw_agent(agent, prompt):
    try:
        subprocess.Popen(
            [OPENCLAW, "agent", "--agent", agent, "--message", prompt, "--json"],
            env=OPENCLAW_ENV, close_fds=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        log("OPENCLAW ERROR [%s]: %s" % (agent, e))


def tg(account, target, text):
    try:
        subprocess.Popen(
            [OPENCLAW, "message", "send",
             "--channel", "telegram",
             "--account", account,
             "--target", target,
             "--message", text],
            env=OPENCLAW_ENV, close_fds=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        log("TG ERROR: %s" % e)


# ── Context Enrichment ────────────────────────────────────────────────────────

def get_conversation_history(chat_id, limit=5):
    """
    Pull last N messages from this chat_id for conversation context.
    Returns formatted string like "Client: ...
ACS: ..." (chronological order).
    """
    if not chat_id:
        return ""
    sql = (
        "SELECT REPLACE(REPLACE(COALESCE(m.text,''), '|', '[P]'), char(10), ' '), m.is_from_me "
        "FROM message m "
        "JOIN chat_message_join cmj ON cmj.message_id = m.ROWID "
        "WHERE cmj.chat_id = %d "
        "AND m.text IS NOT NULL AND m.text != '' "
        "ORDER BY m.ROWID DESC LIMIT %d" % (chat_id, limit)
    )
    try:
        out = ssh_chat_query(sql)
        if not out:
            return ""
        msgs = []
        for line in out.splitlines():
            parts = line.rsplit("|", 1)  # split from right — text may have [P] but no real |
            if len(parts) == 2:
                text_part = parts[0].replace("[P]", "|")
                speaker = "ACS" if parts[1].strip() == "1" else "Client"
                msgs.append("%s: %s" % (speaker, text_part[:200].strip()))
        msgs.reverse()  # chronological order
        return "\n".join(msgs) if msgs else ""
    except Exception as e:
        log("HISTORY ERROR: %s" % e)
        return ""


def get_supabase_context(sender_phone):
    """
    Look up sender in Supabase by phone. Returns dict with:
      - address: street_address from contacts table
      - jobs: list of recent job dicts [{date, status, notes}]
      - access_notes: access code info extracted from job notes
    Returns None if not found or Supabase unavailable.
    """
    if not SUPABASE_KEY or not sender_phone:
        return None
    try:
        import urllib.request as _urlreq, json as _json, urllib.parse as _urlparse

        # Normalize phone to E.164 format for Supabase lookup
        digits = "".join(c for c in sender_phone if c.isdigit())
        if len(digits) == 10:
            e164 = "+1" + digits
        elif len(digits) == 11 and digits.startswith("1"):
            e164 = "+" + digits
        else:
            e164 = sender_phone if sender_phone.startswith("+") else "+" + digits

        # 1. Look up contact by phone
        url = (SUPABASE_URL + "/rest/v1/contacts"
               "?phone=eq." + _urlparse.quote(e164)
               + "&select=id,name,phone,street_address,metadata&limit=1")
        req = _urlreq.Request(url, headers=SUPABASE_HDRS)
        contacts = _json.loads(_urlreq.urlopen(req, timeout=5).read())
        if not contacts:
            return None

        contact = contacts[0]
        contact_id = contact.get("id")
        address = contact.get("street_address") or ""
        metadata = contact.get("metadata") or {}
        job_count = metadata.get("job_count", 0)

        # 2. Fetch recent jobs
        jobs_url = (SUPABASE_URL + "/rest/v1/jobs"
                    "?contact_id=eq." + contact_id
                    + "&select=scheduled_start,status,notes,total_amount_cents"
                    "&order=scheduled_start.desc&limit=3")
        req2 = _urlreq.Request(jobs_url, headers=SUPABASE_HDRS)
        jobs = _json.loads(_urlreq.urlopen(req2, timeout=5).read())

        # 3. Extract access notes from job notes (codes embedded in calendar notes)
        access_notes = ""
        for job in jobs:
            notes = job.get("notes") or ""
            if "access" in notes.lower() or "code" in notes.lower() or "gate" in notes.lower():
                # Extract the access info portion
                lines = notes.splitlines()
                for i, line in enumerate(lines):
                    if any(kw in line.lower() for kw in ["access", "gate", "alarm", "code", "door"]):
                        snippet = "\n".join(lines[i:i+3]).strip()
                        if snippet:
                            access_notes = snippet[:200]
                            break
            if access_notes:
                break

        # Format recent jobs
        job_lines = []
        for job in jobs:
            date = (job.get("scheduled_start") or "")[:10]
            status = job.get("status") or "unknown"
            cents = job.get("total_amount_cents") or 0
            amount = "$%.0f" % (cents / 100) if cents else ""
            raw_notes = (job.get("notes") or "")[:80]
            # Strip "Imported from Google Calendar: " prefix
            raw_notes = raw_notes.replace("Imported from Google Calendar: ", "")
            job_lines.append("  %s — %s %s %s" % (date, status, amount, raw_notes))

        return {
            "address":      address,
            "job_count":    job_count,
            "jobs":         job_lines,
            "access_notes": access_notes,
        }

    except Exception as e:
        log("SUPABASE CONTEXT ERROR: %s" % e)
        return None


# ── Step 1: Quality Gate ───────────────────────────────────────────────────────

def quality_gate(sender, text):
    """
    Returns True if message passes (should be processed).
    Returns False to drop silently.
    """
    # Strip sender to digits only for short-code check
    sender_digits = "".join(c for c in sender if c.isdigit())
    if 4 <= len(sender_digits) <= 6:
        log("QUALITY DROP (short code): %s" % sender)
        return False

    # Ignore-sender patterns
    sl = sender.lower()
    for p in IGNORE_SENDER_PATTERNS:
        if p in sl:
            log("QUALITY DROP (sender pattern): %s" % sender)
            return False

    # Spam text patterns
    tl = text.lower()
    for p in SPAM_PATTERNS:
        if p in tl:
            log("QUALITY DROP (spam): %s" % sender)
            return False

    # Trivial message — single emoji / reaction / one-char (but NOT if urgency word)
    stripped = text.strip()
    urgent = any(kw in tl for kw in URGENT_KEYWORDS)
    if not urgent:
        # Count actual word characters
        word_chars = [c for c in stripped if c.isalnum()]
        if len(word_chars) <= 2:
            log("QUALITY DROP (trivial): [%s] %s" % (sender, repr(stripped)))
            return False

    return True


# ── Step 2: Classify Sender ────────────────────────────────────────────────────

def normalize_phone(phone):
    """Extract last 10 digits for matching."""
    digits = "".join(c for c in phone if c.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits


def lookup_contact(sender):
    """
    Look up sender in contacts.db. Returns dict or None.
    Matches by last 10 digits of phone number.
    Uses json_object() to avoid pipe-separator corruption in notes/tags fields.
    """
    digits = normalize_phone(sender)
    if not digits:
        return None
    try:
        # json_object avoids pipe-split bugs when notes/tags contain '|'
        sql = (
            "SELECT json_object("
            "'name', COALESCE(name,''), "
            "'category', COALESCE(category,'unknown'), "
            "'tags', COALESCE(tags,''), "
            "'notes', COALESCE(notes,''), "
            "'last_contacted', COALESCE(last_contacted,''), "
            "'acs_score', COALESCE(acs_score,0), "
            "'client_status', COALESCE(client_status,''), "
            "'business_tags', COALESCE(business_tags,'') "
            ") FROM contacts "
            "WHERE phone LIKE '%%%s' LIMIT 1" % digits
        )
        out = ssh_query(sql)
        if not out:
            return None
        import json as _json
        return _json.loads(out)
    except Exception as e:
        log("CONTACT LOOKUP ERROR: %s" % e)
        return None


def classify_sender(sender):
    """
    Returns (class_str, info_dict).
    class_str: 'employee' | 'client' | 'known' | 'prospect'

    'client'  — in contacts.db with acs_score > 0 (actual ACS leads + clients)
    'known'   — in contacts.db but acs_score = 0 (personal/family/mixed contacts)
    'prospect'— not in contacts.db (unknown number)
    """
    # 1. Known employees (in-memory, fastest)
    sender_digits = normalize_phone(sender)
    for key in KNOWN_EMPLOYEES:
        if key in sender:
            return "employee", KNOWN_EMPLOYEES[key]
        key_digits = normalize_phone(key)
        # Only compare digits if BOTH sides have digits (avoid "" == "" false match)
        if sender_digits and key_digits and sender_digits == key_digits:
            return "employee", KNOWN_EMPLOYEES[key]

    # 2. Check contacts.db
    contact = lookup_contact(sender)
    if contact:
        acs_score = int(contact.get("acs_score") or 0)
        if acs_score > 0:
            # Actual ACS lead or client — full bot context
            return "client", contact
        else:
            # Known personal/family/mixed contact — no ACS relationship
            return "known", contact

    # 3. Completely unknown
    return "prospect", {}


def is_urgent(text):
    tl = text.lower()
    return any(kw in tl for kw in URGENT_KEYWORDS)


# ── Step 3: Route ──────────────────────────────────────────────────────────────

def route_acs_message(sender, text, chat_id, account_login, sender_class, sender_info, do_telegram=True):
    """Handle messages TO ACS iMessage accounts."""
    account_display = account_login.replace("E:", "")
    urgent = is_urgent(text)
    urgent_flag = "⚠️ URGENT — " if urgent else ""

    if sender_class == "employee":
        # Internal coordination — relay to Caio's Telegram only, no bot
        emp_name = sender_info.get("name", sender)
        log("%sEMPLOYEE RELAY [%s → %s]: %s" % (urgent_flag, emp_name, account_display, text[:80]))
        notif = (
            "%s📱 iMessage to %s from %s (employee):\n%s"
        ) % (urgent_flag, account_display, emp_name, text[:300])
        if do_telegram or urgent:  # historical catch-up = silent; urgent always fires
            tg("astro", "telegram:7124538299", notif)
        return

    # Known personal contact (not ACS client) texting ACS account — unusual
    if sender_class == "known":
        name = sender_info.get("name", sender)
        log("%sKNOWN (personal) → ACS [%s] %s: %s" % (urgent_flag, account_display, name, text[:80]))
        notif = (
            "%s📱 iMessage to %s from %s (personal contact, not ACS client):\n%s"
        ) % (urgent_flag, account_display, name, text[:300])
        if do_telegram or urgent:
            tg("astro", "telegram:7124538299", notif)
            tg("main", "telegram:7747110667", notif)
        return

    # Client or prospect → acs-worker bot response
    if sender_class == "client":
        name      = sender_info.get("name", "a client")
        tags      = sender_info.get("tags", "")
        notes     = sender_info.get("notes", "")
        last_ct   = sender_info.get("last_contacted", "")
        acs_score = sender_info.get("acs_score", "")

        # Phase 3: Supabase context (address, job history, access codes)
        supa = get_supabase_context(sender)
        # Phase 3: Conversation history from chat.db
        history = get_conversation_history(chat_id, limit=5)

        ctx_parts = ["Client context:"]
        ctx_parts.append("  Name: %s" % name)
        if supa and supa.get("address"):
            ctx_parts.append("  Service address: %s" % supa["address"])
        if supa and supa.get("job_count"):
            ctx_parts.append("  Total jobs on record: %s" % supa["job_count"])
        if tags:
            ctx_parts.append("  Tags: %s" % tags)
        if notes:
            ctx_parts.append("  Notes: %s" % notes[:200])
        if supa and supa.get("access_notes"):
            ctx_parts.append("  Access info: %s" % supa["access_notes"].replace("\n", " | "))
        if last_ct:
            ctx_parts.append("  Last contact: %s" % last_ct)
        ctx_parts.append("  ACS score: %s" % (acs_score or "n/a"))
        if supa and supa.get("jobs"):
            ctx_parts.append("  Recent jobs:")
            ctx_parts.extend(supa["jobs"])
        if history:
            ctx_parts.append("Recent conversation:")
            ctx_parts.append(history)

        context_block = "\n".join(ctx_parts)
        sender_label  = "client %s (%s)" % (name, sender)
        tg_prefix = "%s🧹 iMessage to %s\nFrom: %s (known client)\nMsg: %s" % (
            urgent_flag, account_display, name, text[:200])
    else:
        # Prospect — still pull conversation history (may have texted before)
        history = get_conversation_history(chat_id, limit=3)
        context_block = (
            "This is an unknown prospect (not in contacts database). "
            "Greet warmly, introduce ACS, ask for their name and what they need."
        )
        if history:
            context_block += "\n\nRecent conversation:\n" + history
        sender_label  = "unknown prospect (%s)" % sender
        tg_prefix = "%s🧹 iMessage to %s\nFrom: %s (new prospect)\nMsg: %s" % (
            urgent_flag, account_display, sender, text[:200])

    urgency_instruction = ""
    if urgent:
        urgency_instruction = (
            "\n\n⚠️ URGENT MESSAGE DETECTED. Acknowledge immediately. "
            "For cancellations: apologize and confirm cancellation, notify Caio. "
            "For complaints: apologize sincerely, promise Caio will call within the hour. "
            "For emergencies: express concern, get details, escalate immediately."
        )

    prompt = (
        "%sIncoming iMessage to ACS account (%s).\n"
        "From: %s\n"
        "Message: \"%s\"\n\n"
        "%s\n"
        "Review this message and send Caio a Telegram summary with:\n"
        "  - Who sent it and what they need\n"
        "  - Your suggested response (but do NOT send the iMessage — human review required)\n"
        "  - Any urgency flags\n\n"
        "DO NOT call imsg_send. DO NOT reply to the client directly.\n"
        "Caio will respond manually after reviewing your summary.%s"
    ) % (
        urgent_flag,
        account_display,
        sender_label,
        text,
        context_block,
        urgency_instruction,
    )

    log("%sBOT → acs-worker [%s] %s: %s" % (urgent_flag, account_display, sender_label, text[:80]))
    openclaw_agent("acs-worker", prompt)  # agent always dispatches regardless of rate limit
    if do_telegram or urgent:
        tg("astro", "telegram:7124538299", tg_prefix)


def route_bailey_message(sender, text, chat_id, sender_class, sender_info, do_telegram=True):
    """Handle messages TO Bailey's personal iMessage."""
    urgent = is_urgent(text)
    urgent_flag = "⚠️ URGENT — " if urgent else ""

    if sender_class == "employee":
        emp_name = sender_info.get("name", sender)
        log("%sEMPLOYEE → Bailey from %s: %s" % (urgent_flag, emp_name, text[:80]))
        if do_telegram or urgent:
            tg("main", "telegram:7747110667",
               "%s📱 iMessage from %s:\n%s" % (urgent_flag, emp_name, text[:300]))
        openclaw_agent("main",
            "%s%s texted your iMessage:\n\"%s\"\nReview and take action if needed." % (
                urgent_flag, emp_name, text))
        return

    if sender_class == "client":
        name = sender_info.get("name", sender)
        log("%sCLIENT → main [Bailey] %s: %s" % (urgent_flag, name, text[:80]))
        prompt = (
            "%s%s texted your iMessage.\n"
            "Message: \"%s\"\n\n"
            "Contact notes: %s\n"
            "This may be CC or ACS related. Review and respond or escalate as needed."
        ) % (urgent_flag, name, text, sender_info.get("notes", "none"))
        if do_telegram or urgent:
            tg("main", "telegram:7747110667",
               "%s📱 iMessage from %s:\n%s" % (urgent_flag, name, text[:200]))
        openclaw_agent("main", prompt)
        return

    # Known personal contact texting Bailey — normal
    if sender_class == "known":
        name = sender_info.get("name", sender)
        log("%sKNOWN → main [Bailey] %s: %s" % (urgent_flag, name, text[:80]))
        if do_telegram or urgent:
            tg("main", "telegram:7747110667",
               "%s📱 iMessage from %s:\n%s" % (urgent_flag, name, text[:200]))
        openclaw_agent("main",
            "%s%s texted your iMessage:\n\"%s\"\nContact in your database (not ACS client)." % (
                urgent_flag, name, text))
        return

    # Completely unknown on Bailey's line — low priority
    log("PROSPECT → main [Bailey] %s: %s" % (sender, text[:80]))
    if do_telegram:
        tg("main", "telegram:7747110667",
           "📱 Unknown iMessage from %s:\n%s" % (sender, text[:200]))
    openclaw_agent("main",
        "Unknown person (%s) texted your iMessage:\n\"%s\"\nLow priority. Review when you can." % (
            sender, text))


def route_message(sender, text, chat_id, account_login, msg_ts=None):
    # Step 1: Quality gate
    if not quality_gate(sender, text):
        return

    # Step 2: Classify
    sender_class, sender_info = classify_sender(sender)
    log("CLASSIFY [%s] %s → %s" % (account_login.replace("E:",""), sender, sender_class))

    # Step 3: Compute rate-limit flag (employees always get Telegram when fresh)
    ts = msg_ts if msg_ts else time.time()
    do_telegram = should_telegram(sender, ts)

    # Step 4: Route by destination account
    is_acs_account = account_login in ACS_BOT_ACCOUNTS

    if is_acs_account:
        route_acs_message(sender, text, chat_id, account_login, sender_class, sender_info, do_telegram)
    else:
        # Bailey's account or any other
        route_bailey_message(sender, text, chat_id, sender_class, sender_info, do_telegram)


# ── Main Loop ──────────────────────────────────────────────────────────────────

def main():
    state = load_state()
    last_rowid = state.get("last_rowid")

    if last_rowid is None:
        last_rowid = get_max_rowid()
        log("imsg_watcher v2 started, watching from rowid %d" % last_rowid)
        save_state({"last_rowid": last_rowid})
    else:
        log("imsg_watcher v2 started, resuming from rowid %d" % last_rowid)

    while True:
        try:
            rows = poll_new_messages(last_rowid)
            for row in rows:
                rowid, text, sender, chat_id, account_login = row[0], row[1], row[2], row[3], row[4]
                msg_ts = row[5] if len(row) > 5 else time.time()
                if text and text.strip():
                    route_message(sender, text.strip(), chat_id, account_login, msg_ts)
                last_rowid = max(last_rowid, rowid)

            if rows:
                save_state({"last_rowid": last_rowid})

        except Exception as e:
            log("LOOP ERROR: %s" % e)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
