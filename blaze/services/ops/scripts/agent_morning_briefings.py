#!/usr/bin/env python3
"""
agent_morning_briefings.py — Blaze dispatches tailored morning to-do briefings
to Agent Astro (acs-worker → Caio) and Agent CC (cc-worker → Bailey) at 8 AM daily.

Run via LaunchAgent: com.blaze.agent-briefings (8:00 AM daily)
"""
import sys, os, sqlite3, json, subprocess
from datetime import datetime, date

sys.path.insert(0, os.path.dirname(__file__))
from blaze_helper import log_cron

DATA_ROOT = "/Users/_mxappservice/blaze-data"
CONTACTS_DB = "%s/contacts/contacts.db" % DATA_ROOT
KNOWLEDGE_DB = "%s/knowledge.db" % DATA_ROOT
OPENCLAW = "PATH=/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin /usr/local/bin/openclaw"
TODAY = date.today().strftime("%A %b %-d").upper()


# ──────────────────────────────────────────────
# ACS DATA (for Agent Astro)
# ──────────────────────────────────────────────

def get_acs_jobs_today():
    """Pull today's job count and time range from Supabase via contacts.db fallback."""
    try:
        import urllib.request, json as _json
        env = _load_env()
        supa_url = env.get("SUPABASE_URL", "")
        supa_key = env.get("SUPABASE_SERVICE_KEY", "")
        if not supa_url or not supa_key:
            return None

        today_str = date.today().isoformat()
        url = "%s/rest/v1/jobs?select=id,scheduled_date,scheduled_time,status,client_name&scheduled_date=eq.%s&order=scheduled_time" % (supa_url, today_str)
        req = urllib.request.Request(url, headers={
            "apikey": supa_key,
            "Authorization": "Bearer %s" % supa_key,
        })
        resp = urllib.request.urlopen(req, timeout=10)
        jobs = _json.loads(resp.read().decode("utf-8"))
        return jobs
    except Exception:
        return None


def get_acs_followups_due():
    """Pull ACS contacts with follow-ups due today."""
    try:
        conn = sqlite3.connect(CONTACTS_DB, timeout=5)
        rows = conn.execute("""
            SELECT name, company, follow_up_due
            FROM contacts
            WHERE follow_up_due <= date('now', '+1 day')
              AND priority_score >= 40
              AND client_status IN ('active-client', 'prospect', 'lead')
            ORDER BY priority_score DESC, follow_up_due
            LIMIT 5
        """).fetchall()
        conn.close()
        return rows
    except Exception:
        return []


def get_acs_pending_sms():
    """Check for unresolved inbound ACS SMS (from event_stream.db)."""
    event_db = "%s/event_stream.db" % DATA_ROOT
    try:
        conn = sqlite3.connect(event_db, timeout=5)
        rows = conn.execute("""
            SELECT sender, subject, body FROM events
            WHERE source='imessage'
              AND business_unit IN ('ACS', 'BOTH')
              AND action='batched'
              AND created_at > datetime('now', '-24 hours')
            ORDER BY created_at DESC LIMIT 5
        """).fetchall()
        conn.close()
        return rows
    except Exception:
        return []


# ──────────────────────────────────────────────
# CC DATA (for Agent CC)
# ──────────────────────────────────────────────

def get_cc_pipeline():
    """Pull active CC deal pipeline."""
    try:
        from briefing_pipeline import get_pipeline
        return get_pipeline()
    except Exception:
        return None


def get_cc_followups_due():
    """Pull CC contacts with follow-ups due today."""
    try:
        conn = sqlite3.connect(CONTACTS_DB, timeout=5)
        rows = conn.execute("""
            SELECT name, company, follow_up_due
            FROM contacts
            WHERE follow_up_due <= date('now', '+1 day')
              AND priority_score >= 40
              AND (business_unit='CC' OR business_unit IS NULL)
            ORDER BY priority_score DESC, follow_up_due
            LIMIT 5
        """).fetchall()
        conn.close()
        return rows
    except Exception:
        return []


def get_youtube_queue():
    """Pull Kallaway YouTube queue status."""
    try:
        conn = sqlite3.connect(KNOWLEDGE_DB, timeout=5)
        pending = conn.execute("SELECT COUNT(*) FROM youtube_queue WHERE status='pending'").fetchone()[0]
        processed = conn.execute("SELECT COUNT(*) FROM youtube_insights").fetchone()[0]
        # Get next video
        next_vid = conn.execute(
            "SELECT title, channel_name FROM youtube_queue WHERE status='pending' ORDER BY id LIMIT 1"
        ).fetchone()
        conn.close()
        lines = ["%d insights in library, %d videos queued" % (processed, pending)]
        if next_vid:
            lines.append("Next up: %s (%s)" % (next_vid[0][:60], next_vid[1]))
        return "\n".join(lines)
    except Exception:
        return None


# ──────────────────────────────────────────────
# Dispatch via OpenClaw agent
# ──────────────────────────────────────────────

def _load_env():
    env = {}
    try:
        with open("/Users/_mxappservice/.blaze/env_cache") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    except Exception:
        pass
    return env


def send_to_agent(agent, message):
    """Send a briefing message through OpenClaw agent (headless)."""
    cmd = [
        "/usr/local/bin/openclaw", "agent",
        "--agent", agent,
        "--message", message,
        "--json"
    ]
    env = dict(os.environ, PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
        if result.returncode == 0:
            return True
        print("[%s] openclaw error: %s" % (agent, result.stderr[:200]))
        return False
    except Exception as e:
        print("[%s] dispatch error: %s" % (agent, e))
        return False


# ──────────────────────────────────────────────
# Build briefings
# ──────────────────────────────────────────────

def build_astro_briefing():
    """Tailored briefing for Agent Astro (Caio) — ACS operations."""
    lines = ["GOOD MORNING ASTRO — %s" % TODAY, ""]
    lines.append("Your briefing for today. Send this to Caio.")
    lines.append("")

    # Jobs today
    jobs = get_acs_jobs_today()
    if jobs:
        lines.append("JOBS TODAY: %d scheduled" % len(jobs))
        for j in jobs[:3]:
            t = j.get("scheduled_time", "TBD")
            client = j.get("client_name", "Unknown")[:25]
            lines.append("  %s - %s" % (t, client))
        if len(jobs) > 3:
            lines.append("  ... +%d more" % (len(jobs) - 3))
    else:
        lines.append("JOBS TODAY: Check calendar — Supabase sync may be offline.")
    lines.append("")

    # Pending SMS
    sms = get_acs_pending_sms()
    if sms:
        lines.append("UNRESOLVED iMESSAGES (%d):" % len(sms))
        for sender, subj, body in sms:
            lines.append("  -> %s: %s" % (sender[:20], (subj or body)[:60]))
        lines.append("")

    # Follow-ups
    followups = get_acs_followups_due()
    if followups:
        lines.append("FOLLOW-UPS DUE:")
        for name, company, due in followups:
            co = " @ %s" % company.rstrip(";").strip() if company and company.strip() else ""
            lines.append("  -> %s%s [due %s]" % (name, co, due))
        lines.append("")

    lines.append("What's the first thing on your plate today?")
    return "\n".join(lines)


def build_cc_briefing():
    """Tailored briefing for Agent CC (Bailey) — Content Co-op."""
    lines = ["GOOD MORNING CC — %s" % TODAY, ""]
    lines.append("Your CC briefing for today.")
    lines.append("")

    # Pipeline
    pipeline = get_cc_pipeline()
    if pipeline:
        lines.append("PIPELINE:")
        for line in pipeline.strip().split("\n")[:6]:
            if line.strip():
                lines.append("  %s" % line.strip())
        lines.append("")

    # Follow-ups
    followups = get_cc_followups_due()
    if followups:
        lines.append("FOLLOW-UPS DUE:")
        for name, company, due in followups:
            co = " @ %s" % company.rstrip(";").strip() if company and company.strip() else ""
            lines.append("  -> %s%s [due %s]" % (name, co, due))
        lines.append("")

    # YouTube
    yt = get_youtube_queue()
    if yt:
        lines.append("KALLAWAY YOUTUBE:")
        for line in yt.split("\n"):
            lines.append("  %s" % line)
        lines.append("")

    lines.append("What's the priority for the CC team today?")
    return "\n".join(lines)


def main():
    start = datetime.now()

    print("[agent_briefings] Dispatching morning briefings — %s" % TODAY)

    # Build both briefings
    astro_msg = build_astro_briefing()
    cc_msg = build_cc_briefing()

    # Dispatch
    ok_astro = send_to_agent("acs-worker", astro_msg)
    ok_cc = send_to_agent("cc-worker", cc_msg)

    elapsed = (datetime.now() - start).total_seconds()
    summary = "Dispatched: astro=%s cc=%s (%.1fs)" % (
        "ok" if ok_astro else "fail",
        "ok" if ok_cc else "fail",
        elapsed
    )
    print("[agent_briefings] %s" % summary)
    log_cron("agent_morning_briefings", "success" if (ok_astro and ok_cc) else "partial", summary)


if __name__ == "__main__":
    main()
