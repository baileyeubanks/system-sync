#!/usr/bin/env python3
"""
blaze_test.py — Blaze V4 Root-Level Test Suite

Run after EVERY deploy. Catches bugs that blaze_verify.py misses.
Exit code 0 = all pass. Non-zero = failures found.

Usage:
    python3 blaze_test.py          # run all tests
    python3 blaze_test.py --smoke  # fast tests only (5s)
    python3 blaze_test.py --full   # smoke + integration (30s)
"""
import sys, os, time, json, sqlite3, subprocess, traceback

sys.path.insert(0, os.path.dirname(__file__))

DATA_ROOT = "/Users/_mxappservice/blaze-data"
SCRIPTS = os.path.dirname(os.path.abspath(__file__))

# ──────────────────────────────────────────────
# Test framework (zero dependencies)
# ──────────────────────────────────────────────

PASS = 0
FAIL = 0
SKIP = 0
RESULTS = []


def test(name, fn, integration=False):
    global PASS, FAIL, SKIP
    mode = "--full" if len(sys.argv) > 1 and sys.argv[1] == "--full" else ""
    smoke_only = len(sys.argv) > 1 and sys.argv[1] == "--smoke"

    if integration and smoke_only:
        SKIP += 1
        RESULTS.append(("SKIP", name, "smoke mode"))
        return

    start = time.time()
    try:
        fn()
        elapsed = time.time() - start
        PASS += 1
        RESULTS.append(("PASS", name, "%.2fs" % elapsed))
    except AssertionError as e:
        elapsed = time.time() - start
        FAIL += 1
        RESULTS.append(("FAIL", name, str(e)))
    except Exception as e:
        elapsed = time.time() - start
        FAIL += 1
        RESULTS.append(("FAIL", name, "%s: %s" % (type(e).__name__, e)))


def assert_true(condition, msg="assertion failed"):
    if not condition:
        raise AssertionError(msg)


def assert_eq(a, b, msg=None):
    if a != b:
        raise AssertionError(msg or "%r != %r" % (a, b))


def assert_gte(a, b, msg=None):
    if a < b:
        raise AssertionError(msg or "%r < %r (expected >= %r)" % (a, b, b))


def assert_gt(a, b, msg=None):
    if a <= b:
        raise AssertionError(msg or "%r <= %r (expected > %r)" % (a, b, b))


def assert_in(needle, haystack, msg=None):
    if needle not in haystack:
        raise AssertionError(msg or "%r not found in output" % needle)


# ══════════════════════════════════════════════
# LAYER 1: SMOKE TESTS — Database & Data
# ══════════════════════════════════════════════

def test_db_files_exist():
    """Consolidated database files exist and are non-empty."""
    files = {
        "blaze":        "%s/blaze.db" % DATA_ROOT,
        "event_stream": "%s/event_stream.db" % DATA_ROOT,
    }
    for name, path in files.items():
        assert_true(os.path.exists(path), "%s missing: %s" % (name, path))
        size = os.path.getsize(path)
        assert_gt(size, 0, "%s is 0 bytes: %s" % (name, path))


def test_get_db_all_keys():
    """get_db() returns a live connection for every registered key."""
    from blaze_helper import get_db
    for key in ["knowledge", "contacts", "usage", "cron"]:
        conn = get_db(key)
        assert_true(conn is not None, "get_db('%s') returned None" % key)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        assert_gt(len(tables), 0, "get_db('%s') has 0 tables" % key)
        conn.close()


def test_knowledge_goals():
    """knowledge.db has active goals."""
    from blaze_helper import get_db
    conn = get_db("knowledge")
    count = conn.execute(
        "SELECT COUNT(*) FROM goals WHERE status='active'"
    ).fetchone()[0]
    conn.close()
    assert_gte(count, 20, "Expected 20+ active goals, got %d" % count)


def test_knowledge_watchlist():
    """knowledge.db has watchlist tickers."""
    from blaze_helper import get_db
    conn = get_db("knowledge")
    count = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
    conn.close()
    assert_gte(count, 20, "Expected 20+ watchlist tickers, got %d" % count)


def test_contacts_count():
    """contacts.db has 1500+ contacts."""
    from blaze_helper import get_db
    conn = get_db("contacts")
    count = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    conn.close()
    assert_gte(count, 1500, "Expected 1500+ contacts, got %d" % count)


def test_contacts_have_tiers():
    """Most contacts have enrichment tiers."""
    from blaze_helper import get_db
    conn = get_db("contacts")
    total = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    tiered = conn.execute(
        "SELECT COUNT(*) FROM contacts WHERE enrichment_tier IS NOT NULL"
    ).fetchone()[0]
    conn.close()
    pct = (tiered / total) * 100 if total > 0 else 0
    assert_gte(pct, 90, "Only %.0f%% of contacts have tiers (expected 90%%+)" % pct)


def test_contacts_inner_circle():
    """Inner circle (tier <= 10) has expected count."""
    from blaze_helper import get_db
    conn = get_db("contacts")
    count = conn.execute(
        "SELECT COUNT(*) FROM contacts WHERE enrichment_tier <= 10"
    ).fetchone()[0]
    conn.close()
    assert_gte(count, 8, "Expected 8+ inner circle contacts, got %d" % count)


def test_event_log_schema():
    """event_stream.db has the events table with correct columns."""
    conn = sqlite3.connect("%s/event_stream.db" % DATA_ROOT)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(events)").fetchall()]
    conn.close()
    for expected in ["source", "sender", "subject", "body", "score", "action", "raw_id"]:
        assert_in(expected, cols, "events table missing column: %s" % expected)


def test_cron_log_recent():
    """Cron log has entries from today."""
    from blaze_helper import get_db
    conn = get_db("cron")
    count = conn.execute(
        "SELECT COUNT(*) FROM cron_runs WHERE completed_at > datetime('now', '-24 hours')"
    ).fetchone()[0]
    conn.close()
    assert_gt(count, 0, "No cron entries in last 24 hours")


# ══════════════════════════════════════════════
# LAYER 1: SMOKE TESTS — Functions
# ══════════════════════════════════════════════

def test_event_router_high_score():
    """Inner circle + urgent email = high score."""
    from event_router import score_event
    # Use a known tier-10 contact (Caio is inner circle)
    result = score_event(
        "gmail", "caio@astrocleanings.com",
        "URGENT invoice due", "$500 payment needed today",
        channel="email"
    )
    score = result[0]
    assert_gte(score, 70,
        "Inner circle urgent email scored %d, expected >= 70" % score)


def test_event_router_low_score():
    """Unknown sender + ambient news = low score."""
    from event_router import score_event
    result = score_event(
        "x_news", "randombot@twitter.com",
        "Tech roundup", "Some article about semiconductors",
        channel="news"
    )
    score = result[0]
    assert_true(score < 40,
        "Unknown news scored %d, expected < 40" % score)


def test_event_router_channel_weights():
    """Calendar scores higher than news for same sender."""
    from event_router import score_event
    cal_result = score_event("calendar", "calendar", "Meeting", "10 AM", channel="calendar")
    news_result = score_event("x_news", "calendar", "Meeting", "10 AM", channel="news")
    cal_score = cal_result[0]
    news_score = news_result[0]
    assert_gt(cal_score, news_score,
        "Calendar (%d) should score higher than news (%d)" % (cal_score, news_score))


def test_event_router_business_unit():
    """score_event returns correct business_unit classification."""
    from event_router import score_event
    # CC sender
    _, _, bu_cc = score_event(
        "gmail", "bailey@contentco-op.com",
        "Test", "Test", channel="email"
    )
    assert_eq(bu_cc, "CC", "Expected CC for bailey@contentco-op.com, got %s" % bu_cc)

    # ACS sender
    _, _, bu_acs = score_event(
        "gmail", "caio@astrocleanings.com",
        "Test", "Test", channel="email"
    )
    assert_eq(bu_acs, "ACS", "Expected ACS for caio@astrocleanings.com, got %s" % bu_acs)

    # Gmail account override
    _, _, bu_acct = score_event(
        "gmail", "unknown@example.com",
        "Test", "Test", channel="email",
        gmail_account="caio@astrocleanings.com"
    )
    assert_eq(bu_acct, "ACS",
        "Expected ACS for gmail_account override, got %s" % bu_acct)

    # Unknown defaults to BOTH
    _, _, bu_both = score_event(
        "imessage", "+15559999999",
        "", "Hey there", channel="imessage"
    )
    assert_eq(bu_both, "BOTH", "Expected BOTH for unknown sender, got %s" % bu_both)


def test_event_router_contact_lookup():
    """lookup_contact finds a known contact by email."""
    from event_router import lookup_contact
    contact = lookup_contact("bailey@contentco-op.com")
    assert_true(contact is not None, "bailey@contentco-op.com not found in contacts")
    assert_true("name" in contact, "Contact missing 'name' field")


def test_briefing_goals():
    """Briefing get_goals() returns active goals."""
    from morning_briefing_v3 import get_goals
    long_goals, short_goals = get_goals()
    assert_gt(len(long_goals), 0, "No long-term goals returned")
    assert_gt(len(short_goals), 0, "No short-term goals returned")


def test_briefing_watchlist():
    """Briefing get_watchlist_tickers() returns tickers."""
    from morning_briefing_v3 import get_watchlist_tickers
    tickers = get_watchlist_tickers()
    assert_gte(len(tickers), 10, "Expected 10+ tickers, got %d" % len(tickers))


def test_crypto_api_btc():
    """Crypto.com API returns BTC price."""
    from morning_briefing_v3 import _fetch_ticker
    data = _fetch_ticker("BTC_USDT")
    assert_true(data is not None, "BTC_USDT ticker returned None")
    price = float(data.get("a", data.get("last", 0)))
    assert_gt(price, 0, "BTC price is zero or missing")


def test_imessage_reader_import():
    """imessage_reader module loads without error."""
    import imessage_reader
    assert_true(hasattr(imessage_reader, "get_recent_messages"),
        "imessage_reader missing get_recent_messages()")
    assert_true(hasattr(imessage_reader, "get_unread_summary"),
        "imessage_reader missing get_unread_summary()")


def test_push_notify_import():
    """push_notify module loads and has expected functions."""
    import push_notify
    assert_true(hasattr(push_notify, "push"), "push_notify missing push()")
    assert_true(hasattr(push_notify, "push_event"), "push_notify missing push_event()")


def test_push_notify_topic_routing():
    """push_notify routes to correct ntfy topics based on business_unit."""
    from push_notify import _get_topic
    assert_eq(_get_topic("CC"), "blaze-bailey-v4",
        "CC should route to blaze-bailey-v4")
    assert_eq(_get_topic("ACS"), "blaze-astro-v4",
        "ACS should route to blaze-astro-v4")
    assert_eq(_get_topic("BOTH"), "blaze-bailey-v4",
        "BOTH should route to blaze-bailey-v4")
    assert_eq(_get_topic(None), "blaze-bailey-v4",
        "None should route to blaze-bailey-v4")


def test_google_api_manager_import():
    """google_api_manager loads and has calendar/email functions."""
    from google_api_manager import get_todays_events, get_recent_emails
    assert_true(callable(get_todays_events), "get_todays_events not callable")
    assert_true(callable(get_recent_emails), "get_recent_emails not callable")


# ══════════════════════════════════════════════
# LAYER 1: SMOKE TESTS — Services
# ══════════════════════════════════════════════

def test_fastapi_health():
    """FastAPI on port 8899 responds healthy."""
    from urllib.request import urlopen
    resp = urlopen("http://127.0.0.1:8899/health", timeout=5)
    data = json.loads(resp.read().decode("utf-8"))
    assert_eq(data.get("status"), "ok", "FastAPI health: %s" % data)


def test_openclaw_gateway():
    """OpenClaw gateway on port 18789 is reachable."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(3)
    try:
        result = s.connect_ex(("127.0.0.1", 18789))
        assert_eq(result, 0, "OpenClaw gateway port 18789 not open (code %d)" % result)
    finally:
        s.close()


# ══════════════════════════════════════════════
# LAYER 2: INTEGRATION TESTS (--full mode)
# ══════════════════════════════════════════════

def test_briefing_runs():
    """Morning briefing v3 completes in < 30s with all sections."""
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "morning_briefing_v3.py")],
        capture_output=True, text=True, timeout=30,
        cwd=SCRIPTS,
    )
    output = result.stdout + result.stderr
    assert_eq(result.returncode, 0,
        "Briefing crashed (exit %d): %s" % (result.returncode, output[-500:]))
    for section_name in ["TODAY", "EMAIL", "iMESSAGE", "MARKETS", "GOALS IN MOTION", "OVERNIGHT"]:
        assert_in(section_name, result.stdout,
            "Briefing missing section: %s" % section_name)


def test_briefing_no_unavailable():
    """Briefing has at most 1 'Unavailable' section (WATCH is ok)."""
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "morning_briefing_v3.py")],
        capture_output=True, text=True, timeout=30,
        cwd=SCRIPTS,
    )
    count = result.stdout.count("Unavailable")
    assert_true(count <= 1,
        "Briefing has %d 'Unavailable' sections (expected <= 1)" % count)


def test_event_stream_runs():
    """Event stream completes without crashing."""
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "event_stream.py")],
        capture_output=True, text=True, timeout=60,
        cwd=SCRIPTS,
    )
    output = result.stdout + result.stderr
    assert_eq(result.returncode, 0,
        "Event stream crashed (exit %d): %s" % (result.returncode, output[-500:]))
    assert_in("Processed", output,
        "Event stream missing 'Processed' summary in output")


def test_event_stream_writes_db():
    """Event stream leaves data in event_stream.db."""
    conn = sqlite3.connect("%s/event_stream.db" % DATA_ROOT)
    count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()
    assert_gt(count, 0, "event_stream.db has 0 events after stream ran")


def test_verify_score():
    """blaze_verify.py scores >= 80%."""
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "blaze_verify.py")],
        capture_output=True, text=True, timeout=30,
        cwd=SCRIPTS,
    )
    output = result.stdout
    import re as _re
    m = _re.search(r"SCORE:\s*(\d+)/(\d+)", output)
    if m:
        passed = int(m.group(1))
        total = int(m.group(2))
        pct = (passed * 100) // total if total > 0 else 0
        assert_gte(pct, 80,
            "Verify %d/%d (%d%%), expected >= 80%%" % (passed, total, pct))
        return
    assert_eq(result.returncode, 0, "blaze_verify.py crashed")

def test_router_uses_real_contacts():
    """Event router scoring actually reads from the real contacts DB."""
    from event_router import lookup_contact
    # Pick a known contact
    conn = sqlite3.connect("%s/blaze.db" % DATA_ROOT)
    row = conn.execute(
        "SELECT email FROM contacts WHERE enrichment_tier <= 10 AND email IS NOT NULL LIMIT 1"
    ).fetchone()
    conn.close()
    assert_true(row is not None, "No inner circle contact with email found")
    contact = lookup_contact(row[0])
    assert_true(contact is not None,
        "lookup_contact('%s') returned None — DB path mismatch?" % row[0])
    tier = contact.get("enrichment_tier")
    assert_true(tier is not None and tier <= 10,
        "Contact tier %s, expected <= 10" % tier)


def test_router_uses_real_goals():
    """Event router goal matching reads from the real knowledge DB."""
    from event_router import get_active_goals
    # Clear cache to force fresh read
    import event_router
    event_router._goals_cache = None
    goals = get_active_goals()
    assert_gte(len(goals), 20, "Expected 20+ goals, got %d" % len(goals))


def test_briefing_markets_live():
    """Markets section returns actual crypto prices, not errors."""
    from morning_briefing_v3 import get_markets
    result = get_markets()
    assert_true("unavailable" not in result.lower() or result.count("unavailable") <= 2,
        "Too many unavailable tickers in markets: %s" % result[:200])
    assert_in("$", result, "No dollar signs in markets output — prices missing")


# ══════════════════════════════════════════════
# Runner
# ══════════════════════════════════════════════

def main():
    start = time.time()
    mode = sys.argv[1] if len(sys.argv) > 1 else "--full"

    print("\nBLAZE V4 TEST SUITE")
    print("=" * 50)
    if mode == "--smoke":
        print("Mode: SMOKE (fast, no subprocess tests)")
    else:
        print("Mode: FULL (smoke + integration)")
    print()

    # Layer 1: Smoke — Database files
    test("db_files_exist", test_db_files_exist)
    test("get_db_all_keys", test_get_db_all_keys)
    test("knowledge_goals", test_knowledge_goals)
    test("knowledge_watchlist", test_knowledge_watchlist)
    test("contacts_count", test_contacts_count)
    test("contacts_have_tiers", test_contacts_have_tiers)
    test("contacts_inner_circle", test_contacts_inner_circle)
    test("event_log_schema", test_event_log_schema)
    test("cron_log_recent", test_cron_log_recent)

    # Layer 1: Smoke — Functions
    test("event_router_high_score", test_event_router_high_score)
    test("event_router_low_score", test_event_router_low_score)
    test("event_router_channel_weights", test_event_router_channel_weights)
    test("event_router_business_unit", test_event_router_business_unit)
    test("event_router_contact_lookup", test_event_router_contact_lookup)
    test("briefing_goals", test_briefing_goals)
    test("briefing_watchlist", test_briefing_watchlist)
    test("crypto_api_btc", test_crypto_api_btc)
    test("imessage_reader_import", test_imessage_reader_import)
    test("push_notify_import", test_push_notify_import)
    test("push_notify_topic_routing", test_push_notify_topic_routing)
    test("google_api_manager_import", test_google_api_manager_import)

    # Layer 1: Smoke — Services
    test("fastapi_health", test_fastapi_health)
    test("openclaw_gateway", test_openclaw_gateway)

    # Layer 2: Integration
    test("briefing_runs", test_briefing_runs, integration=True)
    test("briefing_no_unavailable", test_briefing_no_unavailable, integration=True)
    test("event_stream_runs", test_event_stream_runs, integration=True)
    test("event_stream_writes_db", test_event_stream_writes_db, integration=True)
    test("verify_score", test_verify_score, integration=True)
    test("router_uses_real_contacts", test_router_uses_real_contacts, integration=True)
    test("router_uses_real_goals", test_router_uses_real_goals, integration=True)
    test("briefing_markets_live", test_briefing_markets_live, integration=True)

    # Results
    elapsed = time.time() - start
    print()
    print("=" * 50)
    for status, name, detail in RESULTS:
        if status == "PASS":
            print("  PASS  %s (%s)" % (name, detail))
        elif status == "SKIP":
            print("  SKIP  %s (%s)" % (name, detail))
        else:
            print("  FAIL  %s" % name)
            print("        -> %s" % detail)

    print()
    print("=" * 50)
    print("  %d PASS | %d FAIL | %d SKIP | %.1fs" % (PASS, FAIL, SKIP, elapsed))
    print("=" * 50)

    if FAIL > 0:
        print("\n  DEPLOY BLOCKED — fix failures before continuing.\n")
        sys.exit(1)
    else:
        print("\n  ALL CLEAR — safe to deploy.\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
