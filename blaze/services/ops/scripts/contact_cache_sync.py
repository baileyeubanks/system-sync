#!/usr/bin/env python3
"""
contact_cache_sync.py - Sync top contacts from Supabase to local FTS5 SQLite cache.
DB: /Users/_mxappservice/blaze-data/contact_cache.db
Schedule: Every 30 minutes via LaunchAgent
"""
import json, os, sqlite3, sys, time
try:
    from urllib.request import Request, urlopen
    from urllib.error import URLError
except ImportError:
    print("ERROR: urllib not available"); sys.exit(1)

SUPABASE_URL = "https://briokwdoonawhxisbydy.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJyaW9rd2Rvb25hd2h4aXNieWR5Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3MTU1Njc2MiwiZXhwIjoyMDg3MTMyNzYyfQ.5V1BsTrqIHGKUUHYJ3PBpL9re_WzKqOzKoQ94dc3me8"
DB_PATH = "/Users/_mxappservice/blaze-data/contact_cache.db"
BATCH_SIZE = 500
TOTAL_LIMIT = 1500
FIELDS = "id,name,phone,email,company,ai_summary,priority_score,core_rank,metadata"

def fetch_contacts(offset, limit):
    url = SUPABASE_URL + "/rest/v1/contacts?select=" + FIELDS + "&order=core_rank.desc.nullslast&offset=" + str(offset) + "&limit=" + str(limit)
    req = Request(url)
    req.add_header("apikey", SUPABASE_KEY)
    req.add_header("Authorization", "Bearer " + SUPABASE_KEY)
    req.add_header("Content-Type", "application/json")
    try:
        resp = urlopen(req, timeout=30)
        return json.loads(resp.read().decode("utf-8"))
    except URLError as e:
        print("ERROR fetching at offset %d: %s" % (offset, str(e)))
        return []

def init_db(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS contacts (id TEXT PRIMARY KEY, name TEXT, phone TEXT, email TEXT, company TEXT, ai_summary TEXT, orbit TEXT, score REAL, metadata_json TEXT, updated_at REAL)")
    row = conn.execute("SELECT name FROM sqlite_master WHERE type=\'table\' AND name=\'contacts_fts\'").fetchone()
    if not row:
        conn.execute("CREATE VIRTUAL TABLE contacts_fts USING fts5(name, email, company, ai_summary, content=\'contacts\', content_rowid=\'rowid\')")
        conn.execute("CREATE TRIGGER IF NOT EXISTS contacts_ai AFTER INSERT ON contacts BEGIN INSERT INTO contacts_fts(rowid, name, email, company, ai_summary) VALUES (new.rowid, new.name, new.email, new.company, new.ai_summary); END")
        conn.execute("CREATE TRIGGER IF NOT EXISTS contacts_ad AFTER DELETE ON contacts BEGIN INSERT INTO contacts_fts(contacts_fts, rowid, name, email, company, ai_summary) VALUES (\'delete\', old.rowid, old.name, old.email, old.company, old.ai_summary); END")
        conn.execute("CREATE TRIGGER IF NOT EXISTS contacts_au AFTER UPDATE ON contacts BEGIN INSERT INTO contacts_fts(contacts_fts, rowid, name, email, company, ai_summary) VALUES (\'delete\', old.rowid, old.name, old.email, old.company, old.ai_summary); INSERT INTO contacts_fts(rowid, name, email, company, ai_summary) VALUES (new.rowid, new.name, new.email, new.company, new.ai_summary); END")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_contacts_phone ON contacts(phone)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_contacts_email ON contacts(email)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_contacts_name ON contacts(name)")
    conn.commit()

def sync_contacts():
    t0 = time.time()
    db_dir = os.path.dirname(DB_PATH)
    if not os.path.exists(db_dir):
        os.makedirs(db_dir)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    init_db(conn)
    all_contacts = []
    offset = 0
    while offset < TOTAL_LIMIT:
        batch_limit = min(BATCH_SIZE, TOTAL_LIMIT - offset)
        batch = fetch_contacts(offset, batch_limit)
        if not batch:
            break
        all_contacts.extend(batch)
        offset += len(batch)
        if len(batch) < batch_limit:
            break
    print("Fetched %d contacts from Supabase" % len(all_contacts))
    if not all_contacts:
        print("WARNING: No contacts fetched, skipping sync")
        conn.close()
        return
    now = time.time()
    existing_ids = set()
    for row in conn.execute("SELECT id FROM contacts"):
        existing_ids.add(row[0])
    fetched_ids = set()
    upserted = 0
    for c in all_contacts:
        cid = c.get("id", "")
        if not cid:
            continue
        fetched_ids.add(cid)
        meta = c.get("metadata") or {}
        orbit_val = meta.get("orbit", "")
        score_val = c.get("core_rank") or c.get("priority_score") or 0
        vals = (c.get("name", ""), c.get("phone", ""), c.get("email", ""), c.get("company", ""), c.get("ai_summary", ""), str(orbit_val), float(score_val), json.dumps(meta), now)
        conn.execute("INSERT OR REPLACE INTO contacts (id, name, phone, email, company, ai_summary, orbit, score, metadata_json, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (cid,) + vals)
        upserted += 1
    stale = existing_ids - fetched_ids
    if stale:
        placeholders = ",".join(["?" for _ in stale])
        conn.execute("DELETE FROM contacts WHERE id IN (%s)" % placeholders, list(stale))
        print("Removed %d stale contacts" % len(stale))
    conn.commit()
    conn.execute("INSERT INTO contacts_fts(contacts_fts) VALUES (\'rebuild\')")
    conn.commit()
    conn.close()
    elapsed = time.time() - t0
    print("Synced %d contacts in %.1fs -> %s" % (upserted, elapsed, DB_PATH))

def search_test(query):
    if not os.path.exists(DB_PATH):
        print("Cache DB not found at %s" % DB_PATH); return
    conn = sqlite3.connect(DB_PATH)
    t0 = time.time()
    rows = conn.execute("SELECT c.id, c.name, c.phone, c.email, c.company, c.ai_summary, c.score FROM contacts_fts f JOIN contacts c ON c.rowid = f.rowid WHERE contacts_fts MATCH ? ORDER BY c.score DESC LIMIT 5", (chr(34) + query + chr(34),)).fetchall()
    elapsed = (time.time() - t0) * 1000
    print("FTS search: %d results in %.1fms" % (len(rows), elapsed))
    for r in rows:
        print("  %s | %s | %s | %s | rank=%.0f" % (r[1], r[2] or "-", r[3] or "-", r[4] or "-", r[6] or 0))
    conn.close()

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--search":
        if len(sys.argv) < 3:
            print("Usage: contact_cache_sync.py --search <query>"); sys.exit(1)
        search_test(sys.argv[2])
    else:
        sync_contacts()
