"""
contact_enricher.py — Nightly CRM enrichment from iMessage conversations.

Scans ACS iMessage conversations for NEW messages since last run, extracts
structured contact data using acs-worker (Claude), upserts to contacts.db + Supabase.

Run: python3 contact_enricher.py [--force] [--dry-run] [--all]
  --force : re-enrich even if enriched recently
  --dry-run: show what would happen, no writes
  --all   : process ALL contacts ever (initial backfill), not just recent

LaunchAgent: com.blaze.contact-enricher (23:45 daily)
State: ~/blaze-data/contact_enricher_state.json
Log:   ~/logs/contact_enricher.log
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
OPENCLAW      = "/usr/local/bin/openclaw"
OPENCLAW_ENV  = {**os.environ, "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"}
CONTACTS_DB   = str(Path.home() / "blaze-data/contacts/contacts.db")
STATE_FILE    = str(Path.home() / "blaze-data/contact_enricher_state.json")
LOG_FILE      = str(Path.home() / "logs/contact_enricher.log")
SUPABASE_URL  = "https://briokwdoonawhxisbydy.supabase.co"

MIN_MSGS     = 2    # skip contacts with fewer messages in their chat
AI_TIMEOUT   = 150  # seconds (openclaw can be slow)
COOLDOWN_HRS = 20   # don't re-enrich same contact within this window
MAX_PER_RUN  = 20   # max contacts to enrich per run (prevents huge AI queues)

ACS_ACCOUNT  = "E:caio@astrocleanings.com"

EMPLOYEE_PHONES = {
    "+15048581959", "+13464015841",   # Caio
    "+15013515927",                   # Bailey
    "+18502471622",                   # Kailany
    "+18575402386",                   # Diego
    "+17137322140",                   # Thiala
}

def _load_env_key(key_name):
    env_file = Path.home() / ".blaze_env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith(key_name + "="):
                return line.split("=", 1)[1].strip()
    return os.environ.get(key_name, "")

SUPABASE_KEY = _load_env_key("SUPABASE_SERVICE_KEY")
SUPA_HDRS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": "Bearer " + SUPABASE_KEY,
    "Content-Type":  "application/json",
    "Prefer":        "return=representation",
}

SSH_BASE = [
    "ssh", "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=no",
    "-o", "ConnectTimeout=5",
    "localhost",
]

DRY_RUN = "--dry-run" in sys.argv
FORCE   = "--force"   in sys.argv
ALL     = "--all"     in sys.argv

# ── Logging ───────────────────────────────────────────────────────────────────
Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
_log_file = open(LOG_FILE, "a")

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = "[%s] %s" % (ts, msg)
    print(line)
    _log_file.write(line + "\n")
    _log_file.flush()

# ── State ─────────────────────────────────────────────────────────────────────
def load_state():
    try:
        return json.loads(Path(STATE_FILE).read_text())
    except Exception:
        return {}

def save_state(state):
    Path(STATE_FILE).write_text(json.dumps(state, indent=2))

# ── SSH chat.db queries ───────────────────────────────────────────────────────
def ssh_chat(sql):
    result = subprocess.run(
        SSH_BASE + ["sqlite3 ~/Library/Messages/chat.db \"%s\"" % sql.replace('"', '\\"')],
        capture_output=True, text=True, timeout=15
    )
    return result.stdout.strip()

def get_max_rowid():
    out = ssh_chat("SELECT MAX(ROWID) FROM message")
    return int(out.strip()) if out.strip().isdigit() else 0

def get_active_senders(since_rowid=0):
    """
    Get unique senders from ACS account with enough messages.
    since_rowid=0 → ALL senders ever (--all mode).
    since_rowid=N → only senders who have messages AFTER rowid N (delta mode).
    """
    rowid_filter = "AND m.ROWID > %d" % since_rowid if since_rowid > 0 else ""
    sql = (
        "SELECT h.id, c.ROWID, count(m.ROWID) as cnt "
        "FROM message m "
        "JOIN chat_message_join cmj ON cmj.message_id = m.ROWID "
        "JOIN chat c ON c.ROWID = cmj.chat_id "
        "LEFT JOIN handle h ON h.ROWID = m.handle_id "
        "WHERE c.account_login = '%s' "
        "AND m.is_from_me = 0 "
        "%s"
        "AND m.text IS NOT NULL AND length(m.text) > 2 "
        "AND h.id IS NOT NULL "
        "GROUP BY h.id "
        "HAVING cnt >= %d "
        "ORDER BY cnt DESC"
    ) % (ACS_ACCOUNT, rowid_filter, MIN_MSGS)

    out = ssh_chat(sql)
    senders = []
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) >= 2:
            phone   = parts[0].strip()
            chat_id = int(parts[1]) if parts[1].strip().isdigit() else 0
            count   = int(parts[2]) if len(parts) > 2 and parts[2].strip().isdigit() else 1
            senders.append((phone, chat_id, count))
    return senders

def get_conversation(chat_id, limit=25):
    """Pull last N messages from chat as readable string."""
    sql = (
        "SELECT REPLACE(COALESCE(m.text,''), char(10), ' '), m.is_from_me "
        "FROM message m "
        "JOIN chat_message_join cmj ON cmj.message_id = m.ROWID "
        "WHERE cmj.chat_id = %d AND m.text IS NOT NULL AND length(m.text) > 1 "
        "ORDER BY m.ROWID DESC LIMIT %d"
    ) % (chat_id, limit)
    out = ssh_chat(sql)
    if not out:
        return ""
    msgs = []
    for line in out.splitlines():
        parts = line.rsplit("|", 1)
        if len(parts) == 2:
            text, is_me = parts[0].strip(), parts[1].strip()
            label = "ACS" if is_me == "1" else "CLIENT"
            msgs.append("%s: %s" % (label, text))
    msgs.reverse()
    return "\n".join(msgs)

# ── Contacts DB ───────────────────────────────────────────────────────────────
def get_contact(phone):
    conn = sqlite3.connect(CONTACTS_DB)
    digits = re.sub(r"[^\d]", "", phone)[-10:]
    row = conn.execute(
        "SELECT name, category, acs_score, tags, notes, last_contacted "
        "FROM contacts WHERE phone LIKE ? LIMIT 1",
        ("%%" + digits,)
    ).fetchone()
    conn.close()
    if row:
        return {"name": row[0] or "", "category": row[1] or "",
                "acs_score": row[2] or 0, "tags": row[3] or "",
                "notes": row[4] or "", "last_contacted": row[5] or ""}
    return None

def upsert_contact(phone, extracted, existing):
    if not extracted:
        return False
    conn = sqlite3.connect(CONTACTS_DB)
    digits = re.sub(r"[^\d]", "", phone)[-10:]

    new_parts = []
    if extracted.get("address"):
        existing_notes = (existing or {}).get("notes", "") or ""
        if extracted["address"][:20].lower() not in existing_notes.lower():
            new_parts.append("Address: " + extracted["address"])
    if extracted.get("access_notes"):
        new_parts.append("Access: " + extracted["access_notes"])
    if extracted.get("service_frequency") and extracted["service_frequency"] not in ("unknown", None):
        new_parts.append("Frequency: " + extracted["service_frequency"])
    if extracted.get("payment_method") and extracted["payment_method"] not in ("unknown", None):
        new_parts.append("Payment: " + extracted["payment_method"])
    if extracted.get("notes_to_append"):
        new_parts.append(extracted["notes_to_append"])

    if not new_parts and not (extracted.get("acs_score_delta") or 0):
        conn.close()
        return False

    if existing:
        current_notes = existing.get("notes", "") or ""
        for part in new_parts:
            if part[:25] not in current_notes:
                current_notes = (current_notes + "\n" + part).strip()
        old_score = existing.get("acs_score", 0) or 0
        new_score = min(100, old_score + (extracted.get("acs_score_delta") or 0))
        cat = existing.get("category", "unknown")
        if extracted.get("is_client") and cat in ("unknown", "mixed", ""):
            cat = "business"
        conn.execute(
            "UPDATE contacts SET notes=?, acs_score=?, category=? WHERE phone LIKE ?",
            (current_notes, new_score, cat, "%%" + digits)
        )
    else:
        conn.execute(
            "INSERT OR IGNORE INTO contacts "
            "(phone, name, category, acs_score, notes, tags, last_contacted) "
            "VALUES (?,?,?,?,?,?,?)",
            (phone,
             extracted.get("name") or "Unknown",
             "business" if extracted.get("is_client") else "unknown",
             min(20, extracted.get("acs_score_delta") or 3),
             "\n".join(new_parts),
             "prospect",
             datetime.now().strftime("%Y-%m-%d"))
        )

    conn.commit()
    conn.close()
    return True

# ── Supabase ──────────────────────────────────────────────────────────────────
def supa_get(phone):
    url = SUPABASE_URL + "/rest/v1/contacts?select=id,metadata,ai_summary,street_address&phone=eq." + urllib.parse.quote(phone)
    try:
        req = urllib.request.Request(url, headers={k: v for k, v in SUPA_HDRS.items() if k != "Prefer"})
        data = json.loads(urllib.request.urlopen(req).read())
        if data:
            return data[0]["id"], data[0].get("metadata") or {}, data[0].get("ai_summary") or "", data[0].get("street_address") or ""
    except Exception:
        pass
    return None, {}, "", ""

def supa_patch(contact_id, payload):
    url = SUPABASE_URL + "/rest/v1/contacts?id=eq." + contact_id
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=SUPA_HDRS, method="PATCH")
    urllib.request.urlopen(req)

def supa_insert(payload):
    url = SUPABASE_URL + "/rest/v1/contacts"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=SUPA_HDRS, method="POST")
    urllib.request.urlopen(req)

def update_supabase(phone, contact_name, extracted, supa_id, meta, summary, existing_address):
    meta = dict(meta)
    changed = False
    if extracted.get("access_notes"):
        meta["access_notes"] = extracted["access_notes"]
        changed = True
    if extracted.get("service_frequency") and extracted["service_frequency"] != "unknown":
        meta["service_frequency"] = extracted["service_frequency"]
        changed = True
    if extracted.get("payment_method") and extracted["payment_method"] != "unknown":
        meta["payment_method"] = extracted["payment_method"]
        changed = True
    if extracted.get("language") and extracted["language"] != "unknown":
        meta["language"] = extracted["language"]
    if extracted.get("notes_to_append"):
        old = meta.get("enricher_notes", "")
        meta["enricher_notes"] = (old + " | " + extracted["notes_to_append"]).strip(" |")
        changed = True
    meta["last_enriched"] = datetime.now().isoformat()

    patch = {"metadata": meta, "last_interaction": datetime.now().isoformat()}

    if extracted.get("address") and not existing_address:
        patch["street_address"] = extracted["address"]
        changed = True

    if changed and (extracted.get("notes_to_append") or extracted.get("address")):
        note = (extracted.get("notes_to_append") or extracted.get("address") or "")[:100]
        patch["ai_summary"] = (summary + " [%s: %s]" % (datetime.now().strftime("%Y-%m-%d"), note)).strip()

    try:
        supa_patch(supa_id, patch)
        return True
    except Exception as e:
        log("  Supabase patch error: %s" % e)
        return False

# ── AI Extraction ─────────────────────────────────────────────────────────────
PROMPT_TMPL = """You are a CRM assistant for Astro Cleaning Services (ACS), a residential cleaning company in Houston, TX. The owner is Caio Gustin.

CONTACT: {name} ({phone})
CURRENT DB RECORD:
{existing_summary}

RECENT CONVERSATION ({msg_count} messages — "ACS" = Caio outbound, "CLIENT" = incoming):
---
{conversation}
---

Extract CRM data. Reply ONLY with a valid JSON object, no extra text.
Translate any Portuguese or Spanish to English in your output values.

{{
  "name": "full name if explicitly stated (null otherwise)",
  "address": "complete service address if mentioned (null if not mentioned)",
  "access_notes": "any entry instructions: gate codes, key info, garage tips, alarm, who to call on arrival (null if none)",
  "service_frequency": "weekly|biweekly|monthly|one-time|unknown",
  "payment_method": "zelle|cash|venmo|card|check|unknown",
  "language": "english|portuguese|spanish|mixed",
  "is_client": true or false,
  "new_names_mentioned": ["Person Name (their relationship or context)"],
  "acs_score_delta": integer 0-15,
  "urgency_flag": null or "complaint|cancel|reschedule|new_booking",
  "notes_to_append": "other useful CRM info in one sentence (null if nothing useful)"
}}

Scoring guide for acs_score_delta:
  0 = nothing useful extracted
  3 = confirmed they are a client
  7 = active recurring client with scheduling info
  10 = high-value client, referral source, or long-term relationship
  15 = exceptional value (brings multiple jobs, inside access, key holder relationship)

Only extract what is EXPLICITLY stated. Do not guess or infer."""


def call_openclaw(prompt):
    try:
        result = subprocess.run(
            [OPENCLAW, "agent", "--agent", "acs-worker", "--message", prompt, "--json"],
            capture_output=True, text=True, timeout=AI_TIMEOUT, env=OPENCLAW_ENV
        )
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        log("  AI timed out")
        return ""
    except Exception as e:
        log("  AI error: %s" % e)
        return ""


def parse_json(text):
    if not text:
        return None
    # Try to unwrap openclaw JSON envelope
    try:
        outer = json.loads(text)
        if isinstance(outer, dict):
            for key in ("response", "content", "message", "text"):
                if key in outer:
                    text = outer[key]
                    break
    except Exception:
        pass
    # Find first complete JSON object in the text
    for match in re.finditer(r'\{', text):
        start = match.start()
        depth = 0
        for i, ch in enumerate(text[start:]):
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:start + i + 1])
                    except Exception:
                        break
    return None


def enrich_contact(phone, contact_name, existing, conversation, msg_count):
    existing_summary = "(not in database)"
    if existing:
        existing_summary = "Name: %s | Score: %s | Category: %s\nTags: %s\nNotes: %s" % (
            existing.get("name", ""), existing.get("acs_score", 0),
            existing.get("category", ""), existing.get("tags", ""),
            (existing.get("notes", "") or "")[:200],
        )
    prompt = PROMPT_TMPL.format(
        name=contact_name or phone, phone=phone,
        existing_summary=existing_summary,
        conversation=conversation, msg_count=msg_count,
    )
    raw = call_openclaw(prompt)
    return parse_json(raw)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    log("=" * 60)
    log("contact_enricher started (dry=%s force=%s all=%s)" % (DRY_RUN, FORCE, ALL))

    state = load_state()

    # Determine ROWID delta
    if ALL:
        since_rowid = 0
        log("--all mode: scanning ALL ACS conversations ever")
    else:
        since_rowid = state.get("last_rowid", 0)
        log("Delta mode: scanning new messages after rowid %d" % since_rowid)

    current_max_rowid = get_max_rowid()
    senders = get_active_senders(since_rowid)
    log("Found %d active senders (min %d msgs)" % (len(senders), MIN_MSGS))

    enriched    = []
    skipped     = []
    errors      = []
    unknown_names = []
    processed   = 0

    for phone, chat_id, msg_count in senders:
        if processed >= MAX_PER_RUN:
            log("Hit MAX_PER_RUN (%d), stopping" % MAX_PER_RUN)
            break

        # Skip employees
        if phone in EMPLOYEE_PHONES:
            skipped.append("%s (employee)" % phone)
            continue

        # Skip short codes
        if len(re.sub(r"\D", "", phone)) <= 6:
            skipped.append("%s (short code)" % phone)
            continue

        # Skip personal/family contacts (not ACS business)
        _pre = get_contact(phone)
        if _pre and _pre.get("category") in ("personal", "family"):
            skipped.append("%s (personal/family)" % phone)
            continue

        # Skip email handles
        if "@" in phone:
            skipped.append("%s (email)" % phone)
            continue

        # Cooldown (skip if enriched recently, unless --force)
        last_ts = state.get(phone, {}).get("enriched_at", 0) if isinstance(state.get(phone), dict) else state.get(phone, 0)
        if not FORCE and time.time() - last_ts < COOLDOWN_HRS * 3600:
            skipped.append("%s (enriched %.0fh ago)" % (phone, (time.time() - last_ts) / 3600))
            continue

        # Get conversation
        conversation = get_conversation(chat_id)
        if not conversation:
            skipped.append("%s (no conversation)" % phone)
            continue

        existing = get_contact(phone)
        contact_name = (existing or {}).get("name", "") or phone

        log("Enriching %s (%s, %d msgs in delta)" % (phone, contact_name, msg_count))

        if DRY_RUN:
            log("  [DRY RUN] Conversation sample:")
            for line in conversation.splitlines()[-5:]:
                log("    " + line)
            enriched.append((phone, contact_name, {}))
            processed += 1
            continue

        extracted = enrich_contact(phone, contact_name, existing, conversation, msg_count)
        if not extracted:
            errors.append(phone)
            processed += 1
            continue

        log("  → name=%s addr=%s access=%s freq=%s score_delta=%s" % (
            extracted.get("name") or "—",
            (extracted.get("address") or "—")[:40],
            (extracted.get("access_notes") or "—")[:30],
            extracted.get("service_frequency") or "—",
            extracted.get("acs_score_delta") or 0,
        ))

        # Collect unknown names
        for name_ctx in (extracted.get("new_names_mentioned") or []):
            if name_ctx and name_ctx.strip():
                unknown_names.append("%s → from %s" % (name_ctx.strip(), contact_name or phone))

        # Write to contacts.db
        upsert_contact(phone, extracted, existing)

        # Write to Supabase
        supa_id, supa_meta, supa_summary, supa_addr = supa_get(phone)
        if supa_id:
            update_supabase(phone, contact_name, extracted, supa_id, supa_meta, supa_summary, supa_addr)
        elif extracted.get("is_client") or (extracted.get("acs_score_delta") or 0) >= 5:
            try:
                supa_insert({
                    "phone": phone,
                    "name": extracted.get("name") or contact_name or phone,
                    "tags": ["acs_client", "iMessage"] if extracted.get("is_client") else ["prospect", "iMessage"],
                    "priority_score": max(1, extracted.get("acs_score_delta") or 3),
                    "preferred_channel": "imessage",
                    "street_address": extracted.get("address") or None,
                    "metadata": {
                        "access_notes":      extracted.get("access_notes"),
                        "service_frequency": extracted.get("service_frequency"),
                        "payment_method":    extracted.get("payment_method"),
                        "language":          extracted.get("language"),
                        "last_enriched":     datetime.now().isoformat(),
                    },
                    "ai_summary": extracted.get("notes_to_append") or "",
                    "last_interaction": datetime.now().isoformat(),
                })
                log("  Inserted new contact to Supabase")
            except Exception as e:
                log("  Supabase insert error: %s" % e)

        # Update state for this phone
        state[phone] = {"enriched_at": time.time(), "last_msg_count": msg_count}
        enriched.append((phone, contact_name, extracted))
        processed += 1
        time.sleep(4)  # pace AI calls

    # Save state with new rowid watermark
    state["last_rowid"] = current_max_rowid
    save_state(state)

    if DRY_RUN:
        log("DRY RUN complete. Would enrich: %d | Skip: %d" % (len(enriched), len(skipped)))
        return

    # ── Telegram summary ──────────────────────────────────────────────────────
    useful = [(p, n, e) for p, n, e in enriched if
              e.get("address") or e.get("access_notes") or (e.get("acs_score_delta") or 0) >= 5]

    if not useful and not unknown_names and not errors:
        log("Nothing notable to report — skipping Telegram")
    else:
        lines = ["🧹 ACS Enricher — %s" % datetime.now().strftime("%b %d")]
        lines.append("%d enriched | %d skipped | %d errors" % (len(enriched), len(skipped), len(errors)))

        if useful:
            lines.append("")
            lines.append("✅ New data captured:")
            for phone, name, e in useful[:7]:
                bits = []
                if e.get("address"):      bits.append("📍 " + e["address"][:45])
                if e.get("access_notes"): bits.append("🔑 " + e["access_notes"][:35])
                if e.get("service_frequency") and e["service_frequency"] != "unknown":
                    bits.append(e["service_frequency"])
                if e.get("urgency_flag"): bits.append("⚠️ " + e["urgency_flag"])
                if bits:
                    lines.append("  • %s: %s" % (name or phone, " | ".join(bits)))

        if unknown_names:
            lines.append("")
            lines.append("❓ New names to confirm:")
            for n in unknown_names[:5]:
                lines.append("  • " + n)

        if errors:
            lines.append("")
            lines.append("⚠️ %d AI errors — check ~/logs/contact_enricher.log" % len(errors))

        msg = "\n".join(lines)
        log("Sending Telegram to Caio")
        try:
            subprocess.Popen(
                [OPENCLAW, "message", "send",
                 "--channel", "telegram", "--account", "astro",
                 "--target", "telegram:7124538299", "--message", msg],
                env=OPENCLAW_ENV, close_fds=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            log("Telegram error: %s" % e)

    log("Done. Enriched: %d | Skipped: %d | Errors: %d | Rowid: %d→%d" % (
        len(enriched), len(skipped), len(errors), since_rowid, current_max_rowid))


if __name__ == "__main__":
    main()
