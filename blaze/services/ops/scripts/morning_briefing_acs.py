#!/usr/bin/env python3
"""
Morning Briefing — ACS V3
Text via Telegram + branded HTML email via Gmail DWD.
Python 3.9 compatible.
"""

import sys
import os
import json
import sqlite3
import subprocess
import time
import re
import unicodedata
import html as html_mod
import urllib.request
import base64
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ── Config ─────────────────────────────────────
DATA_ROOT = "/Users/_mxappservice/blaze-data"
BLAZE_DB = "%s/blaze.db" % DATA_ROOT
EVENT_STREAM_DB = "%s/event_stream.db" % DATA_ROOT
SCRIPTS_DIR = "/Users/_mxappservice/ACS_CC_AUTOBOT/blaze-v4/ops/scripts"
SA_FILE = "/Users/_mxappservice/.gemini/antigravity/playground/perihelion-armstrong/service_account.json"
CALENDAR_ID = "caio@astrocleanings.com"
EMAIL_FROM = "blaze@contentco-op.com"
EMAIL_TO = "caio@astrocleanings.com"
SUPABASE_URL = "https://briokwdoonawhxisbydy.supabase.co"
SUPABASE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJyaW9rd2Rvb25hd2h4aXNieWR5Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3MTU1Njc2MiwiZXhwIjoyMDg3MTMyNzYyfQ."
    "5V1BsTrqIHGKUUHYJ3PBpL9re_WzKqOzKoQ94dc3me8"
)
ACS_BUSINESS_ID = "0ade82e3-ffe9-4c17-ae59-fc4bd198482b"

SEP = "\u2501" * 44

CREW_COLORS = {
    "caio": {"bg": "rgba(26,115,232,0.15)", "fg": "#4fc3f7"},
    "alex": {"bg": "rgba(102,187,106,0.15)", "fg": "#66bb6a"},
    "yennifer": {"bg": "rgba(230,124,115,0.15)", "fg": "#e67c73"},
    "dora": {"bg": "rgba(244,81,30,0.15)", "fg": "#f4511e"},
}

sys.path.insert(0, SCRIPTS_DIR)


# ── Helpers ────────────────────────────────────

def sb_get(path):
    url = SUPABASE_URL + "/rest/v1/" + path
    req = urllib.request.Request(url, headers={
        "apikey": SUPABASE_KEY,
        "Authorization": "Bearer " + SUPABASE_KEY,
    })
    try:
        return json.loads(urllib.request.urlopen(req, timeout=10).read())
    except Exception:
        return []


def _open_db(path):
    if not os.path.exists(path):
        return None
    conn = sqlite3.connect(path, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def strip_accents(s):
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def _esc(s):
    if not s:
        return ""
    return html_mod.escape(str(s))


def _parse_time(start_raw):
    """Parse calendar dateTime to (display, hour_str, ampm)."""
    try:
        time_str = start_raw.split("T")[1][:5]
        hour = int(time_str.split(":")[0])
        minute = time_str.split(":")[1]
        ampm = "AM" if hour < 12 else "PM"
        dh = hour
        if hour > 12:
            dh = hour - 12
        if hour == 0:
            dh = 12
        display = "%d:%s %s" % (dh, minute, ampm)
        hour_str = "%d:%s" % (dh, minute)
        return display, hour_str, ampm
    except Exception:
        return start_raw[:16], start_raw[:16], ""


# ── Data Fetchers (return structured data) ─────

def fetch_schedule():
    """Today's cleaning jobs with enrichment."""
    try:
        from google.oauth2 import service_account as sa_mod
        from googleapiclient.discovery import build

        creds = sa_mod.Credentials.from_service_account_file(
            SA_FILE, scopes=["https://www.googleapis.com/auth/calendar"]
        )
        creds = creds.with_subject(CALENDAR_ID)
        svc = build("calendar", "v3", credentials=creds)

        now = datetime.now()
        t_start = now.replace(hour=0, minute=0, second=0).strftime(
            "%Y-%m-%dT%H:%M:%S-06:00"
        )
        t_end = now.replace(hour=23, minute=59, second=59).strftime(
            "%Y-%m-%dT%H:%M:%S-06:00"
        )

        events = svc.events().list(
            calendarId=CALENDAR_ID,
            timeMin=t_start, timeMax=t_end,
            maxResults=20, singleEvents=True, orderBy="startTime",
        ).execute()

        items = events.get("items", [])
        if not items:
            return []

        # ACS contacts for enrichment
        contacts_raw = sb_get(
            "client_profiles?select=contact_id,"
            "contacts(id,name,phone,metadata)"
            "&business_id=eq." + ACS_BUSINESS_ID + "&limit=100"
        )
        cbn = {}
        for cp in contacts_raw:
            c = cp.get("contacts")
            if c and c.get("name"):
                cbn[strip_accents(c["name"].lower().strip())] = c

        crew_list = sb_get(
            "crew_members?select=id,name,calendar_color_id&limit=10"
        )
        crew_by_color = {}
        for cm in crew_list:
            cid = cm.get("calendar_color_id")
            if cid:
                crew_by_color[str(cid)] = cm.get("name", "")

        jobs = []
        for ev in items:
            title = ev.get("summary", "")
            location = ev.get("location", "")
            desc = ev.get("description", "")
            color_id = ev.get("colorId", "")
            start_raw = ev.get("start", {}).get("dateTime", "")

            if not start_raw or "T" not in start_raw:
                continue

            time_display, hour_str, ampm = _parse_time(start_raw)

            # Match contact
            clean = re.sub(r"\s*\(.*\)\s*$", "", title).strip()
            clean_lower = strip_accents(clean.lower())
            contact = None
            for cname, cdata in cbn.items():
                if cname == clean_lower:
                    contact = cdata
                    break
                if clean_lower in cname or cname in clean_lower:
                    contact = cdata
                    break
                if " " not in clean_lower:
                    first = cname.split()[0] if cname else ""
                    if first == clean_lower:
                        contact = cdata
                        break

            phone = ""
            access = ""
            if contact:
                phone = contact.get("phone", "") or ""
                meta = contact.get("metadata") or {}
                access = meta.get("access_codes", "") or ""

            # Fallback: parse access from description
            if not access and desc:
                acc_lines = []
                in_acc = False
                for dline in desc.split("\n"):
                    ds = dline.strip()
                    if ds.lower().startswith("access:"):
                        in_acc = True
                        rest = ds[7:].strip()
                        if rest:
                            acc_lines.append(rest)
                        continue
                    if in_acc and ds:
                        if any(ds.startswith(p) for p in [
                            "Portal:", "Phone:", "Notes:"
                        ]):
                            in_acc = False
                        elif len(ds) > 1:
                            acc_lines.append(ds)
                    elif in_acc:
                        in_acc = False
                if acc_lines:
                    access = " / ".join(acc_lines)

            crew_name = crew_by_color.get(str(color_id), "")

            notes = []
            if desc:
                for dline in desc.split("\n"):
                    ds = dline.strip()
                    if ds.startswith("Client Note ["):
                        notes.append(ds)

            jobs.append({
                "time_display": time_display,
                "hour": hour_str,
                "ampm": ampm,
                "title": title,
                "location": location,
                "phone": phone,
                "access": access,
                "crew": crew_name,
                "notes": notes,
            })

        return jobs

    except Exception as e:
        try:
            from google_api_manager import get_todays_events
            events = get_todays_events("caio@astrocleanings.com")
            if not events:
                return []
            jobs = []
            for ev in events:
                t = ev.get("time", "")
                ampm = ""
                hr = t
                if " AM" in t or " PM" in t:
                    parts = t.rsplit(" ", 1)
                    hr = parts[0]
                    ampm = parts[1]
                jobs.append({
                    "time_display": t, "hour": hr, "ampm": ampm,
                    "title": ev.get("title", ""),
                    "location": ev.get("location", ""),
                    "phone": "", "access": "", "crew": "", "notes": [],
                })
            return jobs
        except Exception:
            return []


def fetch_tomorrow():
    """Tomorrow's schedule preview."""
    try:
        from google.oauth2 import service_account as sa_mod
        from googleapiclient.discovery import build

        creds = sa_mod.Credentials.from_service_account_file(
            SA_FILE, scopes=["https://www.googleapis.com/auth/calendar"]
        )
        creds = creds.with_subject(CALENDAR_ID)
        svc = build("calendar", "v3", credentials=creds)

        tmrw = datetime.now() + timedelta(days=1)
        t_start = tmrw.replace(hour=0, minute=0, second=0).strftime(
            "%Y-%m-%dT%H:%M:%S-06:00"
        )
        t_end = tmrw.replace(hour=23, minute=59, second=59).strftime(
            "%Y-%m-%dT%H:%M:%S-06:00"
        )

        events = svc.events().list(
            calendarId=CALENDAR_ID,
            timeMin=t_start, timeMax=t_end,
            maxResults=20, singleEvents=True, orderBy="startTime",
        ).execute()

        results = []
        for ev in events.get("items", []):
            title = ev.get("summary", "")
            start_raw = ev.get("start", {}).get("dateTime", "")
            if not start_raw or "T" not in start_raw:
                continue
            td, hr, ap = _parse_time(start_raw)
            results.append({"time_display": td, "hour": hr, "title": title})
        return results
    except Exception:
        return []


def fetch_emails():
    """Recent actionable emails for caio@."""
    results = []
    try:
        if os.path.exists(EVENT_STREAM_DB):
            conn = _open_db(EVENT_STREAM_DB)
            if conn:
                rows = conn.execute(
                    "SELECT sender, subject FROM events "
                    "WHERE source='gmail' AND business_unit='ACS' "
                    "AND created_at > datetime('now', '-12 hours') "
                    "ORDER BY score DESC LIMIT 6"
                ).fetchall()
                conn.close()
                for sender, subject in rows:
                    results.append({"sender": sender or "?", "subject": subject or ""})
                if results:
                    return results
    except Exception:
        pass
    try:
        from google_api_manager import get_recent_emails
        emails = get_recent_emails(
            "caio@astrocleanings.com",
            max_results=6,
            query="is:unread is:inbox -category:promotions newer_than:1d"
        )
        for e in (emails or [])[:6]:
            results.append({
                "sender": e.get("from", "?"),
                "subject": e.get("subject", ""),
            })
    except Exception:
        pass
    return results


def fetch_leads():
    """New ACS leads from the last 24h."""
    try:
        if not os.path.exists(EVENT_STREAM_DB):
            return []
        conn = _open_db(EVENT_STREAM_DB)
        if not conn:
            return []
        rows = conn.execute(
            "SELECT source, sender, subject, body FROM events "
            "WHERE business_unit='ACS' "
            "AND source IN ('gmail', 'imessage') "
            "AND created_at > datetime('now', '-24 hours') "
            "AND (subject LIKE '%%quote%%' OR subject LIKE '%%clean%%' "
            "     OR subject LIKE '%%estimate%%' OR subject LIKE '%%bid%%' "
            "     OR body LIKE '%%quote%%' OR body LIKE '%%clean%%') "
            "ORDER BY created_at DESC LIMIT 4"
        ).fetchall()
        conn.close()
        results = []
        for source, sender, subject, body in rows:
            tag = "EMAIL" if source == "gmail" else "MSG"
            display = subject if subject else (body[:50] if body else "")
            results.append({
                "tag": tag,
                "sender": sender or "?",
                "subject": display,
            })
        return results
    except Exception:
        return []


def fetch_applicants():
    """Job applicants pending review."""
    rows = sb_get(
        "job_applicants?select=name,score,status,applied_at"
        "&status=eq.new&order=score.desc.nullslast&limit=5"
    )
    results = []
    for r in (rows or []):
        results.append({
            "name": r.get("name", "?"),
            "score": r.get("score"),
        })
    return results


def fetch_actions():
    """Real action items from Supabase."""
    items = []

    requests = sb_get(
        "client_requests?select=id,type,message,status,created_at,"
        "contacts(name)"
        "&status=eq.pending&order=created_at.desc&limit=5"
    )
    for r in (requests or []):
        cname = (r.get("contacts") or {}).get("name", "Client")
        msg = (r.get("message") or "")[:60]
        items.append("Reply to %s: %s" % (cname, msg))

    now = datetime.now()
    td_start = now.replace(hour=0, minute=0, second=0).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    td_end = now.replace(hour=23, minute=59, second=59).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    jobs = sb_get(
        "jobs?select=id,scheduled_start,contact_id,contacts(name),"
        "job_crew_assignments(crew_member_id)"
        "&business_id=eq." + ACS_BUSINESS_ID
        + "&scheduled_start=gte." + td_start
        + "&scheduled_start=lte." + td_end
        + "&limit=20"
    )
    for j in (jobs or []):
        assignments = j.get("job_crew_assignments") or []
        if not assignments:
            cname = (j.get("contacts") or {}).get("name", "Unknown")
            items.append("Assign crew for %s" % cname)

    unscored = sb_get(
        "job_applicants?select=name&score=is.null&status=eq.new&limit=3"
    )
    if unscored:
        names = [a.get("name", "?") for a in unscored]
        items.append("Review applicants: %s" % ", ".join(names))

    return items


# ── Text Builder (Telegram) ───────────────────

def build_text(data):
    now = datetime.now()
    day_str = now.strftime("%A %b %-d").upper()

    out = []
    out.append("GOOD MORNING CAIO -- %s" % day_str)
    out.append(SEP)

    actions = data.get("actions", [])
    if actions:
        out.append("")
        out.append("ACTION ITEMS")
        out.append(SEP)
        for item in actions:
            out.append("  -> %s" % item)

    jobs = data.get("schedule", [])
    out.append("")
    out.append("TODAY'S SCHEDULE")
    out.append(SEP)
    if jobs:
        for j in jobs:
            out.append("")
            out.append("  %s  %s" % (j["time_display"], j["title"]))
            if j["location"]:
                out.append("   %s" % j["location"])
            if j["phone"]:
                out.append("   Tel: %s" % j["phone"])
            if j["access"]:
                out.append("   Access: %s" % j["access"])
            if j["crew"]:
                out.append("   Crew: %s" % j["crew"])
            for n in j.get("notes", []):
                out.append("   NOTE: %s" % n)
    else:
        out.append("  No cleanings today.")

    tmrw = data.get("tomorrow", [])
    out.append("")
    out.append("TOMORROW")
    out.append(SEP)
    if tmrw:
        for t in tmrw:
            out.append("  %s - %s" % (t["time_display"], t["title"]))
    else:
        out.append("  No cleanings scheduled.")

    emails = data.get("emails", [])
    if emails:
        out.append("")
        out.append("EMAIL")
        out.append(SEP)
        for e in emails:
            out.append("  -> %s - %s" % (e["sender"], e["subject"]))

    leads = data.get("leads", [])
    if leads:
        out.append("")
        out.append("NEW LEADS")
        out.append(SEP)
        for l in leads:
            out.append("  [%s] %s - %s" % (l["tag"], l["sender"], l["subject"]))

    applicants = data.get("applicants", [])
    if applicants:
        out.append("")
        out.append("APPLICANTS TO REVIEW")
        out.append(SEP)
        for a in applicants:
            score = a.get("score")
            s = " (score: %s)" % score if score else ""
            out.append("  -> %s%s" % (a["name"], s))

    elapsed = data.get("elapsed", 0)
    out.append("")
    out.append(SEP)
    out.append("  Built in %.1fs. Reply here if you need anything." % elapsed)
    out.append(SEP)

    return "\n".join(out)


# ── HTML Builder (Email) ──────────────────────

def build_html(data):
    """Branded ACS dark template — blue/cyan gradient."""
    h = []
    e = _esc

    now = datetime.now()
    date_long = now.strftime("%A, %B %-d, %Y")
    date_short = now.strftime("%b %-d")
    day_abbr = now.strftime("%a")
    tmrw_day = (now + timedelta(days=1)).strftime("%A").upper()

    jobs = data.get("schedule", [])
    tmrw = data.get("tomorrow", [])
    actions = data.get("actions", [])
    emails = data.get("emails", [])
    leads = data.get("leads", [])
    applicants = data.get("applicants", [])
    elapsed = data.get("elapsed", 0)

    n_jobs = len(jobs)
    n_tmrw = len(tmrw)
    n_actions = len(actions)
    n_leads = len(leads)

    # ── Document start ──
    h.append('<!DOCTYPE html>')
    h.append('<html><head><meta charset="utf-8">')
    h.append('<meta name="viewport" content="width=device-width, initial-scale=1.0">')
    h.append('<title>ACS Daily Brief</title></head>')
    h.append('<body style="margin:0;padding:0;background:#060e1a;'
             'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\','
             'Roboto,sans-serif;">')

    # Outer wrapper
    h.append('<table width="100%%" cellpadding="0" cellspacing="0" '
             'style="background:#060e1a;padding:24px 0;">')
    h.append('<tr><td align="center">')
    h.append('<table width="540" cellpadding="0" cellspacing="0" '
             'style="background:#0b1929;border-radius:16px;overflow:hidden;'
             'border:1px solid rgba(79,195,247,0.12);">')

    # ── Header ──
    h.append('<tr><td style="background:linear-gradient(180deg,#0b1929 0%%,'
             '#0d2240 50%%,#0b1929 100%%);padding:28px 24px 20px;'
             'border-bottom:1px solid rgba(79,195,247,0.1);">')
    h.append('<table width="100%%" cellpadding="0" cellspacing="0"><tr>')
    h.append('<td width="52" valign="top">')
    h.append('<img src="https://astrocleanings.com/assets/logo-texas-transparent.png" '
             'width="44" height="44" alt="ACS" '
             'style="border-radius:10px;display:block;">')
    h.append('</td>')
    h.append('<td style="padding-left:12px;">')
    h.append('<div style="color:#4fc3f7;font-size:10px;font-weight:700;'
             'letter-spacing:3px;margin-bottom:4px;">ASTRO CLEANING SERVICES</div>')
    h.append('<div style="color:#e6edf3;font-size:22px;font-weight:800;'
             'letter-spacing:-0.5px;">Daily Operations Brief</div>')
    h.append('</td>')
    h.append('<td align="right" valign="top">')
    h.append('<div style="color:#7d8ca3;font-size:12px;text-align:right;">'
             + e(now.strftime("%A")) + '<br>')
    h.append('<span style="color:#4fc3f7;font-size:18px;font-weight:800;">'
             + e(date_short) + '</span></div>')
    h.append('</td>')
    h.append('</tr></table>')
    h.append('</td></tr>')

    # ── Pulse Bar ──
    h.append('<tr><td style="padding:20px 24px 0;">')
    h.append('<table width="100%%" cellpadding="0" cellspacing="0" '
             'style="border-radius:10px;overflow:hidden;"><tr>')

    pulse_items = [
        (str(n_jobs), "JOBS", "rgba(26,115,232,0.12)", "#4fc3f7"),
        (str(n_tmrw), "TOMORROW", "rgba(46,125,50,0.10)", "#66bb6a"),
        (str(n_actions), "ACTIONS", "rgba(245,124,0,0.10)", "#ffa726"),
        (str(n_leads), "LEADS", "rgba(79,195,247,0.08)", "#81d4fa"),
    ]
    for val, label, bg, fg in pulse_items:
        h.append('<td width="25%%" style="background:' + bg
                 + ';padding:14px 0;text-align:center;">')
        h.append('<div style="color:' + fg
                 + ';font-size:24px;font-weight:800;">' + val + '</div>')
        h.append('<div style="color:#546e8a;font-size:9px;font-weight:700;'
                 'letter-spacing:1.5px;margin-top:2px;">' + label + '</div>')
        h.append('</td>')

    h.append('</tr></table>')
    h.append('</td></tr>')

    # ── Action Items ──
    if actions:
        h.append('<tr><td style="padding:20px 24px 16px;">')
        h.append('<table width="100%%" cellpadding="0" cellspacing="0" '
                 'style="background:rgba(255,167,38,0.05);'
                 'border:1px solid rgba(255,167,38,0.12);border-radius:10px;">')
        h.append('<tr><td style="padding:16px 20px;">')
        h.append('<div style="color:#ffa726;font-size:10px;font-weight:800;'
                 'letter-spacing:2px;margin-bottom:10px;">'
                 '&#9889; NEEDS YOUR ATTENTION</div>')
        h.append('<table width="100%%" cellpadding="0" cellspacing="0">')
        for action in actions:
            h.append('<tr><td style="padding:3px 0;color:#e6edf3;font-size:13px;">')
            h.append('<span style="color:#ffa726;margin-right:8px;">&#8227;</span> '
                     + e(action))
            h.append('</td></tr>')
        h.append('</table>')
        h.append('</td></tr></table>')
        h.append('</td></tr>')

    # ── Schedule Header ──
    h.append('<tr><td style="padding:8px 24px 0;">')
    h.append('<div style="color:#4fc3f7;font-size:10px;font-weight:800;'
             'letter-spacing:2px;">TODAY\'S SCHEDULE</div>')
    h.append('<div style="height:2px;background:linear-gradient(to right,'
             '#1a73e8,#4fc3f7,transparent);margin-top:8px;border-radius:1px;">'
             '</div>')
    h.append('</td></tr>')

    # ── Timeline ──
    h.append('<tr><td style="padding:20px 24px;">')

    if not jobs:
        h.append('<div style="color:#7d8ca3;font-size:14px;padding:12px 0;">'
                 'No cleanings scheduled today.</div>')
    else:
        for idx, job in enumerate(jobs):
            is_last = (idx == len(jobs) - 1)
            margin = "0" if is_last else "18px"

            h.append('<table width="100%%" cellpadding="0" cellspacing="0" '
                     'style="margin-bottom:' + margin + ';">')
            h.append('<tr>')

            # Time column
            h.append('<td width="56" valign="top" style="padding-top:2px;">')
            h.append('<div style="color:#4fc3f7;font-size:14px;font-weight:800;">'
                     + e(job["hour"]) + '</div>')
            h.append('<div style="color:#546e8a;font-size:10px;font-weight:600;">'
                     + e(job["ampm"]) + '</div>')
            h.append('</td>')

            # Dot + connector
            h.append('<td width="24" valign="top" style="padding-top:3px;">')
            h.append('<div style="width:10px;height:10px;border-radius:50%%;'
                     'background:linear-gradient(135deg,#1a73e8,#4fc3f7);'
                     'margin:0 auto;"></div>')
            if not is_last:
                h.append('<div style="width:2px;height:60px;'
                         'background:linear-gradient(180deg,#1a73e8,'
                         'rgba(26,115,232,0.15));margin:4px auto 0;"></div>')
            h.append('</td>')

            # Content
            h.append('<td style="padding-left:10px;">')
            h.append('<div style="color:#e6edf3;font-size:16px;font-weight:700;">'
                     + e(job["title"]) + '</div>')

            # Details block
            detail_lines = []
            if job["location"]:
                detail_lines.append(e(job["location"]))
            if job["access"]:
                detail_lines.append(
                    '<span style="color:#4fc3f7;">Access:</span> '
                    + e(job["access"])
                )
            if job["phone"]:
                detail_lines.append(
                    '<span style="color:#81d4fa;">Tel:</span> '
                    + e(job["phone"])
                )
            for note in job.get("notes", []):
                detail_lines.append(
                    '<span style="color:#ffa726;">Note:</span> '
                    + e(note)
                )

            if detail_lines:
                h.append('<div style="color:#7d8ca3;font-size:12px;'
                         'margin-top:5px;line-height:1.7;">')
                h.append('<br>'.join(detail_lines))
                h.append('</div>')

            # Crew badge
            if job["crew"]:
                crew_key = job["crew"].lower()
                colors = CREW_COLORS.get(crew_key, {
                    "bg": "rgba(26,115,232,0.15)", "fg": "#4fc3f7"
                })
                h.append('<div style="margin-top:6px;">')
                h.append('<span style="background:' + colors["bg"]
                         + ';color:' + colors["fg"]
                         + ';font-size:10px;font-weight:700;padding:3px 8px;'
                         'border-radius:5px;">'
                         + e(job["crew"]).upper() + '</span>')
                h.append('</div>')

            h.append('</td>')
            h.append('</tr></table>')

    h.append('</td></tr>')

    # ── Tomorrow ──
    h.append('<tr><td style="padding:8px 24px 0;">')
    h.append('<div style="color:#66bb6a;font-size:10px;font-weight:800;'
             'letter-spacing:2px;">TOMORROW &mdash; '
             + e(tmrw_day) + '</div>')
    h.append('<div style="height:2px;background:linear-gradient(to right,'
             '#2e7d32,#66bb6a,transparent);margin-top:8px;border-radius:1px;">'
             '</div>')
    h.append('</td></tr>')
    h.append('<tr><td style="padding:14px 24px;">')

    if tmrw:
        h.append('<table width="100%%" cellpadding="0" cellspacing="0">')
        for t in tmrw:
            h.append('<tr>')
            h.append('<td width="56" style="color:#546e8a;font-size:12px;'
                     'padding:4px 0;">' + e(t["hour"]) + '</td>')
            h.append('<td style="color:#e6edf3;font-size:13px;font-weight:600;'
                     'padding:4px 0;">' + e(t["title"]) + '</td>')
            h.append('</tr>')
        h.append('</table>')
    else:
        h.append('<div style="color:#546e8a;font-size:12px;">'
                 'No cleanings scheduled.</div>')

    h.append('</td></tr>')

    # ── Inbox ──
    if emails:
        h.append('<tr><td style="padding:8px 24px 0;">')
        h.append('<div style="color:#81d4fa;font-size:10px;font-weight:800;'
                 'letter-spacing:2px;">INBOX</div>')
        h.append('<div style="height:2px;background:linear-gradient(to right,'
                 '#1a73e8,#81d4fa,transparent);margin-top:8px;'
                 'border-radius:1px;"></div>')
        h.append('</td></tr>')
        h.append('<tr><td style="padding:14px 24px;">')
        h.append('<div style="color:#7d8ca3;font-size:12px;line-height:2.1;">')
        email_lines = []
        for em in emails:
            sender = e(em["sender"].split("<")[0].strip())
            subj = e(em["subject"])
            email_lines.append(
                '<span style="color:#e6edf3;font-weight:600;">'
                + sender + '</span> &mdash; ' + subj
            )
        h.append('<br>'.join(email_lines))
        h.append('</div>')
        h.append('</td></tr>')

    # ── CTA ──
    h.append('<tr><td style="padding:24px 24px 8px;" align="center">')
    h.append('<a href="https://astrocleanings.com/crew" '
             'style="display:inline-block;background:linear-gradient(135deg,'
             '#1a73e8,#4fc3f7);color:#ffffff;font-size:14px;font-weight:700;'
             'padding:14px 40px;border-radius:10px;text-decoration:none;'
             'letter-spacing:0.5px;">Open Crew Portal</a>')
    h.append('</td></tr>')

    # ── Footer ──
    h.append('<tr><td style="padding:20px 24px 24px;">')
    h.append('<table width="100%%" cellpadding="0" cellspacing="0"><tr>')
    h.append('<td style="color:#2d4058;font-size:11px;">'
             'Astro Cleaning Services &bull; Houston, TX</td>')
    h.append('<td align="right" style="color:#1e3048;font-size:10px;">'
             'Powered by Blaze &bull; Built in %.1fs</td>' % elapsed)
    h.append('</tr></table>')
    h.append('</td></tr>')

    # ── Close ──
    h.append('</table>')
    h.append('</td></tr></table>')
    h.append('</body></html>')

    return '\n'.join(h)


# ── Delivery ──────────────────────────────────

def send_telegram(message):
    env = dict(os.environ)
    env["PATH"] = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
    cmd = [
        "/usr/local/bin/openclaw", "message", "send",
        "--channel", "telegram",
        "--account", "astro",
        "--target", "telegram:7124538299",
        "--message", message,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, env=env,
        )
        if result.returncode == 0:
            return True, "sent"
        return False, result.stderr.strip()[:200]
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as ex:
        return False, str(ex)


def send_email(html_body, text_body, subject):
    """Send branded HTML email via Gmail DWD (blaze@contentco-op.com)."""
    try:
        from google.oauth2 import service_account as sa_mod
        from googleapiclient.discovery import build

        creds = sa_mod.Credentials.from_service_account_file(
            SA_FILE,
            scopes=["https://www.googleapis.com/auth/gmail.compose"],
        )
        creds = creds.with_subject(EMAIL_FROM)
        gmail = build("gmail", "v1", credentials=creds)

        msg = MIMEMultipart("alternative")
        msg["To"] = EMAIL_TO
        msg["From"] = "Blaze - ACS Ops <%s>" % EMAIL_FROM
        msg["Subject"] = subject

        msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        gmail.users().messages().send(
            userId="me",
            body={"raw": raw},
        ).execute()

        return True, "sent"
    except Exception as ex:
        return False, str(ex)[:200]


def log_cron(job, status, summary, error=None):
    try:
        conn = _open_db(BLAZE_DB)
        if not conn:
            return
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        conn.execute(
            "INSERT INTO cron_runs "
            "(job_name, started_at, completed_at, status, output_summary, "
            "error_message) VALUES (?, ?, ?, ?, ?, ?)",
            (job, now_str, now_str, status, summary, error),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ── Main ──────────────────────────────────────

def main():
    start = time.time()

    # Parallel data fetch
    results = {}
    tasks = {
        "schedule": fetch_schedule,
        "tomorrow": fetch_tomorrow,
        "emails": fetch_emails,
        "leads": fetch_leads,
        "applicants": fetch_applicants,
        "actions": fetch_actions,
    }
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fn): name for name, fn in tasks.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception:
                results[name] = []

    data = {
        "schedule": results.get("schedule", []),
        "tomorrow": results.get("tomorrow", []),
        "emails": results.get("emails", []),
        "leads": results.get("leads", []),
        "applicants": results.get("applicants", []),
        "actions": results.get("actions", []),
        "elapsed": time.time() - start,
    }

    text = build_text(data)
    html = build_html(data)

    test_mode = "--test" in sys.argv

    if test_mode:
        print(text)
        with open("/tmp/acs_briefing_preview.html", "w") as f:
            f.write(html)
        print("\nHTML preview written to /tmp/acs_briefing_preview.html")
        return

    # Send Telegram (text)
    tg_ok, tg_detail = send_telegram(text)

    # Send email (HTML)
    now = datetime.now()
    n_jobs = len(data["schedule"])
    subject = "ACS Daily Brief \u2014 %s %s \u2014 %d Job%s" % (
        now.strftime("%a"),
        now.strftime("%b %-d"),
        n_jobs,
        "" if n_jobs == 1 else "s",
    )
    email_ok, email_detail = send_email(html, text, subject)

    # Report
    parts = []
    if tg_ok:
        parts.append("telegram:ok")
    else:
        parts.append("telegram:FAIL(%s)" % tg_detail[:60])
    if email_ok:
        parts.append("email:ok")
    else:
        parts.append("email:FAIL(%s)" % email_detail[:60])

    status = "success" if (tg_ok or email_ok) else "fail"
    log_cron("morning_briefing_acs", status, ", ".join(parts))

    print("ACS Briefing V3: %s" % ", ".join(parts))

    if not tg_ok and not email_ok:
        print(text)
        sys.exit(1)


if __name__ == "__main__":
    main()
