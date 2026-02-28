#!/usr/bin/env python3
"""
Blaze Knowledge Engine — autonomous discovery loop
Runs on Mac Mini (Python 3.9, no backslashes in f-strings)

Modes:
  --harvest     Fetch RSS feeds, score, deduplicate, store to knowledge.db
  --synthesize  Pull week's discoveries, feed to research-worker, send Telegram digest
  --status      Print queue stats and recent top items

LaunchAgents:
  com.blaze.knowledge-harvest   daily 8 PM  -> --harvest
  com.blaze.knowledge-weekly    Sunday 6 AM -> --synthesize
"""

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

DB_PATH = os.path.expanduser("~/blaze-data/knowledge.db")
LOG_PATH = os.path.expanduser("~/blaze-logs/knowledge-engine.log")

# ---------------------------------------------------------------------------
# Feed definitions
# ---------------------------------------------------------------------------
FEEDS = [
    # Energy / Industrial
    {"name": "PV Magazine USA",        "url": "https://pv-magazine-usa.com/feed/",                             "category": "energy_market"},
    {"name": "Wind Power Eng",         "url": "https://www.windpowerengineering.com/feed/",                    "category": "energy_market"},
    {"name": "Offshore Technology",    "url": "https://www.offshore-technology.com/feed/",                     "category": "energy_market"},
    {"name": "Power Engineering",      "url": "https://www.power-eng.com/feed/",                              "category": "energy_market"},
    {"name": "Renewable Energy World", "url": "https://www.renewableenergyworld.com/feed/",                    "category": "energy_market"},
    {"name": "Oil Price",              "url": "https://oilprice.com/rss/main",                                 "category": "energy_market"},
    # Business / Opportunities
    {"name": "Construction Dive",      "url": "https://www.constructiondive.com/feeds/news/",                  "category": "business_intel"},
    {"name": "ISHN Safety",            "url": "https://www.ishn.com/rss/articles",                             "category": "business_intel"},
    # AI & Automation
    {"name": "The Verge AI",           "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "category": "ai_tools"},
    {"name": "TechCrunch AI",          "url": "https://techcrunch.com/category/artificial-intelligence/feed/", "category": "ai_tools"},
    {"name": "Simon Willison",         "url": "https://simonwillison.net/atom/everything/",                    "category": "ai_tools"},
    # Content / Production
    {"name": "No Film School",         "url": "https://nofilmschool.com/feed",                                 "category": "content_tech"},
    {"name": "PetaPixel",              "url": "https://petapixel.com/feed/",                                   "category": "content_tech"},
    {"name": "Filmmaker Magazine",     "url": "https://www.filmmakermagazine.com/feed/",                       "category": "content_tech"},
]

# Scoring: keywords that raise relevance for our verticals
KEYWORDS = {
    "energy_market": [
        "solar", "wind", "refinery", "energy", "offshore", "pipeline",
        "turbine", "renewable", "downstream", "upstream", "oil", "gas",
        "power plant", "grid", "lng", "petrochemical", "industrial",
        "maintenance", "safety", "inspection",
    ],
    "content_tech": [
        "video", "production", "filmmaking", "cinematography", "documentary",
        "post-production", "editing", "workflow", "creative", "camera",
        "ai video", "generative video", "sora",
    ],
    "ai_tools": [
        "ai", "llm", "agent", "gpt", "claude", "automation", "workflow",
        "model", "generative", "copilot", "assistant", "openai", "anthropic",
        "tool", "api",
    ],
    "business_intel": [
        "acquisition", "merger", "contract", "expansion", "funding",
        "industrial", "cleaning", "maintenance", "safety", "osha",
        "facility", "plant", "refinery", "tank", "vessel", "rope access",
    ],
}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS discovery_queue (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            url TEXT UNIQUE NOT NULL,
            title TEXT,
            summary TEXT,
            category TEXT,
            relevance_score INTEGER DEFAULT 0,
            fetched_at TEXT NOT NULL,
            processed INTEGER DEFAULT 0,
            week_start TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_dq_week ON discovery_queue(week_start);
        CREATE INDEX IF NOT EXISTS idx_dq_processed ON discovery_queue(processed);
        CREATE TABLE IF NOT EXISTS knowledge_insights (
            id TEXT PRIMARY KEY,
            week_start TEXT NOT NULL,
            insight TEXT NOT NULL,
            created_at TEXT NOT NULL,
            sent_to_telegram INTEGER DEFAULT 0
        );
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# RSS parsing (handles RSS 2.0 and Atom)
# ---------------------------------------------------------------------------
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "dc":   "http://purl.org/dc/elements/1.1/",
    "content": "http://purl.org/rss/1.0/modules/content/",
}


def fetch_feed(feed):
    url = feed["url"]
    req = urllib.request.Request(url, headers={"User-Agent": "BlazeKnowledgeEngine/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
    except Exception as e:
        return [], str(e)

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        return [], str(e)

    items = []
    tag = root.tag.lower()

    if "rss" in tag or root.find("channel") is not None:
        # RSS 2.0
        channel_el = root.find("channel")
        channel = channel_el if channel_el is not None else root
        for item in channel.findall("item"):
            title = (item.findtext("title") or "").strip()
            link  = (item.findtext("link") or "").strip()
            desc  = (item.findtext("description") or "").strip()
            if link:
                items.append({"title": title, "url": link, "summary": desc[:400]})
    else:
        # Atom
        ns = ""
        if "{" in root.tag:
            ns = root.tag.split("}")[0] + "}"
        for entry in root.findall(ns + "entry"):
            title = (entry.findtext(ns + "title") or "").strip()
            link_el = entry.find(ns + "link")
            link = (link_el.get("href") if link_el is not None else "") or ""
            summary = (entry.findtext(ns + "summary") or entry.findtext(ns + "content") or "").strip()
            if link:
                items.append({"title": title, "url": link, "summary": summary[:400]})

    return items, None


def score_item(title, summary, category):
    text = (title + " " + summary).lower()
    score = 0
    for kw in KEYWORDS.get(category, []):
        if kw in text:
            score += 2
    # Boost if keywords from other high-value categories also appear
    for kw in KEYWORDS.get("energy_market", []):
        if kw in text:
            score += 1
    return min(score, 20)


def week_start():
    today = datetime.now(timezone.utc).date()
    return (today - timedelta(days=today.weekday())).isoformat()  # Monday


# ---------------------------------------------------------------------------
# Harvest mode
# ---------------------------------------------------------------------------
def cmd_harvest():
    conn = get_db()
    init_tables(conn)
    ws = week_start()
    now = datetime.now(timezone.utc).isoformat()

    total_new = 0
    total_seen = 0

    for feed in FEEDS:
        items, err = fetch_feed(feed)
        if err:
            log(f"FEED ERROR [{feed['name']}]: {err}")
            continue

        new_for_feed = 0
        for item in items:
            url = item["url"]
            if not url:
                continue
            item_id = hashlib.sha256(url.encode()).hexdigest()[:16]
            score = score_item(item["title"], item["summary"], feed["category"])
            # Only store items with some relevance (score >= 1) unless energy/business feed
            if score == 0 and feed["category"] not in ("energy_market", "business_intel"):
                total_seen += 1
                continue
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO discovery_queue "
                    "(id, source, url, title, summary, category, relevance_score, fetched_at, week_start) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (item_id, feed["name"], url, item["title"][:300], item["summary"], feed["category"], score, now, ws)
                )
                if conn.total_changes > 0:
                    new_for_feed += 1
            except sqlite3.Error as e:
                log(f"DB ERROR: {e}")

        conn.commit()
        log(f"  [{feed['name']}] {len(items)} fetched, {new_for_feed} new")
        total_new += new_for_feed
        total_seen += len(items)

    log(f"Harvest complete: {total_new} new items (from {total_seen} fetched)")

    # Prune items older than 30 days
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    conn.execute("DELETE FROM discovery_queue WHERE fetched_at < ?", (cutoff,))
    conn.commit()
    conn.close()
    print(f"Harvest: {total_new} new discoveries stored.")


# ---------------------------------------------------------------------------
# Synthesize mode
# ---------------------------------------------------------------------------
def cmd_synthesize():
    conn = get_db()
    init_tables(conn)
    ws = week_start()

    # Check if we already sent this week
    existing = conn.execute(
        "SELECT id FROM knowledge_insights WHERE week_start=? AND sent_to_telegram=1", (ws,)
    ).fetchone()
    if existing:
        print(f"Synthesis already sent for week {ws}. Skipping.")
        conn.close()
        return

    # Pull this week's discoveries, top 40 by score
    rows = conn.execute(
        "SELECT title, summary, source, category, relevance_score, url "
        "FROM discovery_queue WHERE week_start=? "
        "ORDER BY relevance_score DESC LIMIT 40",
        (ws,)
    ).fetchall()

    if not rows:
        log(f"Synthesize: no discoveries for week {ws}. Skipping.")
        conn.close()
        return

    # Format discoveries for the agent prompt
    by_cat = {}
    for row in rows:
        cat = row["category"]
        by_cat.setdefault(cat, []).append(row)

    sections = []
    cat_labels = {
        "energy_market": "ENERGY MARKET",
        "ai_tools": "AI & AUTOMATION",
        "content_tech": "CONTENT & PRODUCTION",
        "business_intel": "BUSINESS INTELLIGENCE",
    }
    for cat, label in cat_labels.items():
        items = by_cat.get(cat, [])[:10]
        if not items:
            continue
        lines = [f"[{label}]"]
        for item in items:
            lines.append(f"- {item['title']} ({item['source']})")
            if item["summary"]:
                lines.append(f"  {item['summary'][:120]}")
        sections.append("\n".join(lines))

    discoveries_text = "\n\n".join(sections)
    n = len(rows)

    prompt = (
        "You are a strategic intelligence analyst for Bailey Eubanks. "
        "Content Co-op produces industrial energy video content. "
        "ACS is an industrial cleaning and maintenance services company.\n\n"
        "Week of " + ws + " — " + str(n) + " discoveries:\n\n"
        + discoveries_text + "\n\n"
        "Create a 'Week in Brief' for Bailey. Include:\n"
        "1. ENERGY MARKET — 2-3 developments relevant to energy industry clients\n"
        "2. CONTENT & TECH — Tools or trends for Content Co-op to consider\n"
        "3. AI & AUTOMATION — 1-2 new capabilities worth evaluating\n"
        "4. ACS OPPORTUNITIES — Any industrial/maintenance signals for ACS\n"
        "5. ACTION ITEMS — 1-3 specific things worth acting on this week\n\n"
        "Be direct and specific. Max 350 words. No fluff."
    )

    # Run research-worker via OpenClaw CLI
    openclaw_bin = "/usr/local/bin/openclaw"
    env = os.environ.copy()
    env["PATH"] = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"

    try:
        result = subprocess.run(
            [
                openclaw_bin, "agent",
                "--agent", "research-worker",
                "--message", prompt,
                "--deliver",
                "--reply-channel", "telegram",
                "--reply-to", "telegram:7747110667",
                "--reply-account", "main",
                "--json",
            ],
            capture_output=True, text=True, timeout=120, env=env
        )
        output = result.stdout.strip()
        log(f"Synthesis OpenClaw exit={result.returncode}")
        if result.returncode != 0:
            log(f"Synthesis stderr: {result.stderr[:500]}")
    except subprocess.TimeoutExpired:
        log("Synthesis timed out after 120s")
        conn.close()
        return
    except Exception as e:
        log(f"Synthesis failed: {e}")
        conn.close()
        return

    # Store insight + mark sent
    insight_id = hashlib.sha256(ws.encode()).hexdigest()[:12]
    conn.execute(
        "INSERT OR REPLACE INTO knowledge_insights (id, week_start, insight, created_at, sent_to_telegram) "
        "VALUES (?,?,?,?,?)",
        (insight_id, ws, prompt[:1000], datetime.now(timezone.utc).isoformat(), 1)
    )
    # Mark items as processed
    conn.execute("UPDATE discovery_queue SET processed=1 WHERE week_start=?", (ws,))
    conn.commit()
    conn.close()
    print(f"Synthesis complete. Week {ws}, {n} items synthesized.")


# ---------------------------------------------------------------------------
# Status mode
# ---------------------------------------------------------------------------
def cmd_status():
    conn = get_db()
    init_tables(conn)
    ws = week_start()

    total = conn.execute("SELECT COUNT(*) FROM discovery_queue").fetchone()[0]
    this_week = conn.execute("SELECT COUNT(*) FROM discovery_queue WHERE week_start=?", (ws,)).fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM discovery_queue WHERE processed=0").fetchone()[0]
    by_cat = conn.execute(
        "SELECT category, COUNT(*) as n FROM discovery_queue WHERE week_start=? GROUP BY category", (ws,)
    ).fetchall()
    top5 = conn.execute(
        "SELECT title, source, relevance_score FROM discovery_queue "
        "WHERE week_start=? ORDER BY relevance_score DESC LIMIT 5", (ws,)
    ).fetchall()
    insights = conn.execute("SELECT week_start, sent_to_telegram FROM knowledge_insights ORDER BY created_at DESC LIMIT 3").fetchall()
    conn.close()

    print(f"Knowledge Engine Status — week of {ws}")
    print(f"  Total in DB: {total} | This week: {this_week} | Pending synthesis: {pending}")
    print(f"  By category:")
    for row in by_cat:
        print(f"    {row['category']}: {row['n']}")
    print(f"  Top 5 this week:")
    for row in top5:
        print(f"    [{row['relevance_score']:2d}] {row['title'][:70]} ({row['source']})")
    print(f"  Recent digests:")
    for row in insights:
        sent = "sent" if row["sent_to_telegram"] else "not sent"
        print(f"    Week {row['week_start']}: {sent}")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = "[" + ts + "] " + msg
    print(line)
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Blaze Knowledge Engine")
    parser.add_argument("--harvest", action="store_true", help="Fetch RSS feeds and store discoveries")
    parser.add_argument("--synthesize", action="store_true", help="Synthesize week's discoveries and send digest")
    parser.add_argument("--status", action="store_true", help="Print queue stats")
    args = parser.parse_args()

    if args.harvest:
        log("=== HARVEST START ===")
        cmd_harvest()
    elif args.synthesize:
        log("=== SYNTHESIS START ===")
        cmd_synthesize()
    elif args.status:
        cmd_status()
    else:
        parser.print_help()
