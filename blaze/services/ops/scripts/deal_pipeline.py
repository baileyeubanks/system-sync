#!/usr/bin/env python3
"""
deal_pipeline.py — Deal pipeline tracker for contacts.db
1. Adds deal_stage and deal_value columns (safe if already exist)
2. Seeds known active deals from context
3. Prints current pipeline summary

2026-02-22
"""
import sqlite3
from datetime import datetime, date

DB = "/Users/_mxappservice/blaze-data/contacts/contacts.db"
NOW = datetime.now().isoformat()


KNOWN_DEALS = [
    # (name_search, deal_stage, deal_value, notes_append, close_by)
    ("Tyler Day",           "proposal_sent",  "TBD",    "Nashville commercial cleaning quote. MDG/Freeman Company. Sent Jan 22, no response. Follow up immediately.", "2026-02-28"),
    ("Schneider Electric",  "active",         "TBD",    "CRA Week content system — thought leader video. CERAWeek March. Delivery critical.", "2026-03-10"),
    ("Dustin Dow",          "active",         "TBD",    "Content Co-op active client. Regular production work.", None),
    ("Andrew N. Van Chau",  "active",         "TBD",    "BP — active client.", None),
    ("Alexandra Franceschi","active",         "TBD",    "BP — active client.", None),
    ("Crunch Fitness",      "prospecting",    "TBD",    "ACS target — gym commercial cleaning. Buzz Houston ad focus.", "2026-03-31"),
]


def run():
    conn = sqlite3.connect(DB, timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row

    # Add deal columns (safe — ignores if exists)
    for col in [
        ("deal_stage",    "TEXT"),
        ("deal_value",    "TEXT"),
        ("deal_close_by", "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE contacts ADD COLUMN {col[0]} {col[1]}")
            print(f"Column added: {col[0]}")
        except Exception:
            pass  # Already exists

    # Seed known deals
    seeded = 0
    for name_search, stage, value, note, close_by in KNOWN_DEALS:
        rows = conn.execute(
            "SELECT id, name, notes, deal_stage FROM contacts WHERE name LIKE ?",
            (f"%{name_search}%",)
        ).fetchall()
        if not rows:
            print(f"  Not found: {name_search}")
            continue
        for row in rows[:1]:  # Top match only
            note_update = (row["notes"] or "") + f"\n[{NOW[:10]}] {note}" if note else row["notes"]
            conn.execute("""
                UPDATE contacts SET
                  deal_stage = ?,
                  deal_value = ?,
                  deal_close_by = ?,
                  notes = ?,
                  updated_at = ?
                WHERE id = ?
            """, (stage, value, close_by, note_update, NOW, row["id"]))
            print(f"  Deal seeded: {row['name']} → {stage} (close: {close_by or 'ongoing'})")
            seeded += 1

    # Tag Wix leads with deal_stage = 'new_lead'
    wix_count = conn.execute("""
        UPDATE contacts SET deal_stage = 'new_lead', updated_at = ?
        WHERE source LIKE 'wix%' AND (deal_stage IS NULL OR deal_stage = '')
    """, (NOW,)).rowcount
    print(f"  Wix leads tagged: {wix_count}")

    conn.commit()

    # Print pipeline summary
    print("\n=== DEAL PIPELINE ===")
    stages = conn.execute("""
        SELECT deal_stage, COUNT(*) as cnt,
               GROUP_CONCAT(name, ', ') as names
        FROM contacts
        WHERE deal_stage IS NOT NULL AND deal_stage != ''
        GROUP BY deal_stage
        ORDER BY CASE deal_stage
          WHEN 'active' THEN 1
          WHEN 'proposal_sent' THEN 2
          WHEN 'prospecting' THEN 3
          WHEN 'new_lead' THEN 4
          ELSE 5
        END
    """).fetchall()

    for s in stages:
        names_preview = s["names"][:80] + "..." if len(s["names"]) > 80 else s["names"]
        print(f"  [{s['deal_stage']:20s}] {s['cnt']:3d} contacts — {names_preview}")

    conn.close()


if __name__ == "__main__":
    run()
