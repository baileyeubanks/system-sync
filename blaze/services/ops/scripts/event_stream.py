#!/usr/bin/env python3
"""
event_stream.py — Blaze V4 Intelligence Stream Daemon

Polls all sources, scores events, pushes high-priority, logs everything.
Run via cron: */2 * * * * python3 .../event_stream.py >> .../logs/event_stream.log 2>&1
"""
import sys, os, json, sqlite3, hashlib, logging, time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.dirname(__file__))
from blaze_helper import log_cron

DATA_ROOT = "/Users/_mxappservice/blaze-data"
EVENT_LOG_DB = "%s/event_stream.db" % DATA_ROOT
STATE_FILE = "%s/event_stream_state.json" % DATA_ROOT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("event_stream")


# ──────────────────────────────────────────────
# DB Setup
# ──────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect(EVENT_LOG_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            sender TEXT,
            subject TEXT,
            body TEXT,
            score INTEGER DEFAULT 0,
            context TEXT,
            action TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            raw_id TEXT UNIQUE
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_source ON events(source)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_score ON events(score)")
    # Add business_unit column if not present
    try:
        conn.execute("ALTER TABLE events ADD COLUMN business_unit TEXT DEFAULT 'BOTH'")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists
    conn.commit()
    return conn


def event_exists(conn, raw_id):
    row = conn.execute("SELECT 1 FROM events WHERE raw_id=?", (raw_id,)).fetchone()
    return row is not None


def insert_event(conn, source, sender, subject, body, score, context, action, raw_id,
                 business_unit=None):
    try:
        conn.execute(
            "INSERT INTO events (source, sender, subject, body, score, context, action, "
            "raw_id, business_unit) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (source, sender, subject, body, score, context, action, raw_id,
             business_unit or "BOTH")
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


# ──────────────────────────────────────────────
# State persistence
# ──────────────────────────────────────────────

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ──────────────────────────────────────────────
# Pollers
# ──────────────────────────────────────────────

def poll_gmail(conn):
    """Poll Gmail for new unread emails (2 accounts)."""
    from google_api_manager import get_api
    events = []
    api = get_api()

    for account in ["bailey@contentco-op.com", "caio@astrocleanings.com"]:
        try:
            ws = api.workspace(account)
            msgs = ws.execute(
                ws.gmail.users().messages().list(
                    userId="me",
                    q="is:unread is:inbox -category:promotions -category:updates",
                    maxResults=10
                )
            )
            for msg in msgs.get("messages", []):
                raw_id = "gmail_%s" % msg["id"]
                if event_exists(conn, raw_id):
                    continue

                detail = ws.execute(
                    ws.gmail.users().messages().get(
                        userId="me", id=msg["id"], format="metadata",
                        metadataHeaders=["From", "Subject", "Date"]
                    )
                )
                headers = {
                    h["name"]: h["value"]
                    for h in detail.get("payload", {}).get("headers", [])
                }
                sender_raw = headers.get("From", "")
                # Extract email address for contact lookup
                if "<" in sender_raw and ">" in sender_raw:
                    sender_email = sender_raw.split("<")[1].split(">")[0]
                else:
                    sender_email = sender_raw
                sender_name = sender_raw.split("<")[0].strip().strip('"') or sender_email

                events.append({
                    "source": "gmail",
                    "sender": sender_email,
                    "sender_name": sender_name,
                    "subject": headers.get("Subject", "(no subject)"),
                    "body": detail.get("snippet", "")[:200],
                    "raw_id": raw_id,
                    "channel": "email",
                    "gmail_account": account,
                })
        except Exception as e:
            logger.error("Gmail poll error (%s): %s" % (account, e))

    return events


def poll_imessage(conn):
    """Poll iMessage for new messages."""
    import imessage_reader
    events = []
    try:
        msgs = imessage_reader.get_recent_messages(hours=1, limit=20)
        for m in msgs:
            raw_id = "imsg_%s_%s" % (m["handle"], m["sent_at"])
            if event_exists(conn, raw_id):
                continue

            name = imessage_reader.resolve_name(m["handle"]) or m["handle"]
            events.append({
                "source": "imessage",
                "sender": m["handle"],
                "sender_name": name,
                "subject": "",
                "body": m.get("text", ""),
                "raw_id": raw_id,
                "channel": "imessage",
            })
    except Exception as e:
        logger.error("iMessage poll error: %s" % e)

    return events


def poll_calendar(conn):
    """Check for calendar events starting in next 30 minutes."""
    from google_api_manager import get_todays_events
    events = []
    now = datetime.now()

    try:
        cal_events = get_todays_events("bailey@contentco-op.com")
        for ev in cal_events:
            title_hash = hashlib.md5(ev["title"].encode()).hexdigest()[:8]
            raw_id = "cal_%s_%s" % (now.strftime("%Y-%m-%d"), title_hash)
            if event_exists(conn, raw_id):
                continue

            time_str = ev.get("time", "")
            if time_str == "All day":
                # Only alert for all-day events once in the morning
                if now.hour > 8:
                    continue
            else:
                try:
                    event_time = datetime.strptime(
                        "%s %s" % (now.strftime("%Y-%m-%d"), time_str),
                        "%Y-%m-%d %I:%M %p"
                    )
                    diff_min = (event_time - now).total_seconds() / 60
                    if diff_min < 0 or diff_min > 30:
                        continue
                except ValueError:
                    continue

            loc = ev.get("location", "")
            body = "%s" % time_str
            if loc:
                body = "%s at %s" % (time_str, loc)
            events.append({
                "source": "calendar",
                "sender": "calendar",
                "sender_name": "",
                "subject": ev["title"],
                "body": body,
                "raw_id": raw_id,
                "channel": "calendar",
            })
    except Exception as e:
        logger.error("Calendar poll error: %s" % e)

    return events


def _fetch_market_ticker(instrument):
    """Fetch a single ticker from Crypto.com."""
    try:
        url = "https://api.crypto.com/v2/public/get-ticker?instrument_name=%s" % instrument
        req = Request(url, headers={"User-Agent": "Blaze/4.0"})
        resp = urlopen(req, timeout=5)
        data = json.loads(resp.read().decode("utf-8"))
        result = data.get("result", {}).get("data", [])
        if isinstance(result, list) and result:
            return result[0]
        if isinstance(result, dict):
            return result
        return None
    except Exception:
        return None


def poll_markets(conn, state):
    """Check for big market moves (>5% change). Runs every 10 min."""
    last_check = state.get("last_markets_check", "")
    if last_check:
        try:
            last_dt = datetime.fromisoformat(last_check)
            if (datetime.now() - last_dt).total_seconds() < 600:
                return []
        except ValueError:
            pass

    CRYPTO_MAP = {
        "BTC": "BTC_USDT", "ETH": "ETH_USDT", "SOL": "SOL_USDT",
        "DOGE": "DOGE_USDT", "XRP": "XRP_USDT", "ADA": "ADA_USDT",
        "AVAX": "AVAX_USDT", "LINK": "LINK_USDT",
    }

    # Fetch all tickers in parallel
    ticker_data = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {}
        for ticker, inst in CRYPTO_MAP.items():
            futs[pool.submit(_fetch_market_ticker, inst)] = ticker
        for fut in as_completed(futs):
            ticker = futs[fut]
            try:
                ticker_data[ticker] = fut.result()
            except Exception:
                pass

    events = []
    today = datetime.now().strftime("%Y-%m-%d")
    for ticker, d in ticker_data.items():
        if not d:
            continue
        raw_id = "market_%s_%s" % (ticker, today)
        if event_exists(conn, raw_id):
            continue

        change = float(d.get("c", 0)) * 100
        if abs(change) >= 5:
            price = float(d.get("a", 0))
            sign = "+" if change >= 0 else ""
            if price >= 1000:
                price_str = "${:,.2f}".format(price)
            elif price >= 1:
                price_str = "${:.2f}".format(price)
            else:
                price_str = "${:.4f}".format(price)

            events.append({
                "source": "market",
                "sender": ticker,
                "sender_name": ticker,
                "subject": "%s %s%.1f%%" % (ticker, sign, change),
                "body": "%s moved %s%.1f%% to %s" % (ticker, sign, change, price_str),
                "raw_id": raw_id,
                "channel": "market",
            })

    state["last_markets_check"] = datetime.now().isoformat()
    return events


def poll_x_news(conn, state):
    """Poll X for news. Runs every 30 min."""
    last_check = state.get("last_x_check", "")
    if last_check:
        try:
            last_dt = datetime.fromisoformat(last_check)
            if (datetime.now() - last_dt).total_seconds() < 1800:
                return []
        except ValueError:
            pass

    events = []
    try:
        from x_api_manager import get_news_feed
        items = get_news_feed()
        for item in items:
            text_hash = hashlib.md5(item["text"].encode()).hexdigest()[:12]
            raw_id = "xnews_%s" % text_hash
            if event_exists(conn, raw_id):
                continue

            events.append({
                "source": "x_news",
                "sender": item.get("topic", "X"),
                "sender_name": item.get("topic", "X"),
                "subject": item.get("text", "")[:100],
                "body": item.get("text", ""),
                "raw_id": raw_id,
                "channel": "news",
            })
    except Exception as e:
        logger.error("X news poll error: %s" % e)

    state["last_x_check"] = datetime.now().isoformat()
    return events


def poll_rss_news(conn, state):
    """Poll RSS feeds for news. Runs every 30 min."""
    last_check = state.get("last_rss_check", "")
    if last_check:
        try:
            last_dt = datetime.fromisoformat(last_check)
            if (datetime.now() - last_dt).total_seconds() < 1800:
                return []
        except ValueError:
            pass

    import xml.etree.ElementTree as ET

    RSS_FEEDS = [
        ("nyt_tech",      "NYT Tech",           "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml"),
        ("nyt_biz",       "NYT Business",        "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"),
        ("techcrunch_ai", "TechCrunch AI",       "https://techcrunch.com/category/artificial-intelligence/feed/"),
        ("nofilmschool",  "No Film School",      "https://nofilmschool.com/rss.xml"),
        ("houston",       "Houston ABC13",   "https://abc13.com/feed/"),
        ("coindesk",      "CoinDesk",            "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ]

    events = []
    for feed_key, feed_name, url in RSS_FEEDS:
        try:
            req = Request(url, headers={"User-Agent": "Blaze/4.0"})
            resp = urlopen(req, timeout=10)
            tree = ET.fromstring(resp.read())
            items = tree.findall(".//item")
            for item in items[:3]:
                title = (item.findtext("title") or "").strip()
                desc = (item.findtext("description") or "").strip()[:200]
                if not title:
                    continue
                raw_id = "rss_%s_%s" % (feed_key, hashlib.md5(title.encode()).hexdigest()[:12])
                if event_exists(conn, raw_id):
                    continue
                events.append({
                    "source": "rss_news",
                    "sender": feed_name,
                    "sender_name": feed_name,
                    "subject": title,
                    "body": desc,
                    "raw_id": raw_id,
                    "channel": "news",
                    "feed_key": feed_key,
                })
        except Exception as e:
            logger.warning("RSS poll error (%s): %s" % (feed_name, e))

    state["last_rss_check"] = datetime.now().isoformat()
    return events


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    start = time.time()
    conn = init_db()
    state = load_state()

    from event_router import score_event
    from push_notify import push_event

    # Poll all sources
    all_events = []
    all_events.extend(poll_gmail(conn))
    all_events.extend(poll_imessage(conn))
    all_events.extend(poll_calendar(conn))
    all_events.extend(poll_markets(conn, state))
    all_events.extend(poll_x_news(conn, state))
    all_events.extend(poll_rss_news(conn, state))

    pushed = 0
    batched = 0
    logged = 0

    for ev in all_events:
        # Pass gmail_account to score_event for business_unit classification
        gmail_account = ev.get("gmail_account")
        score, context, business_unit = score_event(
            ev["source"], ev["sender"], ev["subject"], ev["body"],
            channel=ev["channel"], gmail_account=gmail_account
        )

        if score >= 70:
            action = "pushed"
            msg = ev["subject"] or ev["body"][:80]
            if ev.get("sender_name"):
                msg = "%s: %s" % (ev["sender_name"], msg)
            push_event(msg, score, title="Blaze [%d]" % score,
                       business_unit=business_unit)
            pushed += 1
        elif score >= 40:
            action = "batched"
            batched += 1
        else:
            action = "logged"
            logged += 1

        display_sender = ev.get("sender_name") or ev["sender"]
        insert_event(
            conn, ev["source"], display_sender,
            ev["subject"], ev["body"],
            score, context, action, ev["raw_id"],
            business_unit=business_unit
        )

    state["last_run"] = datetime.now().isoformat()
    save_state(state)
    conn.close()

    elapsed = time.time() - start
    summary = "Processed %d events, pushed %d, batched %d, logged %d (%.1fs)" % (
        len(all_events), pushed, batched, logged, elapsed
    )
    logger.info(summary)
    print(summary)
    log_cron("event_stream", "success", summary)


if __name__ == "__main__":
    main()
