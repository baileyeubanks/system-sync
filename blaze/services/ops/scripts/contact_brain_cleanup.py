#!/usr/bin/env python3
"""
contact_brain_cleanup.py — Data quality pass on contacts.db
1. Remove trailing semicolons from company names (216 contacts)
2. Delete/merge ghost contacts (notification artifacts)
3. Normalize phone numbers to E.164
4. Strip whitespace from names

Safe to re-run.
2026-02-22
"""
import sqlite3, re
from datetime import datetime

DB = "/Users/_mxappservice/blaze-data/contacts/contacts.db"
NOW = datetime.now().isoformat()

GHOST_PATTERNS = [
    "Deposited a new message",
    "Your iMessage sender id",
    "SMS from",
    "Unknown Caller",
]

def normalize_phone(phone):
    if not phone:
        return None
    digits = re.sub(r'\D', '', str(phone))
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits[0] == '1':
        return f"+{digits}"
    return phone.strip()


def run():
    conn = sqlite3.connect(DB, timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row

    # 1. Strip trailing semicolons from companies
    companies_fixed = conn.execute("""
        UPDATE contacts SET company = rtrim(trim(company), ';'),
                             updated_at = ?
        WHERE company LIKE '%;'
    """, (NOW,)).rowcount
    print(f"Company semicolons stripped: {companies_fixed}")

    # 2. Delete ghost contacts (notification artifacts)
    ghosts_removed = 0
    for pattern in GHOST_PATTERNS:
        count = conn.execute(
            "DELETE FROM contacts WHERE name LIKE ?", (f"%{pattern}%",)
        ).rowcount
        if count:
            print(f"  Deleted ghost: '{pattern}' ({count} rows)")
        ghosts_removed += count

    # 3. Normalize phone numbers
    phones_fixed = 0
    rows = conn.execute("SELECT id, phone FROM contacts WHERE phone IS NOT NULL").fetchall()
    for r in rows:
        normalized = normalize_phone(r["phone"])
        if normalized and normalized != r["phone"]:
            conn.execute("UPDATE contacts SET phone=?, updated_at=? WHERE id=?",
                         (normalized, NOW, r["id"]))
            phones_fixed += 1

    # 4. Strip whitespace from names and company
    conn.execute("""
        UPDATE contacts SET name = trim(name), updated_at = ?
        WHERE name != trim(name)
    """, (NOW,))
    conn.execute("""
        UPDATE contacts SET company = trim(company), updated_at = ?
        WHERE company IS NOT NULL AND company != trim(company)
    """, (NOW,))

    conn.commit()
    conn.close()

    print(f"\nCleanup complete:")
    print(f"  Company names fixed: {companies_fixed}")
    print(f"  Ghost contacts removed: {ghosts_removed}")
    print(f"  Phone numbers normalized: {phones_fixed}")


if __name__ == "__main__":
    run()
