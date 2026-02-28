#!/usr/bin/env python3
"""
Gmail Contact Sync v2 — runs every 5 minutes via cron
1. Inbox sync: scores contacts by who emails Bailey (via Gmail API)
2. Sent mail sync: tracks who Bailey replied to (sent_to flag, last_sent_to date)
3. Google Contacts sync: pulls People API contacts into contacts.db
Updates contact scores, orbits, tiers.
"""
import sys, os, sqlite3, json, logging
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from blaze_helper import log_cron, get_db
from google_api_manager import get_api

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("gmail_sync")

DB = "/Users/_mxappservice/blaze-data/blaze.db"

ACCOUNTS = [
    "bailey@contentco-op.com",
    "caio@astrocleanings.com",
    "blaze@contentco-op.com",
    # "baileyeubanks@gmail.com",  # NOT connected — Mail service not enabled
]

# Noise domains to skip
NOISE_DOMAINS = {
    "noreply", "no-reply", "notifications", "mailer", "donotreply",
    "support", "newsletter", "updates", "billing", "receipts",
    "github.com", "stripe.com", "google.com", "apple.com",
    "paypal.com", "amazon.com", "fidelity.com", "chase.com",
    "capitalone.com", "venmo.com", "cashapp.com", "zelle",
}

def is_noise_email(email):
    """Skip automated/noreply addresses."""
    if not email or "@" not in email:
        return True
    local, domain = email.lower().split("@", 1)
    for noise in NOISE_DOMAINS:
        if noise in local or noise in domain:
            return True
    return False

def extract_email(header_value):
    """Extract email from 'Name <email@example.com>' format."""
    if "<" in header_value and ">" in header_value:
        return header_value.split("<")[1].split(">")[0].strip().lower()
    return header_value.strip().lower()

def extract_name(header_value):
    """Extract name from 'Name <email@example.com>' format."""
    if "<" in header_value:
        name = header_value.split("<")[0].strip().strip('"').strip("'")
        if name:
            return name
    return ""

def ensure_columns(conn):
    """Add sent_to and last_sent_to columns if they don't exist."""
    existing = [row[1] for row in conn.execute("PRAGMA table_info(contacts)").fetchall()]
    if "sent_to" not in existing:
        conn.execute("ALTER TABLE contacts ADD COLUMN sent_to INTEGER DEFAULT 0")
    if "last_sent_to" not in existing:
        conn.execute("ALTER TABLE contacts ADD COLUMN last_sent_to TEXT")
    conn.commit()


# ===== INBOX SYNC (who emails us) =====

def sync_inbox(conn, api, account, hours=4):
    """Pull recent inbox messages and score contacts by inbound activity."""
    try:
        client = api.workspace(account)
        after_ts = int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp())
        query = "is:inbox -category:promotions -category:updates -category:social after:%d" % after_ts

        msgs_resp = client.execute(
            client.gmail.users().messages().list(userId="me", q=query, maxResults=100)
        )
        messages = msgs_resp.get("messages", [])
        if not messages:
            return 0

        contacts_seen = {}  # email -> {name, count, last_date, they_initiate, has_dollar}

        for msg in messages:
            try:
                detail = client.execute(
                    client.gmail.users().messages().get(
                        userId="me", id=msg["id"], format="metadata",
                        metadataHeaders=["From", "Subject", "Date"]
                    )
                )
                headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
                from_raw = headers.get("From", "")
                email = extract_email(from_raw)
                name = extract_name(from_raw)

                if is_noise_email(email):
                    continue
                # Skip self
                if email == account.lower():
                    continue

                snippet = detail.get("snippet", "")
                has_dollar = "$" in snippet

                # Parse date
                internal_date = detail.get("internalDate", "0")
                msg_date = datetime.fromtimestamp(int(internal_date) / 1000, tz=timezone.utc).isoformat()

                if email not in contacts_seen:
                    contacts_seen[email] = {
                        "name": name, "count": 0, "last_date": msg_date,
                        "they_initiate": True, "has_dollar": False
                    }
                contacts_seen[email]["count"] += 1
                if msg_date > contacts_seen[email]["last_date"]:
                    contacts_seen[email]["last_date"] = msg_date
                if has_dollar:
                    contacts_seen[email]["has_dollar"] = True
            except Exception as e:
                logger.debug("Msg parse error: %s", e)
                continue

        updated = 0
        for email, data in contacts_seen.items():
            upsert_contact_from_gmail(
                conn, email, data["name"], data["last_date"],
                data["count"], data["they_initiate"], data["has_dollar"]
            )
            updated += 1

        return updated
    except Exception as e:
        logger.error("Inbox sync error (%s): %s", account, e)
        return 0


def upsert_contact_from_gmail(conn, email, name, last_email_date,
                               email_count, they_initiate, has_dollar):
    """Upsert contact with Gmail inbound data."""
    recency_score = 0
    if last_email_date:
        try:
            dt = datetime.fromisoformat(last_email_date.replace("Z", "+00:00"))
            days_ago = (datetime.now(timezone.utc) - dt).days
            recency_score = max(0, 100 - days_ago * 0.3)
        except Exception:
            pass

    freq_score = min(100, email_count * 2)
    gmail_score = round((recency_score * 0.6) + (freq_score * 0.4), 2)

    if has_dollar:
        gmail_score = min(100, gmail_score * 1.3)
    if they_initiate:
        gmail_score = min(100, gmail_score * 1.1)

    existing = conn.execute(
        "SELECT id, priority_score, interaction_count FROM contacts WHERE email=?",
        (email,)
    ).fetchone()

    now = datetime.utcnow().isoformat()

    if existing:
        current_score = existing[1] or 0
        blended = round((current_score + gmail_score) / 2, 2)
        conn.execute("""
            UPDATE contacts SET
                priority_score = ?,
                interaction_count = interaction_count + ?,
                last_contacted = MAX(COALESCE(last_contacted,'2000-01-01'), ?),
                updated_at = ?,
                category = CASE WHEN category='unknown' THEN 'business' ELSE category END
            WHERE id = ?
        """, (blended, email_count, last_email_date or "", now, existing[0]))
    else:
        conn.execute("""
            INSERT OR IGNORE INTO contacts
            (name, email, priority_score, interaction_count,
             last_contacted, category, orbit, source, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (name or email, email, gmail_score, email_count,
              last_email_date, "business", 5, "gmail-inbox", now, now))

    conn.commit()


# ===== SENT MAIL SYNC (who Bailey replied to) =====

def sync_sent_mail(conn, api, account, hours=4):
    """Pull sent messages and mark contacts Bailey has replied to."""
    try:
        client = api.workspace(account)
        after_ts = int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp())
        query = "in:sent after:%d" % after_ts

        msgs_resp = client.execute(
            client.gmail.users().messages().list(userId="me", q=query, maxResults=100)
        )
        messages = msgs_resp.get("messages", [])
        if not messages:
            return 0

        updated = 0
        for msg in messages:
            try:
                detail = client.execute(
                    client.gmail.users().messages().get(
                        userId="me", id=msg["id"], format="metadata",
                        metadataHeaders=["To", "Cc", "Date"]
                    )
                )
                headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}

                # Parse sent date
                internal_date = detail.get("internalDate", "0")
                sent_date = datetime.fromtimestamp(int(internal_date) / 1000, tz=timezone.utc).isoformat()

                # Process To + Cc recipients
                recipients = []
                for field in ["To", "Cc"]:
                    raw = headers.get(field, "")
                    if raw:
                        for part in raw.split(","):
                            email = extract_email(part.strip())
                            name = extract_name(part.strip())
                            if email and not is_noise_email(email) and email != account.lower():
                                recipients.append((email, name))

                for email, name in recipients:
                    mark_sent_to(conn, email, name, sent_date)
                    updated += 1

            except Exception as e:
                logger.debug("Sent msg parse error: %s", e)
                continue

        return updated
    except Exception as e:
        logger.error("Sent sync error (%s): %s", account, e)
        return 0


def mark_sent_to(conn, email, name, sent_date):
    """Mark contact as sent_to and update last_sent_to date."""
    existing = conn.execute(
        "SELECT id, last_sent_to FROM contacts WHERE email=?", (email,)
    ).fetchone()

    now = datetime.utcnow().isoformat()

    if existing:
        # Update sent_to flag and date
        current_last = existing[1] or "2000-01-01"
        new_last = max(current_last, sent_date)
        conn.execute("""
            UPDATE contacts SET
                sent_to = 1,
                last_sent_to = ?,
                last_contacted = MAX(COALESCE(last_contacted,'2000-01-01'), ?),
                updated_at = ?
            WHERE id = ?
        """, (new_last, sent_date, now, existing[0]))
    else:
        # Create new contact from sent mail
        conn.execute("""
            INSERT OR IGNORE INTO contacts
            (name, email, sent_to, last_sent_to, last_contacted,
             category, orbit, source, created_at, updated_at)
            VALUES (?,?,1,?,?,?,?,?,?,?)
        """, (name or email, email, sent_date, sent_date,
              "business", 5, "gmail-sent", now, now))

    conn.commit()


# ===== GOOGLE CONTACTS SYNC (People API) =====

def sync_google_contacts(conn, api, account):
    """Pull contacts from Google People API and upsert."""
    try:
        client = api.workspace(account)
        contacts_fetched = 0
        page_token = None

        while True:
            kwargs = {
                "resourceName": "people/me",
                "pageSize": 100,
                "personFields": "names,emailAddresses,phoneNumbers,organizations",
            }
            if page_token:
                kwargs["pageToken"] = page_token

            result = client.execute(
                client.people.people().connections().list(**kwargs)
            )

            connections = result.get("connections", [])
            for person in connections:
                names = person.get("names", [])
                emails = person.get("emailAddresses", [])
                phones = person.get("phoneNumbers", [])
                orgs = person.get("organizations", [])

                if not emails:
                    continue

                name = names[0].get("displayName", "") if names else ""
                email = emails[0].get("value", "").strip().lower()
                phone = phones[0].get("value", "") if phones else ""
                company = orgs[0].get("name", "") if orgs else ""

                if not email or is_noise_email(email):
                    continue

                upsert_google_contact(conn, email, name, phone, company, account)
                contacts_fetched += 1

            page_token = result.get("nextPageToken")
            if not page_token:
                break

        return contacts_fetched
    except Exception as e:
        logger.error("People API error (%s): %s", account, e)
        return 0


def upsert_google_contact(conn, email, name, phone, company, source_account):
    """Upsert a Google Contact into contacts.db."""
    existing = conn.execute(
        "SELECT id, name, phone, company FROM contacts WHERE email=?", (email,)
    ).fetchone()

    now = datetime.utcnow().isoformat()
    source_tag = "google-contacts-%s" % source_account.split("@")[0]

    if existing:
        # Fill in blanks only — don't overwrite existing data
        updates = []
        params = []
        if not existing[1] and name:
            updates.append("name = ?")
            params.append(name)
        if not existing[2] and phone:
            updates.append("phone = ?")
            params.append(phone)
        if not existing[3] and company:
            updates.append("company = ?")
            params.append(company)
        if updates:
            updates.append("updated_at = ?")
            params.append(now)
            params.append(existing[0])
            conn.execute(
                "UPDATE contacts SET %s WHERE id = ?" % ", ".join(updates),
                params
            )
    else:
        conn.execute("""
            INSERT OR IGNORE INTO contacts
            (name, email, phone, company, category, orbit, source,
             created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (name or email, email, phone, company,
              "unknown", 5, source_tag, now, now))

    conn.commit()


# ===== ORBIT + TIER RECALCULATION =====

def recalculate_orbits_and_tiers(conn):
    """Recalculate orbit from last_contacted recency and tiers from score ranking."""
    conn.execute("""
        UPDATE contacts SET orbit = CASE
            WHEN julianday('now') - julianday(last_contacted) <= 7  THEN 1
            WHEN julianday('now') - julianday(last_contacted) <= 30 THEN 2
            WHEN julianday('now') - julianday(last_contacted) <= 90 THEN 3
            WHEN julianday('now') - julianday(last_contacted) <= 180 THEN 4
            ELSE 5
        END
        WHERE last_contacted IS NOT NULL AND last_contacted != ''
    """)
    conn.commit()

    ranked = conn.execute("SELECT id FROM contacts ORDER BY priority_score DESC").fetchall()
    for i, (cid,) in enumerate(ranked):
        rank = i + 1
        tier = 10 if rank <= 10 else 25 if rank <= 25 else 100 if rank <= 100 else 500 if rank <= 500 else 1000
        conn.execute("UPDATE contacts SET enrichment_tier=? WHERE id=?", (tier, cid))
    conn.commit()


# ===== MAIN =====

def run():
    api = get_api()
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    ensure_columns(conn)

    inbox_total = 0
    sent_total = 0
    gcontacts_total = 0

    for account in ACCOUNTS:
        # Inbox sync
        n = sync_inbox(conn, api, account)
        inbox_total += n
        print("%s inbox: %d contacts" % (account, n))

        # Sent mail sync
        n = sync_sent_mail(conn, api, account)
        sent_total += n
        print("%s sent: %d recipients" % (account, n))

        # Google Contacts sync
        n = sync_google_contacts(conn, api, account)
        gcontacts_total += n
        print("%s people: %d contacts" % (account, n))

    recalculate_orbits_and_tiers(conn)

    total = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    sent_count = conn.execute("SELECT COUNT(*) FROM contacts WHERE sent_to = 1").fetchone()[0]
    conn.close()

    summary = "inbox:%d sent:%d people:%d | total:%d sent_to:%d" % (
        inbox_total, sent_total, gcontacts_total, total, sent_count
    )
    print("\nSync complete. %s" % summary)
    log_cron("gmail_contact_sync", "success", summary)
    log_cron("sent_mail_sync", "success", "sent:%d recipients across %d accounts" % (sent_total, len(ACCOUNTS)))
    log_cron("google_contacts_sync", "success", "people:%d contacts across %d accounts" % (gcontacts_total, len(ACCOUNTS)))

if __name__ == "__main__":
    run()
