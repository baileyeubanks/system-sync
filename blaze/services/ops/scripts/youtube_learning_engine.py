#!/usr/bin/env python3
"""
YouTube Learning Engine V4 — Blaze Intelligence Layer

Autonomous research system that:
  1. Monitors 30+ curated YouTube channels via RSS (no API key needed)
  2. Pulls transcripts via youtube-transcript-api
  3. Summarizes + extracts insights via local Ollama models (deepseek-r1)
  4. Stores everything in knowledge.db
  5. Feeds morning briefing LEARNING section
  6. Keeps queue at 50 pending videos — "one gets done, another is ready to load"

Python 3.9 compatible. $0 cost. 100% local + private.

Usage:
  python3 youtube_learning_engine.py             # Full cycle: fill queue + process next
  python3 youtube_learning_engine.py --fill       # Only refill queue from RSS
  python3 youtube_learning_engine.py --process    # Only process next pending video
  python3 youtube_learning_engine.py --status     # Show queue status
  python3 youtube_learning_engine.py --briefing   # Output for morning briefing
  python3 youtube_learning_engine.py --daemon     # Run continuously (cron alternative)
"""

import json, os, sys, sqlite3, logging, time, re, subprocess
from datetime import datetime, timedelta
from xml.etree import ElementTree
from urllib.request import urlopen, Request
from urllib.error import URLError

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DATA_DIR = "/Users/_mxappservice/blaze-data"
KNOWLEDGE_DB = os.path.join(DATA_DIR, "knowledge.db")
LOG_FILE = os.path.join(DATA_DIR, "logs", "youtube_learning.log")
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

TARGET_QUEUE_SIZE = 50
OLLAMA_URL = "http://localhost:11434"
FAST_MODEL = "deepseek-r1:7b"
DEEP_MODEL = "deepseek-r1:14b"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger("youtube_learning")

# ---------------------------------------------------------------------------
# Channel list — merged business + tech (the original V3 curation + business)
# ---------------------------------------------------------------------------
CHANNELS = [
    # Tier 1: Business Wisdom + Finance
    {"id": "UCIaH-gZIVC432YRjNVvnyCA", "name": "Acquired", "cat": "business-history", "priority": 100,
     "why": "How Disney/LVMH/Costco built moats — business history for strategic thinking"},
    {"id": "UCGy2GclSbhfFha9rEJMSGXg", "name": "Alex Hormozi", "cat": "sales-scaling", "priority": 100,
     "why": "Sales tactics, $100M Offers, stop chasing invoices — attract better clients"},
    {"id": "UCKeGnPIO_dEhklTSv0IzlMA", "name": "Patrick Boyle", "cat": "finance", "priority": 95,
     "why": "Finance with humor — economic context, avoid financial mistakes"},
    {"id": "UCGENKMi5NUPjMVqLHCPMu_w", "name": "All-In Podcast", "cat": "tech-finance", "priority": 95,
     "why": "Economic trends from successful founders — macro context"},
    {"id": "UCuCkxoKLYO_EQ2GeFtbM_bw", "name": "Graham Stephan", "cat": "wealth", "priority": 85,
     "why": "Wealth building, real estate, financial discipline"},

    # Tier 2: Pricing, Scaling, AI Tools
    {"id": "UCG1o5O30GZmdi9ohXJVGOBQ", "name": "Chris Do / The Futur", "cat": "creative-pricing", "priority": 100,
     "why": "10x pricing for creatives — THIS IS YOUR WORLD"},
    {"id": "UCn-EnGBdo4gf1bx3BnFq3qA", "name": "Matt Wolfe", "cat": "ai-tools", "priority": 95,
     "why": "Practical AI tools, not hype — stay current on what works"},
    {"id": "UCcefcZRL2oaA_uBNeo5UOWg", "name": "Y Combinator", "cat": "startups", "priority": 90,
     "why": "Startup wisdom, growth tactics, business fundamentals"},
    {"id": "UClgihdkPzNDtuoQy4xDw5mA", "name": "Dan Martell", "cat": "service-scaling", "priority": 95,
     "why": "Systems for scaling service businesses — directly applicable to ACS + CC"},

    # Tier 3: Local Business / Service Businesses
    {"id": "UCSmgFCIG6o8sDLF6Ewjq1SQ", "name": "Codie Sanchez", "cat": "small-biz", "priority": 95,
     "why": "Small business investing, boring businesses — scale Astro into a real business"},
    {"id": "UCaO6TYtlC8U5ttz62hTrZgg", "name": "My First Million", "cat": "biz-ideas", "priority": 90,
     "why": "Business ideas in chaos — opportunity spotting"},

    # Tier 4: Creator Economy + Video Business
    {"id": "UCamLstJyCa-t5gfZegxsFMw", "name": "Colin and Samir", "cat": "creator-economy", "priority": 90,
     "why": "Creator business models — how to monetize video beyond production"},
    {"id": "UCBJycsmduvYEL83R_U4JriQ", "name": "MKBHD", "cat": "video-empire", "priority": 85,
     "why": "Video empire building, tech production quality"},
    {"id": "UC3DkFux8Iv-aYnTRWzwaiBA", "name": "Peter McKinnon", "cat": "video-biz", "priority": 85,
     "why": "Video business tactics, creative entrepreneurship"},

    # Tier 5: AI + Tech (staying sharp)
    {"id": "UCsBjURrPoezykLs9EqgamOA", "name": "Fireship", "cat": "tech-ai", "priority": 100,
     "why": "Fast tech + AI news, modern frameworks — stay current in 5 min"},
    {"id": "UCW-gUiQP2dCLBAEMKcfk7Ow", "name": "Josh tried coding", "cat": "ai-coding", "priority": 95,
     "why": "AI coding tools (Cursor, Claude, v0) — exactly what Bailey uses"},
    {"id": "UCehTG9BfYt1xsBwzEPvhdKw", "name": "Theo - t3.gg", "cat": "architecture", "priority": 90,
     "why": "TypeScript, Next.js, architecture decisions — industry best practices"},

    # Tier 6: Mindset + Wealth Philosophy
    {"id": "UCPswBMwFhOwC6m053v_SFZQ", "name": "Naval", "cat": "philosophy", "priority": 85,
     "why": "Wealth without selling time — foundational thinking"},
    {"id": "UCIgKGGJkt1MrNmhq3vKOhWQ", "name": "The Plain Bagel", "cat": "finance-basics", "priority": 85,
     "why": "Wealth building basics, investing fundamentals"},
]


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(KNOWLEDGE_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_schema():
    """Add youtube tables to knowledge.db if they don't exist."""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS youtube_channels (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT,
            priority INTEGER DEFAULT 90,
            why TEXT,
            last_fetched TEXT,
            video_count INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS youtube_queue (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            channel_name TEXT,
            channel_id TEXT,
            url TEXT,
            published TEXT,
            status TEXT DEFAULT 'pending',
            priority INTEGER DEFAULT 90,
            transcript TEXT,
            summary TEXT,
            added_at TEXT,
            started_at TEXT,
            completed_at TEXT,
            error TEXT
        );

        CREATE TABLE IF NOT EXISTS youtube_insights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id TEXT NOT NULL,
            video_title TEXT,
            channel_name TEXT,
            business_unit TEXT DEFAULT 'both',
            insight TEXT NOT NULL,
            relevance_score INTEGER DEFAULT 5,
            category TEXT,
            actionable INTEGER DEFAULT 0,
            action_item TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (video_id) REFERENCES youtube_queue(id)
        );

        CREATE INDEX IF NOT EXISTS idx_queue_status ON youtube_queue(status);
        CREATE INDEX IF NOT EXISTS idx_insights_score ON youtube_insights(relevance_score DESC);
        CREATE INDEX IF NOT EXISTS idx_insights_unit ON youtube_insights(business_unit);
    """)
    conn.commit()

    # Upsert channels
    for ch in CHANNELS:
        conn.execute("""
            INSERT INTO youtube_channels (id, name, category, priority, why)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, category=excluded.category,
                priority=excluded.priority, why=excluded.why
        """, (ch["id"], ch["name"], ch["cat"], ch["priority"], ch["why"]))
    conn.commit()
    conn.close()
    log.info("Schema initialized, %d channels loaded" % len(CHANNELS))


# ---------------------------------------------------------------------------
# RSS Fetcher — no API key needed
# ---------------------------------------------------------------------------

def fetch_channel_rss(channel_id, max_results=15):
    """Fetch latest videos from YouTube RSS feed. Returns list of dicts."""
    url = "https://www.youtube.com/feeds/videos.xml?channel_id=%s" % channel_id
    try:
        req = Request(url, headers={"User-Agent": "BlazeV4/1.0"})
        resp = urlopen(req, timeout=15)
        xml = resp.read().decode("utf-8")
    except Exception as e:
        log.warning("RSS fetch failed for %s: %s" % (channel_id, e))
        return []

    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015",
        "media": "http://search.yahoo.com/mrss/",
    }

    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as e:
        log.warning("XML parse failed for %s: %s" % (channel_id, e))
        return []

    entries = root.findall("atom:entry", ns)
    videos = []
    for entry in entries[:max_results]:
        vid_el = entry.find("yt:videoId", ns)
        title_el = entry.find("atom:title", ns)
        pub_el = entry.find("atom:published", ns)
        author_el = entry.find("atom:author/atom:name", ns)

        if vid_el is None:
            continue

        vid = vid_el.text
        title = title_el.text if title_el is not None else ""
        published = pub_el.text if pub_el is not None else ""
        channel = author_el.text if author_el is not None else ""

        videos.append({
            "id": vid,
            "title": title,
            "channel": channel,
            "channel_id": channel_id,
            "published": published,
            "url": "https://www.youtube.com/watch?v=%s" % vid,
        })

    return videos


# ---------------------------------------------------------------------------
# Queue Management
# ---------------------------------------------------------------------------

def get_queue_status():
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM youtube_queue").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM youtube_queue WHERE status='pending'").fetchone()[0]
    processing = conn.execute("SELECT COUNT(*) FROM youtube_queue WHERE status='processing'").fetchone()[0]
    completed = conn.execute("SELECT COUNT(*) FROM youtube_queue WHERE status='completed'").fetchone()[0]
    failed = conn.execute("SELECT COUNT(*) FROM youtube_queue WHERE status='failed'").fetchone()[0]
    insights = conn.execute("SELECT COUNT(*) FROM youtube_insights").fetchone()[0]
    conn.close()
    return {
        "total": total, "pending": pending, "processing": processing,
        "completed": completed, "failed": failed, "insights": insights,
        "needs_refill": pending < TARGET_QUEUE_SIZE,
    }


def fill_queue():
    """Fetch RSS from all channels, add new videos to queue up to target."""
    conn = get_db()
    existing = set(r[0] for r in conn.execute("SELECT id FROM youtube_queue").fetchall())
    pending_count = conn.execute("SELECT COUNT(*) FROM youtube_queue WHERE status='pending'").fetchone()[0]

    needed = TARGET_QUEUE_SIZE - pending_count
    if needed <= 0:
        log.info("Queue full (%d pending). No refill needed." % pending_count)
        conn.close()
        return 0

    log.info("Need %d more videos (currently %d pending)" % (needed, pending_count))

    # Sort channels by priority
    channels = sorted(CHANNELS, key=lambda c: c["priority"], reverse=True)
    added = 0

    for ch in channels:
        if added >= needed:
            break

        log.info("Fetching RSS: %s (%s)" % (ch["name"], ch["id"]))
        videos = fetch_channel_rss(ch["id"])

        if not videos:
            continue

        now = datetime.now().isoformat()
        for v in videos:
            if added >= needed:
                break
            if v["id"] in existing:
                continue

            conn.execute("""
                INSERT OR IGNORE INTO youtube_queue
                    (id, title, channel_name, channel_id, url, published, status, priority, added_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            """, (v["id"], v["title"], v["channel"], v["channel_id"],
                  v["url"], v["published"], ch["priority"], now))
            existing.add(v["id"])
            added += 1
            log.info("  + %s" % v["title"][:60])

        # Update channel last_fetched
        conn.execute(
            "UPDATE youtube_channels SET last_fetched=?, video_count=video_count+? WHERE id=?",
            (now, len(videos), ch["id"])
        )
        conn.commit()

    conn.commit()
    conn.close()
    log.info("Queue refill complete: added %d videos" % added)
    return added


# ---------------------------------------------------------------------------
# Transcript Extraction
# ---------------------------------------------------------------------------

def get_transcript(video_id):
    """Get YouTube transcript via youtube-transcript-api. Returns text or None."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        ytt_api = YouTubeTranscriptApi()
        transcript_list = ytt_api.fetch(video_id)
        # Join all snippets
        text = " ".join(snippet.text for snippet in transcript_list)
        # Clean up
        text = re.sub(r'\[.*?\]', '', text)  # Remove [Music] etc
        text = re.sub(r'\s+', ' ', text).strip()
        return text
    except Exception as e:
        log.warning("Transcript failed for %s: %s" % (video_id, e))
        return None


# ---------------------------------------------------------------------------
# Ollama Integration
# ---------------------------------------------------------------------------

def ollama_generate(prompt, model=FAST_MODEL, timeout=120):
    """Call Ollama API for text generation. Returns response text."""
    import json as _json
    from urllib.request import urlopen, Request

    url = "%s/api/generate" % OLLAMA_URL
    payload = _json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 2048}
    }).encode("utf-8")

    req = Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        resp = urlopen(req, timeout=timeout)
        result = _json.loads(resp.read().decode("utf-8"))
        return result.get("response", "").strip()
    except Exception as e:
        log.error("Ollama call failed (%s): %s" % (model, e))
        return None


def summarize_transcript(title, channel, transcript):
    """Use fast model to create a concise summary."""
    # Truncate very long transcripts to ~8000 chars
    if len(transcript) > 8000:
        transcript = transcript[:4000] + " [...] " + transcript[-4000:]

    prompt = """Summarize this YouTube video transcript concisely.

VIDEO: "%s" by %s

TRANSCRIPT:
%s

Provide:
1. SUMMARY (2-3 sentences, what the video covers)
2. KEY POINTS (3-5 bullet points, most important takeaways)
3. APPLICABLE TO (who benefits: "Content Co-op", "Astro Cleaning", or "Both")

Be direct. No filler.""" % (title, channel, transcript)

    return ollama_generate(prompt, model=FAST_MODEL, timeout=180)


def extract_insights(title, channel, summary, transcript_snippet):
    """Use deep model to extract actionable business insights."""
    prompt = """You are a business intelligence analyst for Bailey Eubanks.

BAILEY'S CONTEXT:
- Runs Content Co-op (video production for corporate clients like BP, Schneider Electric)
- Runs Astro Cleaning Services (residential cleaning in Houston)
- YouTube channel: Kallaway (creative entrepreneurship)
- Under financial pressure, adapting to AI disruption
- Needs: better pricing, more clients, operational efficiency, AI leverage

VIDEO: "%s" by %s

SUMMARY: %s

TRANSCRIPT EXCERPT: %s

Extract 1-3 ACTIONABLE insights. For each:
- INSIGHT: One clear sentence
- RELEVANCE: Score 1-10 (10 = directly solves a current problem)
- BUSINESS: "CC" (Content Co-op), "ACS" (Astro Cleaning), or "BOTH"
- CATEGORY: pricing / sales / operations / ai-tools / marketing / finance / mindset
- ACTION: Specific thing Bailey can do THIS WEEK (or "none" if just context)

Format as JSON array:
[{"insight":"...","relevance":N,"business":"...","category":"...","action":"..."}]

Only include insights scoring 6+. If nothing is relevant, return [].""" % (
        title, channel, summary, transcript_snippet[:3000])

    return ollama_generate(prompt, model=DEEP_MODEL, timeout=240)


# ---------------------------------------------------------------------------
# Video Processing Pipeline
# ---------------------------------------------------------------------------

def process_next_video():
    """Pick the highest-priority pending video, transcribe, summarize, extract insights."""
    conn = get_db()

    # Get next pending video (highest priority first, then oldest)
    row = conn.execute("""
        SELECT id, title, channel_name, url, priority
        FROM youtube_queue
        WHERE status = 'pending'
        ORDER BY priority DESC, added_at ASC
        LIMIT 1
    """).fetchone()

    if not row:
        log.info("No pending videos in queue.")
        conn.close()
        return None

    vid_id = row["id"]
    title = row["title"]
    channel = row["channel_name"]

    log.info("Processing: %s — %s" % (channel, title))

    # Mark as processing
    conn.execute(
        "UPDATE youtube_queue SET status='processing', started_at=? WHERE id=?",
        (datetime.now().isoformat(), vid_id)
    )
    conn.commit()

    # Step 1: Get transcript
    transcript = get_transcript(vid_id)
    if not transcript:
        log.warning("No transcript available for %s — marking failed" % vid_id)
        conn.execute(
            "UPDATE youtube_queue SET status='failed', error='no transcript' WHERE id=?",
            (vid_id,)
        )
        conn.commit()
        conn.close()
        return None

    log.info("Transcript: %d chars" % len(transcript))

    # Step 2: Summarize (fast model)
    summary = summarize_transcript(title, channel, transcript)
    if not summary:
        log.warning("Summary failed for %s" % vid_id)
        conn.execute(
            "UPDATE youtube_queue SET status='failed', error='summary failed', transcript=? WHERE id=?",
            (transcript[:5000], vid_id)
        )
        conn.commit()
        conn.close()
        return None

    log.info("Summary complete (%d chars)" % len(summary))

    # Step 3: Extract insights (deep model)
    raw_insights = extract_insights(title, channel, summary, transcript)
    insights_saved = 0

    if raw_insights:
        # Parse JSON from response (may have thinking tags or extra text)
        json_match = re.search(r'\[.*\]', raw_insights, re.DOTALL)
        if json_match:
            try:
                insights_list = json.loads(json_match.group())
                for ins in insights_list:
                    if not isinstance(ins, dict):
                        continue
                    relevance = ins.get("relevance", 5)
                    if relevance < 6:
                        continue

                    biz = ins.get("business", "BOTH").upper()
                    if biz == "CC":
                        biz_unit = "CC"
                    elif biz == "ACS":
                        biz_unit = "ACS"
                    else:
                        biz_unit = "both"

                    action = ins.get("action", "")
                    is_actionable = 1 if (action and action.lower() != "none") else 0

                    conn.execute("""
                        INSERT INTO youtube_insights
                            (video_id, video_title, channel_name, business_unit,
                             insight, relevance_score, category, actionable, action_item)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        vid_id, title, channel, biz_unit,
                        ins.get("insight", ""),
                        relevance,
                        ins.get("category", "general"),
                        is_actionable,
                        action if is_actionable else None,
                    ))
                    insights_saved += 1
            except json.JSONDecodeError:
                log.warning("Could not parse insights JSON for %s" % vid_id)

    # Step 4: Mark completed
    conn.execute("""
        UPDATE youtube_queue
        SET status='completed', transcript=?, summary=?, completed_at=?
        WHERE id=?
    """, (transcript[:10000], summary, datetime.now().isoformat(), vid_id))
    conn.commit()
    conn.close()

    log.info("DONE: %s — %d insights saved" % (title[:50], insights_saved))
    return {"id": vid_id, "title": title, "channel": channel, "insights": insights_saved}


# ---------------------------------------------------------------------------
# Morning Briefing Integration
# ---------------------------------------------------------------------------

def get_briefing_text():
    """Generate LEARNING section for morning briefing."""
    conn = get_db()

    # Recent insights (last 48 hours, top scored)
    cutoff = (datetime.now() - timedelta(hours=48)).isoformat()
    insights = conn.execute("""
        SELECT insight, relevance_score, channel_name, category, action_item, business_unit
        FROM youtube_insights
        WHERE created_at > ? AND relevance_score >= 7
        ORDER BY relevance_score DESC
        LIMIT 5
    """, (cutoff,)).fetchall()

    # Queue status
    status = get_queue_status()

    # Videos processed in last 24h
    day_cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
    recent = conn.execute("""
        SELECT title, channel_name FROM youtube_queue
        WHERE status='completed' AND completed_at > ?
        ORDER BY completed_at DESC LIMIT 3
    """, (day_cutoff,)).fetchall()

    conn.close()

    lines = []

    if insights:
        for ins in insights:
            score = ins["relevance_score"]
            biz = ins["business_unit"].upper()
            tag = "[%s] " % biz if biz != "BOTH" else ""
            lines.append("  %s(%d/10) %s" % (tag, score, ins["insight"]))
            if ins["action_item"]:
                lines.append("    -> ACTION: %s" % ins["action_item"])
    else:
        lines.append("  No new high-value insights in last 48h.")

    lines.append("")
    lines.append("  Queue: %d pending | %d completed | %d insights total" % (
        status["pending"], status["completed"], status["insights"]))

    if recent:
        lines.append("  Recently processed:")
        for r in recent:
            lines.append("    - %s (%s)" % (r["title"][:50], r["channel_name"]))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Status Display
# ---------------------------------------------------------------------------

def print_status():
    s = get_queue_status()
    print("\nYOUTUBE LEARNING ENGINE — STATUS")
    print("=" * 48)
    print("  Pending:    %d / %d target" % (s["pending"], TARGET_QUEUE_SIZE))
    print("  Processing: %d" % s["processing"])
    print("  Completed:  %d" % s["completed"])
    print("  Failed:     %d" % s["failed"])
    print("  Insights:   %d total" % s["insights"])
    print("  Needs fill: %s" % ("YES" if s["needs_refill"] else "no"))

    conn = get_db()
    top = conn.execute("""
        SELECT insight, relevance_score, channel_name, action_item
        FROM youtube_insights
        ORDER BY created_at DESC LIMIT 5
    """).fetchall()
    conn.close()

    if top:
        print("\n  LATEST INSIGHTS:")
        for ins in top:
            print("  [%d/10] %s" % (ins["relevance_score"], ins["insight"][:70]))
            if ins["action_item"]:
                print("         -> %s" % ins["action_item"][:60])
    print()


# ---------------------------------------------------------------------------
# Daemon Mode
# ---------------------------------------------------------------------------

def daemon():
    """Run continuously: fill queue every 6 hours, process a video every 10 min."""
    log.info("DAEMON MODE — starting continuous learning loop")
    last_fill = 0
    FILL_INTERVAL = 6 * 3600  # 6 hours
    PROCESS_INTERVAL = 600    # 10 minutes

    while True:
        now = time.time()

        # Refill queue periodically
        if now - last_fill > FILL_INTERVAL:
            try:
                fill_queue()
                last_fill = now
            except Exception as e:
                log.error("Fill queue error: %s" % e)

        # Process next video
        try:
            result = process_next_video()
            if result:
                log.info("Processed: %s (%d insights)" % (
                    result["title"][:40], result["insights"]))
        except Exception as e:
            log.error("Process error: %s" % e)

        log.info("Sleeping %d seconds..." % PROCESS_INTERVAL)
        time.sleep(PROCESS_INTERVAL)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    init_schema()

    args = sys.argv[1:]

    if "--status" in args:
        print_status()

    elif "--fill" in args:
        added = fill_queue()
        print("Added %d videos to queue." % added)

    elif "--process" in args:
        result = process_next_video()
        if result:
            print("Processed: %s — %d insights" % (result["title"], result["insights"]))
        else:
            print("No videos to process.")

    elif "--briefing" in args:
        print(get_briefing_text())

    elif "--daemon" in args:
        daemon()

    else:
        # Default: fill + process one
        fill_queue()
        result = process_next_video()
        if result:
            print("Processed: %s — %d insights" % (result["title"], result["insights"]))
        print_status()


if __name__ == "__main__":
    main()
