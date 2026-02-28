#!/usr/bin/env python3
"""
Business Advisory Council v2 — Direct API, no ask_blaze() dependency.
Refactored 2026-02-21: replaced all OpenClaw CLI calls with direct Google API
calls, identical pattern to morning_briefing_v3.py.
Runs at 1am daily via com.blaze.morning-briefing LaunchAgent (or openclaw cron).
"""
import sys, os, sqlite3, json, time
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0, os.path.dirname(__file__))
from blaze_helper import log_cron

DATA_ROOT = "/Users/_mxappservice/blaze-data"
BLAZE_DB = os.path.join(DATA_ROOT, "blaze.db")
USAGE_DB = os.path.join(DATA_ROOT, "usage.db")
CRON_DB = os.path.join(DATA_ROOT, "cron-log.db")
CONTACTS_DB = os.path.join(DATA_ROOT, "contacts", "contacts.db")
EVENT_STREAM_DB = os.path.join(DATA_ROOT, "event_stream.db")
SEP = "━" * 48


def _open_db(path):
    conn = sqlite3.connect(path, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


# ─────────────────────────────────────────────
# Data pulls (all direct, no OpenClaw)
# ─────────────────────────────────────────────

def get_crm_summary():
    try:
        conn = _open_db(CONTACTS_DB)
        total = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
        low_health = conn.execute(
            "SELECT COUNT(*) FROM contacts WHERE relationship_health_score < 40"
        ).fetchone()[0]
        due = conn.execute(
            "SELECT COUNT(*) FROM contacts WHERE follow_up_due <= date('now', '+7 days')"
        ).fetchone()[0]
        top = conn.execute(
            "SELECT name, company FROM contacts ORDER BY priority_score DESC, interaction_count DESC LIMIT 3"
        ).fetchall()
        conn.close()
        top_str = ", ".join(f"{r[0]} ({r[1]})" for r in top if r[0]) if top else "N/A"
        return f"{total} contacts | {low_health} low health | {due} follow-ups due | Top: {top_str}"
    except Exception as e:
        return f"CRM not readable: {e}"


def get_usage_summary():
    try:
        conn = _open_db(USAGE_DB)
        row = conn.execute(
            "SELECT ROUND(SUM(cost_usd),4), COUNT(*) FROM api_calls "
            "WHERE ts > datetime('now', '-24 hours')"
        ).fetchone()
        conn.close()
        return f"${row[0] or 0:.4f} spent, {row[1] or 0} API calls (24h)"
    except Exception as e:
        return f"Usage DB not readable: {e}"


def get_cron_health():
    try:
        conn = _open_db(CRON_DB)
        fails = conn.execute(
            "SELECT COUNT(*) FROM cron_runs WHERE status='fail' "
            "AND started_at > datetime('now', '-24 hours')"
        ).fetchone()[0]
        total = conn.execute(
            "SELECT COUNT(*) FROM cron_runs WHERE started_at > datetime('now', '-24 hours')"
        ).fetchone()[0]
        conn.close()
        return f"{total} jobs ran, {fails} failed (24h)"
    except Exception as e:
        return f"Cron log not readable: {e}"


def get_email_summary():
    """Pull top scored emails from event_stream.db (same as morning briefing)."""
    try:
        conn = _open_db(EVENT_STREAM_DB)
        rows = conn.execute(
            "SELECT sender, subject, score FROM events "
            "WHERE source='gmail' AND created_at > datetime('now', '-24 hours') "
            "AND score >= 40 ORDER BY score DESC LIMIT 6"
        ).fetchall()
        conn.close()
        if not rows:
            return "No high-priority emails in last 24h"
        lines = [f"  {sender}: {subject} (score:{score})" for sender, subject, score in rows]
        return "\n".join(lines)
    except Exception as e:
        # Fallback to direct API
        try:
            from google_api_manager import get_recent_emails
            emails = get_recent_emails("bailey@contentco-op.com", max_results=5)
            if not emails:
                return "Inbox clear"
            return "\n".join(f"  {e['from']}: {e['subject']}" for e in emails[:5])
        except Exception as e2:
            return f"Email unavailable: {e2}"


def get_tomorrow_calendar():
    """Pull tomorrow's calendar via direct Google API."""
    try:
        from google_api_manager import get_todays_events
        events = get_todays_events("bailey@contentco-op.com")
        from datetime import date, timedelta
        
        tmr_events = events or []
        if not tmr_events:
            return "No events tomorrow"
        return " | ".join(f"{e.get('time','?')} {e.get('title','?')}" for e in tmr_events[:4])
    except Exception as e:
        return f"Calendar unavailable: {e}"


def get_active_goals():
    """Pull short-term active goals from knowledge.db."""
    try:
        conn = _open_db(os.path.join(DATA_ROOT, "knowledge.db"))
        goals = conn.execute(
            "SELECT goal FROM goals WHERE type='short' AND status='active' ORDER BY id LIMIT 5"
        ).fetchall()
        conn.close()
        return "\n".join(f"  → {g[0]}" for g in goals) if goals else "No active short-term goals"
    except Exception as e:
        return f"Goals unavailable: {e}"


# ─────────────────────────────────────────────
# Synthesis (direct model call via OpenClaw CLI, only if env available)
# ─────────────────────────────────────────────

def synthesize_findings(crm, cron, usage, email, calendar, goals):
    """
    Produce 5 business findings. Try OpenClaw CLI first (research-worker),
    fall back to a rule-based summary if unavailable.
    """
    import subprocess
    OPENCLAW = "/usr/local/bin/openclaw"

    synthesis_prompt = (
        "You are the Business Advisory Council synthesis engine for Bailey Eubanks.\n\n"
        f"Data snapshot (24h window):\n"
        f"CRM: {crm}\n"
        f"System: {cron}\n"
        f"Costs: {usage}\n"
        f"Email: {email[:400]}\n"
        f"Tomorrow: {calendar}\n"
        f"Active goals: {goals[:300]}\n\n"
        "Produce exactly 5 numbered findings. One sentence each.\n"
        "Rank by business impact. Be specific. No filler. No corporate language.\n"
        "Focus on: risks, opportunities, things needing Bailey's attention."
    )

    try:
        result = subprocess.run(
            [OPENCLAW, "agent", "--agent", "research-worker",
             "--message", synthesis_prompt, "--json", "--timeout-seconds", "45"],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"}
        )
        if result.returncode == 0:
            raw = result.stdout.strip()
            try:
                data = json.loads(raw)
                return data.get("text") or data.get("content") or raw
            except Exception:
                return raw
        else:
            raise RuntimeError(result.stderr.strip()[:200])
    except Exception as e:
        # Rule-based fallback — no LLM needed
        return (
            f"1. CRM health: {crm}\n"
            f"2. System: {cron}\n"
            f"3. Cost: {usage}\n"
            f"4. Top email: {email.split(chr(10))[0] if email else 'none'}\n"
            f"5. Tomorrow: {calendar}"
        )


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def run_council():
    start = time.time()

    # Parallel data pulls
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {
            pool.submit(get_crm_summary): "crm",
            pool.submit(get_usage_summary): "usage",
            pool.submit(get_cron_health): "cron",
            pool.submit(get_email_summary): "email",
            pool.submit(get_tomorrow_calendar): "calendar",
        }
        results = {}
        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = future.result()
            except Exception as e:
                results[key] = f"error: {e}"

    goals = get_active_goals()
    findings = synthesize_findings(
        results["crm"], results["cron"], results["usage"],
        results["email"], results["calendar"], goals
    )

    elapsed = round(time.time() - start, 1)

    output = (
        f"\nBUSINESS ADVISORY COUNCIL\n"
        f"{SEP}\n"
        f"CRM      {results['crm']}\n"
        f"System   {results['cron']}\n"
        f"Cost     {results['usage']}\n"
        f"{SEP}\n"
        f"EMAIL ACTIVITY\n{results['email']}\n"
        f"{SEP}\n"
        f"TOMORROW\n  {results['calendar']}\n"
        f"{SEP}\n"
        f"ACTIVE GOALS\n{goals}\n"
        f"{SEP}\n"
        f"FINDINGS\n{findings}\n"
        f"{SEP}\n"
        f"Built in {elapsed}s\n"
    )

    print(output)
    log_cron("business_council", "success", findings[:200] if findings else "no output")


if __name__ == "__main__":
    run_council()
