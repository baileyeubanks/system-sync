"""
x_api_manager.py -- X (Twitter) API integration for Blaze V4
Handles: news feed, search, posting drafts, DM monitoring
"""
import json, os, logging
from pathlib import Path

logger = logging.getLogger(__name__)

ENV_FILE = "/Users/_mxappservice/.blaze/env_cache"

def _load_env():
    env = {}
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env

def get_client():
    """Full OAuth 1.0a client — needed for user-context ops (posting, mentions)."""
    import tweepy
    env = _load_env()
    return tweepy.Client(
        bearer_token=env.get("X_BEARER_TOKEN") or None,
        consumer_key=env["X_API_KEY"],
        consumer_secret=env["X_API_SECRET"],
        access_token=env["X_ACCESS_TOKEN"],
        access_token_secret=env["X_ACCESS_TOKEN_SECRET"],
        wait_on_rate_limit=True,
    )

def _bearer_client():
    """App-only client using bearer token — sufficient for search/read."""
    import tweepy
    env = _load_env()
    return tweepy.Client(
        bearer_token=env["X_BEARER_TOKEN"],
        wait_on_rate_limit=True,
    )

def get_news_feed(topics=None, max_results=8):
    """
    Pull relevant X posts for morning briefing news section.
    Uses bearer-token-only auth — no access token required.
    """
    if topics is None:
        topics = [
            "AI video production",
            "content creator business",
            "Houston Texas news",
            "commercial cleaning industry",
        ]

    client = _bearer_client()
    results = []

    for topic in topics[:3]:  # Limit API calls
        try:
            tweets = client.search_recent_tweets(
                query="%s -is:retweet lang:en" % topic,
                max_results=max(10, min(max_results, 100)),
                tweet_fields=["author_id", "created_at", "public_metrics", "text"],
            )
            if tweets.data:
                for t in tweets.data[:2]:
                    metrics = t.public_metrics or {}
                    results.append({
                        "topic": topic,
                        "text": t.text[:120],
                        "engagement": metrics.get("like_count", 0),
                    })
        except Exception as e:
            logger.warning("X search failed for '%s': %s" % (topic, e))

    return results

def get_kallaway_mentions():
    """Check mentions of @Kallaway account."""
    try:
        client = get_client()
        me = client.get_me()
        mentions = client.get_users_mentions(
            id=me.data.id,
            max_results=10,
            tweet_fields=["created_at", "author_id", "text"],
        )
        return [{"text": m.text, "id": str(m.id)} for m in (mentions.data or [])]
    except Exception as e:
        logger.warning("Mentions fetch failed: %s" % e)
        return []

def draft_post(text):
    """
    Stage a post for Bailey approval. NEVER auto-posts.
    Saves to drafts db for Telegram approval flow.
    """
    import sqlite3
    from datetime import datetime

    db = "/Users/_mxappservice/blaze-data/blaze.db"
    conn = sqlite3.connect(db)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS x_drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT,
            created_at TEXT,
            status TEXT DEFAULT 'pending',
            approved_at TEXT
        )
    """)
    conn.execute(
        "INSERT INTO x_drafts (text, created_at) VALUES (?, ?)",
        (text, datetime.now().isoformat())
    )
    conn.commit()
    draft_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    logger.info("Draft saved: id=%d" % draft_id)
    return {"id": draft_id, "text": text, "status": "pending_approval"}

def post_approved(draft_id):
    """Post an approved draft. Called only after Bailey approves via Telegram."""
    import sqlite3
    from datetime import datetime

    db = "/Users/_mxappservice/blaze-data/blaze.db"
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT text FROM x_drafts WHERE id=? AND status='pending'", (draft_id,)).fetchone()

    if not row:
        logger.error("Draft %d not found or already posted" % draft_id)
        return False

    text = row[0]
    try:
        client = get_client()
        response = client.create_tweet(text=text)
        conn.execute(
            "UPDATE x_drafts SET status='posted', approved_at=? WHERE id=?",
            (datetime.now().isoformat(), draft_id)
        )
        conn.commit()
        conn.close()
        logger.info("Posted tweet id=%s" % response.data["id"])
        return True
    except Exception as e:
        logger.error("Post failed: %s" % e)
        conn.close()
        return False

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING)
    print("=== X API Test ===")

    try:
        feed = get_news_feed(["AI content creation", "Houston news"], max_results=5)
        print("News feed: %d items" % len(feed))
        for item in feed[:3]:
            print("  [%s] %s..." % (item["topic"], item["text"][:60]))

    except Exception as e:
        print("FAIL: %s" % e)
        print("X API keys need regeneration at developer.x.com")
