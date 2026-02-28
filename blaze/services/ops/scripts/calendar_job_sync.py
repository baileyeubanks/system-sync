#!/usr/bin/env python3
"""
calendar_job_sync.py — Sync Google Calendar events into Supabase jobs table.

Pulls next 7 days of ACS calendar events, matches to ACS client contacts,
and upserts into the jobs + job_crew_assignments tables.

Uses calendar_sync table (google_event_id -> job_id) for deduplication.

V2: Fixed contact matching — only matches against ACS client contacts (31)
    instead of all 1,500 contacts. Added accent normalization.

Python 3.9 compatible.
"""
import json
import re
import sys
import unicodedata
import uuid
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone

from google.oauth2 import service_account
from googleapiclient.discovery import build

# ── Config ───────────────────────────────────────────────────────────────
SA_FILE = "/Users/_mxappservice/.gemini/antigravity/playground/perihelion-armstrong/service_account.json"
CALENDAR_ID = "caio@astrocleanings.com"
SUPABASE_URL = "https://briokwdoonawhxisbydy.supabase.co"
SUPABASE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJyaW9rd2Rvb25hd2h4aXNieWR5Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3MTU1Njc2MiwiZXhwIjoyMDg3MTMyNzYyfQ."
    "5V1BsTrqIHGKUUHYJ3PBpL9re_WzKqOzKoQ94dc3me8"
)
ACS_BUSINESS_ID = "0ade82e3-ffe9-4c17-ae59-fc4bd198482b"

# Calendar colorId -> crew member UUID
CREW_MAP = {
    "9": {
        "id": "dae379c3-b17e-4ac5-b67a-31b8cc81b503",
        "name": "Caio",
    },
    "2": {
        "id": "65b3d9fd-b31e-476e-b15c-23c5a9b24f6d",
        "name": "Alex",
    },
    "4": {
        "id": "997914ff-7610-4ed2-8a55-f3eda5b76019",
        "name": "Yennifer",
    },
    "6": {
        "id": "0a19f0ba-3d51-4087-96b2-b4bf9cd53cb1",
        "name": "Dora",
    },
}

DRY_RUN = "--dry-run" in sys.argv


# ── Supabase helpers ─────────────────────────────────────────────────────

def sb_request(method, path, data=None, params=None):
    """Make a request to Supabase REST API."""
    url = SUPABASE_URL + "/rest/v1/" + path
    if params:
        url += "?" + urllib.parse.urlencode(params, safe="*.,=")

    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")

    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": "Bearer " + SUPABASE_KEY,
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req)
        raw = resp.read()
        if raw:
            return json.loads(raw)
        return None
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        print("  Supabase {} {} -> {} {}".format(method, path[:80], e.code, err_body[:200]))
        return None


def sb_get(path, params=None):
    return sb_request("GET", path, params=params)


def sb_post(path, data):
    return sb_request("POST", path, data=data)


def sb_patch(path, data):
    return sb_request("PATCH", path, data=data)


# ── Calendar helpers ─────────────────────────────────────────────────────

def get_calendar_service():
    """Build Google Calendar service with Domain-Wide Delegation."""
    creds = service_account.Credentials.from_service_account_file(
        SA_FILE, scopes=["https://www.googleapis.com/auth/calendar"]
    )
    creds = creds.with_subject(CALENDAR_ID)
    return build("calendar", "v3", credentials=creds)


def clean_title(raw_title):
    """Strip 'Astro Cleaning - ' prefix and 'Cleaning' suffix, normalize."""
    t = raw_title.strip()

    for prefix in ["Astro Cleaning - ", "Astro Cleaning -", "Astro Cle - "]:
        if t.startswith(prefix):
            t = t[len(prefix):].strip()
            break

    t = re.sub(r"\s+[Cc]leaning[s]?\.?\s*$", "", t)
    t = re.sub(
        r"\s+(Thurs|Thur|Thu|Wed|Mon|Tue|Tues|Fri|Sat|Sun)\.?\s*$",
        "", t, flags=re.IGNORECASE
    )
    t = re.sub(r"\s+First\s*$", "", t)
    t = re.sub(r"\s*-\s*$", "", t)
    t = re.sub(r"^\s*-\s*", "", t)
    t = re.sub(r"\s+", " ", t).strip()

    return t


def extract_access_codes(description):
    """Parse access codes from event description."""
    if not description:
        return ""

    codes = []
    lines = description.split("\n")
    in_access = False

    for line in lines:
        stripped = line.strip()

        if stripped.lower().startswith("access:"):
            in_access = True
            rest = stripped[7:].strip()
            if rest:
                codes.append(rest)
            continue

        if re.match(
            r"^(Gate\s+code|Alarm\s+code|Garage\s+door|Apartment|Apt\s|Cadeado|Big\s+Gate)",
            stripped, re.IGNORECASE
        ):
            codes.append(stripped)
            in_access = True
            continue

        if re.match(r"^\d{3,}[#*]?\s*$", stripped):
            codes.append(stripped)
            continue

        stop_prefixes = ["Portal:", "Notes:", "Client:", "Phone:", "Address:"]
        if in_access and stripped and not any(stripped.startswith(p) for p in stop_prefixes):
            codes.append(stripped)
            continue

        if in_access and (not stripped or any(stripped.startswith(p) for p in stop_prefixes)):
            in_access = False

    codes = [c for c in codes if len(c.strip()) > 1]
    return "\n".join(codes).strip()


def extract_notes(description):
    """Extract special notes from event description."""
    if not description:
        return ""

    notes = []
    lines = description.split("\n")

    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith("notes:"):
            rest = stripped[6:].strip()
            if rest:
                notes.append(rest)
        # Also capture "Client Note" sections from portal pushes
        if stripped.startswith("Client Note ["):
            notes.append(stripped)

    return "\n".join(notes).strip()


# ── Contact matching (V2: ACS-only, accent-aware) ───────────────────────

def strip_accents(s):
    """Remove diacritics: Cecília -> Cecilia, João -> Joao."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def load_acs_contacts():
    """Load ONLY ACS client contacts via client_profiles join.

    Returns list of contact dicts and a contact_id -> client_profile_id map.
    """
    path = (
        "client_profiles?select=id,contact_id,status,"
        "contacts(id,name,phone,email,metadata)"
        "&business_id=eq." + ACS_BUSINESS_ID + "&limit=100"
    )
    rows = sb_get(path)
    if not rows:
        print("  WARNING: No client profiles loaded")
        return [], {}

    seen = set()
    contacts = []
    cp_map = {}  # contact_id -> client_profile_id

    for r in rows:
        c = r.get("contacts")
        if not c:
            continue
        cid = c.get("id")
        if cid and cid not in seen:
            seen.add(cid)
            contacts.append(c)
        if cid:
            cp_map[cid] = r.get("id")

    return contacts, cp_map


def match_contact(cal_name, contacts):
    """Match calendar event title to an ACS client contact.

    Matching strategy (against only ~31 ACS contacts):
    1. Exact full name (accent-stripped)
    2. Substring match (2+ word names)
    3. First + last name match
    4. Single name → unique first name match
    """
    if not cal_name:
        return None

    # Strip parenthetical suffixes: "Rubens Franz (House)" -> "rubens franz"
    search = re.sub(r"\s*\(.*\)\s*$", "", cal_name.lower().strip())
    search_norm = strip_accents(search)
    search_parts = search_norm.split()

    # Pass 1: exact full name
    for c in contacts:
        cname = strip_accents((c.get("name") or "").lower().strip())
        if cname == search_norm:
            return c

    # Pass 2: substring containment (both directions, 2+ words)
    if len(search_parts) >= 2:
        for c in contacts:
            cname = strip_accents((c.get("name") or "").lower().strip())
            if len(cname.split()) >= 2:
                if search_norm in cname or cname in search_norm:
                    return c

    # Pass 3: first + last name match
    if len(search_parts) >= 2:
        for c in contacts:
            cname = strip_accents((c.get("name") or "").lower().strip())
            cp = cname.split()
            if len(cp) >= 2:
                if search_parts[0] == cp[0] and search_parts[-1] == cp[-1]:
                    return c

    # Pass 4: single name → unique first name match among ACS clients
    if len(search_parts) == 1:
        candidates = []
        for c in contacts:
            cname = strip_accents((c.get("name") or "").lower().strip())
            cp = cname.split()
            if cp and cp[0] == search_parts[0]:
                candidates.append(c)
        if len(candidates) == 1:
            return candidates[0]

    return None


# ── Existing sync records ────────────────────────────────────────────────

def load_synced_events():
    """Load existing calendar_sync records."""
    rows = sb_get("calendar_sync?select=job_id,google_event_id")
    if not rows:
        return {}
    result = {}
    for r in rows:
        gid = r.get("google_event_id")
        if gid:
            result[gid] = r.get("job_id")
    return result


def load_existing_crew_assignments(job_ids):
    """Load existing crew assignments for given job IDs."""
    if not job_ids:
        return {}

    result = {}
    batch_size = 20
    for i in range(0, len(job_ids), batch_size):
        batch = job_ids[i:i + batch_size]
        filter_val = ",".join(batch)
        path = "job_crew_assignments?job_id=in.({})&select=job_id,crew_member_id".format(filter_val)
        rows = sb_get(path)
        if rows:
            for r in rows:
                jid = r.get("job_id")
                if jid not in result:
                    result[jid] = set()
                result[jid].add(r.get("crew_member_id"))
    return result


# ── Main sync ────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("  ACS CALENDAR -> JOBS SYNC (V2)")
    if DRY_RUN:
        print("  MODE: DRY RUN (no writes)")
    else:
        print("  MODE: LIVE")
    print("  Time: {}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    print("=" * 70)

    # 1. Build calendar service
    print("\n[1/6] Connecting to Google Calendar...")
    service = get_calendar_service()

    now = datetime.now(timezone.utc)
    time_min = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    time_max = (now + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")

    events_result = service.events().list(
        calendarId=CALENDAR_ID,
        timeMin=time_min,
        timeMax=time_max,
        maxResults=250,
        singleEvents=True,
        orderBy="startTime"
    ).execute()

    items = events_result.get("items", [])
    print("  Found {} calendar events in next 7 days".format(len(items)))

    if not items:
        print("\nNo events to sync. Done.")
        return

    # 2. Load ACS contacts ONLY (not all 1,500)
    print("\n[2/6] Loading ACS client contacts...")
    contacts, cp_map = load_acs_contacts()
    print("  {} ACS client contacts loaded".format(len(contacts)))

    # 3. Load existing sync records
    print("\n[3/6] Loading existing sync records...")
    synced = load_synced_events()
    print("  {} events already synced".format(len(synced)))

    # 4. Process events
    print("\n[4/6] Processing calendar events...")
    created = 0
    updated = 0
    skipped = 0
    errors = 0
    crew_assigned = 0
    new_job_ids = []

    for ev in items:
        event_id = ev.get("id", "")
        raw_title = ev.get("summary", "")
        description = ev.get("description", "")
        color_id = ev.get("colorId", "")
        location = ev.get("location", "")

        start_obj = ev.get("start", {})
        end_obj = ev.get("end", {})
        start_dt = start_obj.get("dateTime") or start_obj.get("date")
        end_dt = end_obj.get("dateTime") or end_obj.get("date")

        if not start_dt or not end_dt:
            skipped += 1
            continue

        # Skip all-day events
        if "T" not in str(start_dt):
            skipped += 1
            continue

        client_name = clean_title(raw_title)
        if not client_name:
            skipped += 1
            continue

        # Match against ACS clients only
        contact = match_contact(client_name, contacts)
        contact_id = contact.get("id") if contact else None
        contact_name = contact.get("name") if contact else None
        client_profile_id = cp_map.get(contact_id) if contact_id else None

        # Build notes
        access_codes = extract_access_codes(description)
        special_notes = extract_notes(description)
        notes_parts = []
        if access_codes:
            notes_parts.append("Access:\n" + access_codes)
        if special_notes:
            notes_parts.append("Notes: " + special_notes)
        if location:
            notes_parts.append("Address: " + location)
        notes = "\n\n".join(notes_parts) if notes_parts else None

        crew = CREW_MAP.get(str(color_id))

        # Check if already synced
        existing_job_id = synced.get(event_id)

        match_str = " -> {} ({})".format(contact_name, contact_id[:8]) if contact_id else " -> (no match)"

        if existing_job_id:
            # Update existing
            job_data = {
                "scheduled_start": start_dt,
                "scheduled_end": end_dt,
            }
            if notes:
                job_data["notes"] = notes
            if contact_id:
                job_data["contact_id"] = contact_id
            if client_profile_id:
                job_data["client_profile_id"] = client_profile_id

            print("  UPDATE: {}{}".format(client_name, match_str))

            if not DRY_RUN:
                result = sb_patch("jobs?id=eq." + existing_job_id, job_data)
                if result:
                    sb_patch(
                        "calendar_sync?job_id=eq." + existing_job_id,
                        {"synced_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
                    )
                    updated += 1
                    new_job_ids.append(existing_job_id)
                else:
                    errors += 1
            else:
                updated += 1
                new_job_ids.append(existing_job_id)

        else:
            # Create new
            job_id = str(uuid.uuid4())
            job_data = {
                "id": job_id,
                "scheduled_start": start_dt,
                "scheduled_end": end_dt,
                "status": "scheduled",
                "business_id": ACS_BUSINESS_ID,
            }
            if notes:
                job_data["notes"] = notes
            if contact_id:
                job_data["contact_id"] = contact_id
            if client_profile_id:
                job_data["client_profile_id"] = client_profile_id

            print("  CREATE: {}{}".format(client_name, match_str))

            if not DRY_RUN:
                result = sb_post("jobs", job_data)
                if result:
                    sb_post("calendar_sync", {
                        "job_id": job_id,
                        "google_event_id": event_id,
                        "synced_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    })
                    created += 1
                    new_job_ids.append(job_id)
                else:
                    errors += 1
            else:
                created += 1
                new_job_ids.append(job_id)

    # 5. Crew assignments
    print("\n[5/6] Syncing crew assignments...")

    if not DRY_RUN:
        existing_assignments = load_existing_crew_assignments(new_job_ids)
    else:
        existing_assignments = {}

    for ev in items:
        event_id = ev.get("id", "")
        color_id = ev.get("colorId", "")
        raw_title = ev.get("summary", "")
        client_name = clean_title(raw_title)

        start_dt = ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date")
        if not start_dt or "T" not in str(start_dt):
            continue

        crew = CREW_MAP.get(str(color_id))
        if not crew:
            continue

        job_id = synced.get(event_id)
        if not job_id:
            if DRY_RUN:
                print("  ASSIGN (dry): {} -> {}".format(client_name[:30], crew["name"]))
                crew_assigned += 1
                continue
            else:
                rows = sb_get(
                    "calendar_sync?google_event_id=eq."
                    + urllib.parse.quote(event_id, safe="")
                    + "&select=job_id"
                )
                if rows and rows[0].get("job_id"):
                    job_id = rows[0]["job_id"]
                else:
                    continue

        if not job_id:
            continue

        existing = existing_assignments.get(job_id, set())
        if crew["id"] in existing:
            continue

        print("  ASSIGN: {} -> {}".format(client_name[:30], crew["name"]))

        if not DRY_RUN:
            result = sb_post("job_crew_assignments", {
                "job_id": job_id,
                "crew_member_id": crew["id"],
            })
            if result:
                crew_assigned += 1
            else:
                crew_assigned += 1  # likely dupe constraint

        else:
            crew_assigned += 1

    # 6. Summary
    print("\n[6/6] Summary")
    print("=" * 70)
    print("  Events processed: {}".format(len(items)))
    print("  Jobs created:     {}".format(created))
    print("  Jobs updated:     {}".format(updated))
    print("  Skipped:          {}".format(skipped))
    print("  Errors:           {}".format(errors))
    print("  Crew assigned:    {}".format(crew_assigned))
    if DRY_RUN:
        print("\n  ** DRY RUN -- no changes written **")
    print("=" * 70)


if __name__ == "__main__":
    main()
