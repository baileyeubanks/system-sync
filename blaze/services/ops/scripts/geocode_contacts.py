#!/usr/bin/env python3
"""
geocode_contacts.py — Batch geocode ACS contact addresses via Google Places Text Search.

Queries contacts WHERE lat IS NULL AND business_id = ACS_ID.
Uses Google Places Text Search API → patches lat, lng, geom, geocoded_at to Supabase.

Run once on Mac Mini:
  python3 geocode_contacts.py
  python3 geocode_contacts.py --dry-run   # preview only
"""
import sys, os, json, time
from urllib.request import Request, urlopen
from urllib.parse import quote

ACS_BUSINESS_ID = "0ade82e3-ffe9-4c17-ae59-fc4bd198482b"
def _load_geocode_key():
    env_file = Path.home() / ".blaze_env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("GOOGLE_GEOCODE_API_KEY="):
                return line.split("=", 1)[1].strip()
    import os
    return os.environ.get("GOOGLE_GEOCODE_API_KEY", "")

GOOGLE_API_KEY = _load_geocode_key()
ENV_FILE = "/Users/_mxappservice/.blaze/env_cache"

DRY_RUN = "--dry-run" in sys.argv


def _load_env():
    env = {}
    try:
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    except Exception:
        pass
    return env


def supabase_request(method, path, body=None, env=None):
    if env is None:
        env = _load_env()
    supa_url = env.get("SUPABASE_URL", "")
    supa_key = env.get("SUPABASE_SERVICE_KEY", "")
    if not supa_url or not supa_key:
        raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_KEY required in env_cache")

    url = "%s/rest/v1/%s" % (supa_url, path)
    data = json.dumps(body).encode("utf-8") if body else None
    headers = {
        "apikey": supa_key,
        "Authorization": "Bearer %s" % supa_key,
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    req = Request(url, data=data, headers=headers, method=method)
    resp = urlopen(req, timeout=15)
    raw = resp.read()
    return json.loads(raw) if raw else {}


def geocode_address(address):
    """Call Google Places Text Search to get lat/lng for an address."""
    encoded = quote(address)
    url = ("https://maps.googleapis.com/maps/api/place/textsearch/json"
           "?query=%s&key=%s" % (encoded, GOOGLE_API_KEY))
    req = Request(url, headers={"User-Agent": "Blaze/4.0"})
    resp = urlopen(req, timeout=10)
    data = json.loads(resp.read().decode("utf-8"))
    results = data.get("results", [])
    if not results:
        return None, None
    loc = results[0]["geometry"]["location"]
    return loc["lat"], loc["lng"]


def main():
    env = _load_env()
    print("Fetching ACS contacts without geocodes...")

    contacts = supabase_request(
        "GET",
        "contacts?select=id,name,address&business_id=eq.%s&lat=is.null&address=not.is.null&limit=100" % ACS_BUSINESS_ID,
        env=env
    )

    if not contacts:
        print("No contacts to geocode.")
        return

    print("Found %d contacts to geocode." % len(contacts))

    success = 0
    failed = 0
    for c in contacts:
        contact_id = c["id"]
        name = c.get("name", "?")
        address = c.get("address", "").strip()
        if not address:
            print("  SKIP %s — no address" % name)
            failed += 1
            continue

        # Append city/state if not present
        search_addr = address
        if "houston" not in address.lower() and "tx" not in address.lower():
            search_addr = "%s, Houston TX" % address

        try:
            lat, lng = geocode_address(search_addr)
            if lat is None:
                print("  FAIL %s — no results for: %s" % (name, search_addr))
                failed += 1
                continue

            print("  OK   %s — %.4f, %.4f" % (name, lat, lng))
            if not DRY_RUN:
                supabase_request("PATCH", "contacts?id=eq.%s" % contact_id, {
                    "lat": lat,
                    "lng": lng,
                    "geocoded_at": "now()",
                }, env=env)
            success += 1
            time.sleep(0.2)  # stay under Google Places rate limit

        except Exception as e:
            print("  ERR  %s — %s" % (name, e))
            failed += 1
            time.sleep(0.5)

    print("\nDone: %d geocoded, %d failed%s" % (
        success, failed, " (DRY RUN — no writes)" if DRY_RUN else ""))


if __name__ == "__main__":
    main()
