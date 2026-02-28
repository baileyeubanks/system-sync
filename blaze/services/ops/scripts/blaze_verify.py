#!/usr/bin/env python3
"""
Blaze V4 — System Verification
Confirms real rows, real services, real launchd agents.
Run manually or via launchd for monitoring.
"""
import sqlite3, subprocess, os, json
from datetime import datetime, timedelta

DATA_ROOT = "/Users/_mxappservice/blaze-data"
LOGS_DIR = "/Users/_mxappservice/blaze-logs"

BLAZE_DB = "%s/blaze.db" % DATA_ROOT
EVENT_STREAM_DB = "%s/event_stream.db" % DATA_ROOT

CHECKS = []


def check(name, passed, detail=""):
    CHECKS.append({"name": name, "passed": passed, "detail": detail})


def _open_db(path):
    conn = sqlite3.connect(path, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def verify_databases():
    # blaze.db — consolidated database
    if os.path.exists(BLAZE_DB):
        try:
            conn = _open_db(BLAZE_DB)
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            table_names = [t[0] for t in tables if t[0] != "sqlite_sequence"]
            row_counts = {}
            for t in table_names:
                count = conn.execute("SELECT COUNT(*) FROM [%s]" % t).fetchone()[0]
                row_counts[t] = count
            conn.close()
            total = sum(row_counts.values())
            detail = ", ".join(["%s:%d" % (k, v) for k, v in sorted(row_counts.items())])
            check("db:blaze", total > 0, "%d total rows (%s)" % (total, detail))
        except Exception as e:
            check("db:blaze", False, "Error: %s" % e)
    else:
        check("db:blaze", False, "File not found: %s" % BLAZE_DB)

    # event_stream.db
    if os.path.exists(EVENT_STREAM_DB):
        try:
            conn = _open_db(EVENT_STREAM_DB)
            count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            conn.close()
            check("db:event_stream", True, "%d events" % count)
        except Exception as e:
            check("db:event_stream", False, "Error: %s" % e)
    else:
        check("db:event_stream", False, "File not found: %s" % EVENT_STREAM_DB)

    # Specific table checks on blaze.db
    try:
        conn = _open_db(BLAZE_DB)

        contacts = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
        check("db:contacts", contacts > 1000, "%d contacts" % contacts)

        goals = conn.execute("SELECT COUNT(*) FROM goals").fetchone()[0]
        check("db:goals", goals > 0, "%d goals" % goals)

        watchlist = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
        check("db:watchlist", watchlist > 0, "%d tickers" % watchlist)

        conn.close()
    except Exception as e:
        check("db:table_checks", False, "Error: %s" % e)


def verify_launchd():
    """Check that all expected launchd agents are loaded."""
    expected_agents = [
        "com.blaze.gmail-sync",
        "com.blaze.event-stream",
        "com.blaze.morning-briefing",
        "com.blaze.backup",
        "com.blaze.git-autosync",
        "com.blaze.verify",
        "com.blaze.usage-summary",
    ]

    try:
        result = subprocess.run(
            ["launchctl", "list"],
            capture_output=True, text=True
        )
        output = result.stdout

        loaded = []
        missing = []
        for agent in expected_agents:
            if agent in output:
                loaded.append(agent.split(".")[-1])
            else:
                missing.append(agent.split(".")[-1])

        check(
            "launchd:agents",
            len(loaded) >= 5,
            "%d/%d loaded (%s)" % (len(loaded), len(expected_agents), ", ".join(loaded))
        )

        if missing:
            check("launchd:missing", False, "Missing: %s" % ", ".join(missing))

    except Exception as e:
        check("launchd:agents", False, "Error: %s" % e)


def verify_services():
    # FastAPI runtime on port 8899
    try:
        result = subprocess.run(
            ["curl", "-s", "-m", "3", "http://127.0.0.1:8899/health"],
            capture_output=True, text=True
        )
        data = json.loads(result.stdout)
        check("service:fastapi", data.get("status") == "ok", "port 8899 healthy")
    except:
        check("service:fastapi", False, "Not responding on :8899")


def verify_recent_activity():
    try:
        conn = _open_db(BLAZE_DB)
    except:
        check("recent:db_open", False, "Could not open blaze.db")
        return

    # Gmail sync — last 15 min
    try:
        cutoff = (datetime.utcnow() - timedelta(minutes=15)).isoformat()
        row = conn.execute(
            "SELECT COUNT(*) FROM cron_runs WHERE job_name='gmail_contact_sync' AND started_at > ?",
            (cutoff,)
        ).fetchone()
        check("recent:gmail_sync", row[0] > 0, "%d runs in last 15min" % row[0])
    except:
        check("recent:gmail_sync", False, "Could not query cron_runs")

    # Morning briefing — ran today
    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        row = conn.execute(
            "SELECT COUNT(*) FROM cron_runs WHERE job_name IN "
            "('morning_briefing','morning_briefing_v2','morning_briefing_v3') "
            "AND started_at LIKE ?",
            (today + "%",)
        ).fetchone()
        check("recent:morning_briefing", row[0] > 0, "%d runs today" % row[0])
    except:
        check("recent:morning_briefing", False, "Could not query cron_runs")

    # Event stream — last 5 min
    try:
        cutoff = (datetime.utcnow() - timedelta(minutes=5)).isoformat()
        row = conn.execute(
            "SELECT COUNT(*) FROM cron_runs WHERE job_name='event_stream' AND started_at > ?",
            (cutoff,)
        ).fetchone()
        check("recent:event_stream", row[0] > 0, "%d runs in last 5min" % row[0])
    except:
        check("recent:event_stream", False, "Could not query cron_runs")

    conn.close()


def verify_backups():
    backup_dir = "%s/backups" % DATA_ROOT
    today = datetime.now().strftime("%Y-%m-%d")
    today_dir = os.path.join(backup_dir, today)
    if os.path.isdir(today_dir):
        files = os.listdir(today_dir)
        check("backups:today", len(files) >= 1, "%d backup files today" % len(files))
    else:
        check("backups:today", False, "No backup directory for %s" % today)


def verify_logs():
    """Check launchd log files exist."""
    if not os.path.isdir(LOGS_DIR):
        check("logs:directory", False, "Log dir missing: %s" % LOGS_DIR)
        return

    log_files = [f for f in os.listdir(LOGS_DIR) if f.endswith(".log")]
    check("logs:directory", len(log_files) > 0, "%d log files in %s" % (len(log_files), LOGS_DIR))


def main():
    print("=" * 50)
    print("BLAZE V4 — SYSTEM VERIFICATION")
    print("Run: %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 50)

    verify_databases()
    verify_launchd()
    verify_services()
    verify_recent_activity()
    verify_backups()
    verify_logs()

    passed = sum(1 for c in CHECKS if c["passed"])
    total = len(CHECKS)

    print("")
    for c in CHECKS:
        status = "PASS" if c["passed"] else "FAIL"
        print("[%s] %-30s %s" % (status, c["name"], c["detail"]))

    print("")
    print("=" * 50)
    score = (passed / total * 100) if total > 0 else 0
    print("SCORE: %d/%d (%.0f%%)" % (passed, total, score))
    if score == 100:
        print("STATUS: ALL SYSTEMS GREEN")
    elif score >= 70:
        print("STATUS: OPERATIONAL (some warnings)")
    else:
        print("STATUS: DEGRADED — action needed")
    print("=" * 50)

    # Log the verification run to blaze.db
    try:
        conn = _open_db(BLAZE_DB)
        conn.execute(
            "INSERT INTO cron_runs (job_name,started_at,completed_at,status,output_summary) VALUES (?,?,?,?,?)",
            ("blaze_verify", datetime.utcnow().isoformat(), datetime.utcnow().isoformat(),
             "success" if score >= 70 else "partial",
             "%d/%d checks passed (%.0f%%)" % (passed, total, score))
        )
        conn.commit()
        conn.close()
    except:
        pass


if __name__ == "__main__":
    main()
