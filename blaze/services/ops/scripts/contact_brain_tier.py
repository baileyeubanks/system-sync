#!/usr/bin/env python3
"""
Contact Brain Tier Assignment
- Trims to top 1000 by priority_score
- Assigns enrichment_tier: 10 / 25 / 100 / 500 / 1000
- Adds missing tier columns if not present
"""
import sqlite3

DB = "/Users/_mxappservice/blaze-data/blaze.db"
conn = sqlite3.connect(DB)
conn.execute("PRAGMA journal_mode=WAL")

# Add tier columns if missing
for col, coltype in [
    ("enrichment_tier", "INTEGER DEFAULT 0"),
    ("ai_profile", "TEXT"),
    ("ai_profile_enriched", "TEXT"),
    ("ai_profile_deep", "TEXT"),
    ("ai_profile_live", "TEXT"),
]:
    try:
        conn.execute(f"ALTER TABLE contacts ADD COLUMN {col} {coltype}")
        conn.commit()
    except:
        pass

# Get all contacts ranked by priority_score
rows = conn.execute("""
    SELECT id, name, phone, priority_score
    FROM contacts
    ORDER BY priority_score DESC, interaction_count DESC
""").fetchall()

total = len(rows)
print(f"Total contacts before trim: {total}")

# Keep top 1000, delete the rest
if total > 1000:
    keep_ids = [r[0] for r in rows[:1000]]
    placeholders = ",".join("?" * len(keep_ids))
    deleted = conn.execute(f"DELETE FROM contacts WHERE id NOT IN ({placeholders})", keep_ids).rowcount
    conn.commit()
    print(f"Deleted {deleted} contacts outside top 1000")

# Assign tiers to top 1000
ranked = conn.execute("""
    SELECT id FROM contacts
    ORDER BY priority_score DESC, interaction_count DESC
""").fetchall()

for i, (cid,) in enumerate(ranked):
    rank = i + 1
    tier = 10 if rank <= 10 else 25 if rank <= 25 else 100 if rank <= 100 else 500 if rank <= 500 else 1000
    conn.execute("UPDATE contacts SET enrichment_tier=? WHERE id=?", (tier, cid))

conn.commit()
print("Tiers assigned")

# Stats
total = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
named = conn.execute("SELECT COUNT(*) FROM contacts WHERE name IS NOT NULL AND name != phone AND name != ''").fetchone()[0]
t10 = conn.execute("SELECT COUNT(*) FROM contacts WHERE enrichment_tier=10").fetchone()[0]
t25 = conn.execute("SELECT COUNT(*) FROM contacts WHERE enrichment_tier=25").fetchone()[0]
t100 = conn.execute("SELECT COUNT(*) FROM contacts WHERE enrichment_tier=100").fetchone()[0]
t500 = conn.execute("SELECT COUNT(*) FROM contacts WHERE enrichment_tier=500").fetchone()[0]
t1000 = conn.execute("SELECT COUNT(*) FROM contacts WHERE enrichment_tier=1000").fetchone()[0]

print(f"""
=== CONTACT BRAIN FINAL ===
Total:          {total}
Named:          {named}

Tiers:
  T10  (live, hourly):     {t10}
  T25  (deep enriched):    {t25}
  T100 (AI enriched):      {t100}
  T500 (basic AI):         {t500}
  T1000 (data only):       {t1000}
""")

print("TOP 10:")
rows = conn.execute("""
    SELECT name, phone, orbit, priority_score, category, last_contacted
    FROM contacts
    ORDER BY priority_score DESC
    LIMIT 10
""").fetchall()
for r in rows:
    print(f"  {r[0] or r[1]} | orbit={r[2]} | score={r[3]} | {r[4]} | last={r[5]}")

conn.close()
print("Done.")
