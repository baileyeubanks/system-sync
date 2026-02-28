#!/usr/bin/env python3
"""
ACS Calendar Cleanup — Fix all recurring events.
- Rename: client-first titles (no "Astro Cleaning -" prefix)
- Description: real address (from location), phone (from Supabase), access codes (preserved)
- Color: by crew member assignment (default Blueberry/9)

Updates the RECURRING PARENT event so all future occurrences inherit.
"""
import json
import re
import sys
import urllib.request
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ── Config ───────────────────────────────────────────────────────────────
SA_FILE = "/Users/_mxappservice/.gemini/antigravity/playground/perihelion-armstrong/service_account.json"
CALENDAR_ID = "caio@astrocleanings.com"
SUPABASE_URL = "https://briokwdoonawhxisbydy.supabase.co"
def _load_supa_key():
    env_file = __import__("pathlib").Path.home() / ".blaze_env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("SUPABASE_SERVICE_KEY="):
                return line.split("=", 1)[1].strip()
    return __import__("os").environ.get("SUPABASE_SERVICE_KEY", "")

SUPABASE_KEY = _load_supa_key()

# Calendar color IDs by crew
COLOR_MAP = {
    "caio": "9",       # Blueberry (blue)
    "alex": "2",       # Sage (green)
    "yennifer": "4",   # Flamingo (pink)
    "dora": "6",       # Tangerine (orange)
    "default": "9",    # Blueberry
}


def clean_title(raw_title):
    """Strip 'Astro Cleaning - ' prefix and 'Cleaning' suffix, normalize."""
    t = raw_title.strip()

    # Strip "Astro Cleaning - " or variants
    for prefix in ["Astro Cleaning - ", "Astro Cleaning -", "Astro Cle - "]:
        if t.startswith(prefix):
            t = t[len(prefix):].strip()
            break

    # Strip trailing " Cleaning", " Cleanings"
    t = re.sub(r"\s+[Cc]leaning[s]?\.?\s*$", "", t)

    # Strip day suffixes
    t = re.sub(r"\s+(Thurs|Thur|Thu|Wed|Mon|Tue|Tues|Fri|Sat|Sun)\.?\s*$", "", t, flags=re.IGNORECASE)

    # Strip " First" suffix
    t = re.sub(r"\s+First\s*$", "", t)

    # Strip trailing " -" or "- "
    t = re.sub(r"\s*-\s*$", "", t)
    t = re.sub(r"^\s*-\s*", "", t)

    # Fix double spaces
    t = re.sub(r"\s+", " ", t).strip()

    return t


def extract_access_codes(description):
    """Parse access codes from existing description."""
    if not description:
        return ""

    codes = []
    lines = description.split("\n")
    in_access = False

    for line in lines:
        stripped = line.strip()

        # Detect access section start
        if stripped.lower().startswith("access:"):
            in_access = True
            rest = stripped[7:].strip()
            if rest:
                codes.append(rest)
            continue

        # Lines that look like codes
        if re.match(r"^(Gate\s+code|Alarm\s+code|Garage\s+door|Apartment|Apt\s|Cadeado|Big\s+Gate)", stripped, re.IGNORECASE):
            codes.append(stripped)
            in_access = True
            continue

        # Standalone number codes
        if re.match(r"^\d{3,}[#*]?\s*$", stripped):
            codes.append(stripped)
            continue

        # Continue in access section
        if in_access and stripped and not any(stripped.startswith(p) for p in ["Portal:", "Notes:", "Client:", "Phone:", "Address:"]):
            codes.append(stripped)
            continue

        if in_access and (not stripped or any(stripped.startswith(p) for p in ["Portal:", "Notes:"])):
            in_access = False

    # Filter out stray single characters
    codes = [c for c in codes if len(c.strip()) > 1]
    return "\n".join(codes).strip()


def supabase_find_contact(name):
    """Find contact in Supabase by name."""
    encoded = name.replace(" ", "%20").replace("'", "%27")
    url = SUPABASE_URL + "/rest/v1/contacts?select=name,phone,email,street_address,city,metadata&name=ilike.*" + encoded + "*&limit=3"
    req = urllib.request.Request(url, headers={
        "apikey": SUPABASE_KEY,
        "Authorization": "Bearer " + SUPABASE_KEY,
    })
    try:
        rows = json.loads(urllib.request.urlopen(req).read())
        return rows[0] if rows else None
    except Exception:
        return None


def extract_phone_from_desc(description):
    """Extract existing phone from description."""
    if not description:
        return None
    m = re.search(r"Phone:\s*(\+?\d[\d\s()-]{8,})", description)
    if m:
        phone = m.group(1).strip()
        if phone != "n/a":
            return phone
    return None


def build_description(client_name, phone, address, access_codes):
    """Build clean event description."""
    lines = []
    lines.append(client_name)
    if phone:
        lines.append("Phone: " + phone)
    lines.append(address or "(no address)")
    lines.append("")

    if access_codes:
        lines.append("Access:")
        lines.append(access_codes)
        lines.append("")

    lines.append("Portal: https://astrocleanings.com/portal")

    return "\n".join(lines)


def get_calendar_service():
    """Build Google Calendar service with DWD."""
    creds = service_account.Credentials.from_service_account_file(
        SA_FILE, scopes=["https://www.googleapis.com/auth/calendar"]
    )
    creds = creds.with_subject(CALENDAR_ID)
    return build("calendar", "v3", credentials=creds)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "report"

    print("=" * 70)
    print("  ACS CALENDAR CLEANUP")
    print("  Mode: " + mode)
    print("=" * 70)

    service = get_calendar_service()

    # Pull 4 weeks of events to find all recurring series
    events = service.events().list(
        calendarId=CALENDAR_ID,
        timeMin="2026-02-24T00:00:00-06:00",
        timeMax="2026-03-22T00:00:00-06:00",
        maxResults=250,
        singleEvents=True,
        orderBy="startTime"
    ).execute()

    items = events.get("items", [])
    print("\nTotal events (4 weeks): {}".format(len(items)))

    # Group by recurring event ID
    recurring = {}
    for ev in items:
        rid = ev.get("recurringEventId")
        if not rid:
            continue
        if rid not in recurring:
            recurring[rid] = ev

    print("Unique recurring series: {}".format(len(recurring)))

    # Process each recurring series
    updates = []
    for rid, sample_ev in recurring.items():
        old_title = sample_ev.get("summary", "")
        location = sample_ev.get("location", "")
        old_desc = sample_ev.get("description", "")
        old_color = sample_ev.get("colorId", "")

        # Clean the title
        new_title = clean_title(old_title)

        # Manual overrides for tricky titles
        title_lower = old_title.lower()
        if "rubens" in title_lower and "house" in title_lower:
            new_title = "Rubens Franz (House)"
        elif "rubens franz hair studio" in title_lower:
            new_title = "Rubens Franz (Studio)"
        elif "david medina" in title_lower and "thurs" in title_lower:
            new_title = "David Medina (Thu)"
        elif "david medina" in title_lower and "wed" in title_lower:
            new_title = "David Medina (Wed)"
        elif "mr manford" in title_lower:
            new_title = "Mr Manford"
        elif title_lower.startswith("jason") or "jason" in title_lower:
            new_title = "Jason"
        elif "amanda" in title_lower and "cleaning" in title_lower:
            new_title = "Amanda"
        elif "cecilia" in title_lower or "cecília" in title_lower:
            new_title = "Cecilia"

        # Find phone from Supabase
        search_name = new_title.split("(")[0].strip()
        sb_contact = supabase_find_contact(search_name)
        sb_phone = (sb_contact or {}).get("phone") if sb_contact else None

        # Also check existing description for phone
        existing_phone = extract_phone_from_desc(old_desc)
        phone = sb_phone or existing_phone

        # Extract access codes from existing description
        access_codes = extract_access_codes(old_desc)

        # Build new description
        new_desc = build_description(new_title, phone, location, access_codes)

        # Default color (Blueberry/9 = Caio)
        new_color = COLOR_MAP["default"]

        changed = (new_title != old_title) or (new_desc != old_desc) or (str(new_color) != str(old_color or ""))

        print("\n--- {}".format(old_title))
        print("  TITLE: {} -> {}".format(old_title, new_title))
        print("  PHONE: {}".format(phone or "(none)"))
        print("  ADDR:  {}".format(location[:60] if location else "(none)"))
        print("  ACCESS: {}".format(access_codes[:60] if access_codes else "(none)"))
        print("  CHANGED: {}".format("YES" if changed else "no"))

        if changed:
            updates.append({
                "rid": rid,
                "new_title": new_title,
                "new_desc": new_desc,
                "new_color": new_color,
            })

    print("\n" + "=" * 70)
    print("  {} of {} recurring series need updates".format(len(updates), len(recurring)))
    print("=" * 70)

    if mode == "report":
        print("\nDry run. Run with 'execute' to apply.")
        return

    if mode == "execute":
        print("\nUpdating RECURRING PARENT events...")
        ok = 0
        errors = 0

        for u in updates:
            rid = u["rid"]
            try:
                # Fetch the parent event
                parent = service.events().get(
                    calendarId=CALENDAR_ID,
                    eventId=rid
                ).execute()

                patch = {
                    "summary": u["new_title"],
                    "description": u["new_desc"],
                    "colorId": u["new_color"],
                }

                service.events().patch(
                    calendarId=CALENDAR_ID,
                    eventId=rid,
                    body=patch
                ).execute()
                ok += 1
                print("  OK: {}".format(u["new_title"]))

            except Exception as e:
                errors += 1
                print("  ERR [{}]: {}".format(u["new_title"], str(e)[:120]))

        print("\nDone: {} updated, {} errors".format(ok, errors))


if __name__ == "__main__":
    main()
