#!/usr/bin/env python3
"""
crew_daily_checklist.py — Morning checklist for ACS crew members
Runs daily at 6 AM via LaunchAgent. Pulls today's jobs from Supabase
and Google Calendar, builds a formatted checklist, sends via iMessage.

Python 3.9 compatible (no f-string backslashes, no walrus in comprehensions).
"""

import json
import os
import sys
import subprocess
import datetime
import logging
import traceback

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SUPABASE_URL = "https://briokwdoonawhxisbydy.supabase.co"
SUPABASE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJyaW9rd2Rvb25hd2h4aXNieWR5Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIs"
    "ImlhdCI6MTc3MTU1Njc2MiwiZXhwIjoyMDg3MTMyNzYyfQ."
    "5V1BsTrqIHGKUUHYJ3PBpL9re_WzKqOzKoQ94dc3me8"
)
ACS_BUSINESS_ID = "0ade82e3-ffe9-4c17-ae59-fc4bd198482b"
IMSG_BIN = "/opt/homebrew/bin/imsg"
SERVICE_ACCOUNT_PATH = (
    "/Users/_mxappservice/.gemini/antigravity/playground/"
    "perihelion-armstrong/service_account.json"
)
CALENDAR_ID = "caio@astrocleanings.com"

# Manager receives full summary
MANAGER_IDS = {"dae379c3-b17e-4ac5-b67a-31b8cc81b503"}

LOG_DIR = "/Users/_mxappservice/logs"
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(LOG_DIR, "crew-checklist.log")),
    ],
)
log = logging.getLogger("crew_checklist")

# ---------------------------------------------------------------------------
# Supabase helpers (REST API, no SDK needed)
# ---------------------------------------------------------------------------
try:
    import urllib.request
    import urllib.error
    import urllib.parse
except ImportError:
    pass


def sb_request(path, method="GET", body=None):
    """Make a Supabase REST API request."""
    url = SUPABASE_URL + "/rest/v1/" + path
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": "Bearer " + SUPABASE_KEY,
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        log.error("Supabase %s %s -> %s: %s", method, path, e.code, e.read().decode())
        return []


def get_crew_members():
    """Fetch all active crew members."""
    path = "crew_members?business_id=eq.{bid}&status=eq.active&select=id,name,phone,role".format(
        bid=ACS_BUSINESS_ID
    )
    return sb_request(path)


def get_crew_jobs_today(crew_member_id, today_start, today_end):
    """Fetch today's jobs for a crew member via job_crew_assignments join."""
    # Step 1: get job IDs from assignments
    assign_path = "job_crew_assignments?crew_member_id=eq.{cid}&select=job_id".format(
        cid=crew_member_id
    )
    assignments = sb_request(assign_path)
    if not assignments:
        return []

    job_ids = [a["job_id"] for a in assignments if a.get("job_id")]
    if not job_ids:
        return []

    # Step 2: fetch jobs with contacts
    # Supabase REST: in filter uses parentheses
    ids_param = ",".join(job_ids)
    job_path = (
        "jobs?id=in.({ids})"
        "&scheduled_start=gte.{start}"
        "&scheduled_start=lte.{end}"
        "&select=id,scheduled_start,scheduled_end,status,notes,"
        "contacts(id,name,phone,street_address,city,state,zip,metadata)"
        "&order=scheduled_start.asc"
    ).format(ids=ids_param, start=today_start, end=today_end)
    return sb_request(job_path)


# ---------------------------------------------------------------------------
# Google Calendar fallback
# ---------------------------------------------------------------------------
def get_calendar_events_today(today_start_dt, today_end_dt):
    """Pull today's events from Google Calendar as fallback data source."""
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError:
        log.warning("google-auth / google-api-python-client not installed, skipping calendar fallback")
        return []

    try:
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_PATH,
            scopes=["https://www.googleapis.com/auth/calendar"],
        )
        creds = creds.with_subject(CALENDAR_ID)
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        time_min = today_start_dt.isoformat() + "Z"
        time_max = today_end_dt.isoformat() + "Z"
        result = service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=50,
        ).execute()
        return result.get("items", [])
    except Exception as e:
        log.error("Calendar API error: %s", e)
        return []


def parse_calendar_event(event):
    """Extract useful fields from a Google Calendar event."""
    summary = event.get("summary", "Unknown")
    description = event.get("description", "")
    location = event.get("location", "")
    start_raw = event.get("start", {})
    start_time = start_raw.get("dateTime", start_raw.get("date", ""))

    # Parse access codes from description
    access_codes = ""
    notes = ""
    if description:
        lines = description.split("\n")
        for line in lines:
            lower = line.lower().strip()
            if "code" in lower or "gate" in lower or "alarm" in lower or "key" in lower or "access" in lower:
                if access_codes:
                    access_codes += " | "
                access_codes += line.strip()
            else:
                if notes:
                    notes += "\n"
                notes += line.strip()

    return {
        "summary": summary,
        "location": location,
        "start_time": start_time,
        "access_codes": access_codes,
        "notes": notes.strip(),
    }


# ---------------------------------------------------------------------------
# iMessage sender
# ---------------------------------------------------------------------------
def send_imessage(phone, message):
    """Send iMessage using imsg binary. Known: process hangs on ack after send."""
    if not os.path.exists(IMSG_BIN):
        log.error("imsg binary not found at %s", IMSG_BIN)
        return False

    log.info("Sending iMessage to %s (%d chars)", phone, len(message))
    try:
        proc = subprocess.Popen(
            [IMSG_BIN, "send", "--to", phone, "--text", message, "--service", "imessage"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            proc.communicate(timeout=6)
        except subprocess.TimeoutExpired:
            # Message sent, process hangs on ack — expected behavior
            proc.kill()
            proc.wait()
        return True
    except Exception as e:
        log.error("Failed to send iMessage to %s: %s", phone, e)
        return False


# ---------------------------------------------------------------------------
# Checklist builder
# ---------------------------------------------------------------------------
def format_time(iso_str):
    """Parse ISO time to 'h:MM AM' format."""
    if not iso_str:
        return "TBD"
    try:
        # Handle both Z and +00:00 formats
        clean = iso_str.replace("Z", "+00:00")
        if "T" in clean:
            dt = datetime.datetime.fromisoformat(clean)
            # Convert to Central Time (UTC-6)
            ct = dt - datetime.timedelta(hours=6)
            return ct.strftime("%-I:%M %p")
        return iso_str
    except Exception:
        return iso_str


def extract_access_codes(contact):
    """Pull access codes from contact metadata."""
    if not contact:
        return ""
    metadata = contact.get("metadata") or {}
    codes = metadata.get("access_codes")
    if not codes:
        return ""
    if isinstance(codes, dict):
        parts = []
        for key, val in codes.items():
            parts.append("{}: {}".format(key, val))
        return " | ".join(parts)
    return str(codes)


def extract_notes_from_job(job):
    """Pull special notes from job notes field, filtering out import boilerplate."""
    notes = job.get("notes") or ""
    lines = notes.split("\n")
    useful = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Skip the auto-import line
        if stripped.startswith("Imported from Google Calendar"):
            continue
        # Clean up "Calendar Notes:" prefix
        if stripped.startswith("Calendar Notes:"):
            stripped = stripped[len("Calendar Notes:"):].strip()
        if stripped:
            useful.append(stripped)
    return "\n".join(useful)


def build_address(contact):
    """Build address string from contact fields."""
    if not contact:
        return ""
    parts = [
        contact.get("street_address", ""),
        contact.get("city", ""),
        contact.get("state", ""),
    ]
    return ", ".join(p for p in parts if p)


def build_checklist_message(crew_name, jobs, today_dt):
    """Build formatted checklist message for a crew member."""
    day_label = today_dt.strftime("%A, %B %-d")
    first_name = crew_name.split()[0] if crew_name else "Team"

    msg = "Good morning {}! Your schedule for {}:\n".format(first_name, day_label)

    if not jobs:
        msg += "\nNo jobs scheduled for today. Enjoy your day off!\n"
        msg += "\nFull details: astrocleanings.com/crew"
        return msg

    for i, job in enumerate(jobs, 1):
        contact = job.get("contacts") or {}
        client_name = contact.get("name", "Client")
        address = build_address(contact)
        time_str = format_time(job.get("scheduled_start"))
        end_str = format_time(job.get("scheduled_end"))
        access = extract_access_codes(contact)
        notes = extract_notes_from_job(job)

        msg += "\n{}. {} - {}".format(i, time_str, client_name)
        if end_str and end_str != "TBD":
            msg += " (until {})".format(end_str)
        if address:
            msg += "\n   {}".format(address)
        if access:
            msg += "\n   Access: {}".format(access)
        if notes:
            for note_line in notes.split("\n"):
                msg += "\n   Note: {}".format(note_line)

    msg += "\n\nTotal: {} job{} today".format(len(jobs), "s" if len(jobs) != 1 else "")
    msg += "\nFull details: astrocleanings.com/crew"
    return msg


def build_manager_summary(all_crew_jobs, today_dt):
    """Build a summary message for managers showing all crew schedules."""
    day_label = today_dt.strftime("%A, %B %-d")
    msg = "ACS Daily Summary - {}\n".format(day_label)
    msg += "=" * 35

    total_jobs = 0
    for crew_name, jobs in all_crew_jobs.items():
        job_count = len(jobs)
        total_jobs += job_count
        msg += "\n\n{} ({} job{}):".format(crew_name, job_count, "s" if job_count != 1 else "")
        if not jobs:
            msg += "\n  Day off"
        else:
            for job in jobs:
                contact = job.get("contacts") or {}
                client_name = contact.get("name", "Client")
                time_str = format_time(job.get("scheduled_start"))
                status = job.get("status", "scheduled")
                msg += "\n  {} - {} [{}]".format(time_str, client_name, status)

    msg += "\n\n{} total jobs across {} crew members".format(
        total_jobs, len(all_crew_jobs)
    )
    return msg


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    log.info("=== Crew Daily Checklist starting ===")

    # Date range for today (UTC)
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # Use Central Time offset: today in CT = UTC minus 6 hours
    # So "today" in CT means we need UTC from 06:00 today to 06:00 tomorrow
    ct_offset = datetime.timedelta(hours=6)
    today_ct_start = today_start + ct_offset  # 6 AM UTC = midnight CT
    today_ct_end = today_ct_start + datetime.timedelta(hours=24) - datetime.timedelta(seconds=1)

    # URL-encode the + as %2B so Supabase REST doesn't decode it as a space
    today_start_iso = today_ct_start.strftime("%Y-%m-%dT%H:%M:%S") + "%2B00:00"
    today_end_iso = today_ct_end.strftime("%Y-%m-%dT%H:%M:%S") + "%2B00:00"
    today_display = (now - ct_offset).replace(hour=0, minute=0, second=0, microsecond=0)

    log.info("Today (CT): %s", today_display.strftime("%Y-%m-%d"))
    log.info("Query window: %s to %s", today_start_iso, today_end_iso)

    # Fetch crew members
    crew_members = get_crew_members()
    if not crew_members:
        log.warning("No active crew members found")
        return

    log.info("Found %d crew members", len(crew_members))

    # Also pull calendar events as fallback
    cal_events = get_calendar_events_today(today_ct_start, today_ct_end)
    log.info("Calendar fallback: %d events", len(cal_events))

    all_crew_jobs = {}  # name -> jobs list for manager summary

    for member in crew_members:
        member_id = member["id"]
        member_name = member.get("name", "Crew")
        member_phone = member.get("phone", "")
        member_role = member.get("role", "cleaner")

        log.info("Processing %s (%s)", member_name, member_phone)

        # Get jobs from Supabase
        jobs = get_crew_jobs_today(member_id, today_start_iso, today_end_iso)
        log.info("  Supabase jobs: %d", len(jobs))

        all_crew_jobs[member_name] = jobs

        # Build and send checklist
        checklist = build_checklist_message(member_name, jobs, today_display)

        if member_phone:
            # Normalize phone for imsg
            phone = member_phone.strip()
            if not phone.startswith("+"):
                digits = "".join(c for c in phone if c.isdigit())
                if len(digits) == 10:
                    phone = "+1" + digits
                elif len(digits) == 11 and digits.startswith("1"):
                    phone = "+" + digits
            send_imessage(phone, checklist)
            log.info("  Sent checklist to %s", member_name)
        else:
            log.warning("  No phone for %s, skipping iMessage", member_name)

    # Send manager summary
    for member in crew_members:
        if member["id"] in MANAGER_IDS:
            manager_phone = member.get("phone", "")
            if manager_phone:
                phone = manager_phone.strip()
                if not phone.startswith("+"):
                    digits = "".join(c for c in phone if c.isdigit())
                    if len(digits) == 10:
                        phone = "+1" + digits
                    elif len(digits) == 11 and digits.startswith("1"):
                        phone = "+" + digits
                summary = build_manager_summary(all_crew_jobs, today_display)
                send_imessage(phone, summary)
                log.info("Sent manager summary to %s", member.get("name"))

    log.info("=== Crew Daily Checklist complete ===")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.error("Fatal error:\n%s", traceback.format_exc())
        sys.exit(1)
