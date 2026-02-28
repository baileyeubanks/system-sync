#!/usr/bin/env python3
"""
hcad_property_enricher.py — HCAD Property Data Enrichment for ACS Contacts

Queries Harris County Appraisal District (HCAD) GIS REST API using contact lat/lng,
pulls property records (sq ft, appraised value, owner, etc.), calculates pricing
tier + multiplier, and upserts to Supabase property_data table.

Usage:
    python3 hcad_property_enricher.py                     # enrich all un-enriched contacts
    python3 hcad_property_enricher.py --address "1234 Main St, Houston TX"
    python3 hcad_property_enricher.py --contact-id UUID   # enrich specific contact
    python3 hcad_property_enricher.py --refresh            # re-enrich all
    python3 hcad_property_enricher.py --stats              # show tier distribution
    python3 hcad_property_enricher.py --dry-run            # preview without writing
    python3 hcad_property_enricher.py --setup              # create property_data table (needs DB password)

Requires: requests (pip3 install requests)
Optional: psycopg2-binary (for --setup only)
"""
import sys
import os
import json
import time
import argparse
from datetime import datetime
from pathlib import Path

# ── Config ──────────────────────────────────────────────────
HCAD_URL = "https://www.gis.hctx.net/arcgis/rest/services/HCAD/Parcels/MapServer/0/query"

# Google Places Text Search (for --address mode)
GOOGLE_PLACES_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"

# Supabase
ENV_CACHE = Path.home() / ".blaze" / "env_cache"
BLAZE_ENV = Path.home() / ".blaze_env"

# Value tier thresholds
TIERS = [
    (1500000, "elite",   1.60),
    (800000,  "ultra",   1.40),
    (500000,  "luxury",  1.25),
    (250000,  "premium", 1.12),
    (0,       "standard", 1.00),
]

# State class descriptions
STATE_CLASS_MAP = {
    "A1": "Real property - residential single-family",
    "A2": "Real property - residential multi-family",
    "B1": "Real property - commercial",
    "B2": "Real property - industrial",
    "C1": "Vacant lots - residential",
    "D1": "Qualified agricultural land",
    "E1": "Farm and ranch improvements",
    "F1": "Commercial real property",
    "F2": "Industrial real property",
}


# ── Helpers ─────────────────────────────────────────────────
def _load_env():
    """Load environment from env_cache and .blaze_env."""
    env = {}
    for f in [ENV_CACHE, BLAZE_ENV]:
        if f.exists():
            for line in f.read_text().splitlines():
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    return env


def supabase_request(method, path, body=None, env=None, headers_extra=None):
    """Make a Supabase REST API request."""
    import requests
    if env is None:
        env = _load_env()
    url = "%s/rest/v1/%s" % (env["SUPABASE_URL"], path)
    headers = {
        "apikey": env["SUPABASE_SERVICE_KEY"],
        "Authorization": "Bearer %s" % env["SUPABASE_SERVICE_KEY"],
        "Content-Type": "application/json",
    }
    if headers_extra:
        headers.update(headers_extra)

    if method == "GET":
        resp = requests.get(url, headers=headers, timeout=15)
    elif method == "POST":
        resp = requests.post(url, headers=headers, json=body, timeout=15)
    elif method == "PATCH":
        resp = requests.patch(url, headers=headers, json=body, timeout=15)
    elif method == "DELETE":
        resp = requests.delete(url, headers=headers, timeout=15)
    else:
        raise ValueError("Unsupported method: %s" % method)

    if resp.status_code >= 400:
        raise Exception("Supabase %s %s → %d: %s" % (method, path, resp.status_code, resp.text))

    if resp.status_code == 204 or not resp.text:
        return None
    return resp.json()


def query_hcad(lat, lng):
    """Query HCAD GIS API with WGS84 lat/lng. Returns feature attributes or None."""
    import requests
    params = {
        "where": "1=1",
        "geometry": json.dumps({"x": lng, "y": lat}),
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "f": "json",
    }
    try:
        resp = requests.get(HCAD_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        features = data.get("features", [])
        if not features:
            return None
        return features[0].get("attributes", {})
    except Exception as e:
        print("  HCAD query failed for (%.6f, %.6f): %s" % (lat, lng, e))
        return None


def geocode_address(address, api_key):
    """Geocode an address via Google Places Text Search API. Returns (lat, lng) or None."""
    import requests
    try:
        resp = requests.get(GOOGLE_PLACES_URL, params={
            "query": address,
            "key": api_key,
        }, timeout=10)
        data = resp.json()
        results = data.get("results", [])
        if results:
            loc = results[0]["geometry"]["location"]
            return (loc["lat"], loc["lng"])
    except Exception as e:
        print("Geocode failed for %r: %s" % (address, e))
    return None


def classify_tier(appraised_value):
    """Return (tier_name, multiplier) based on appraised value."""
    if appraised_value is None:
        return ("standard", 1.0)
    for threshold, tier, multiplier in TIERS:
        if appraised_value >= threshold:
            return (tier, multiplier)
    return ("standard", 1.0)


def parse_hcad_to_record(attrs, contact_id=None):
    """Parse raw HCAD attributes into a property_data record dict."""
    # Build county address from parts
    parts = []
    num = attrs.get("site_str_num")
    if num:
        parts.append(str(int(num)) if isinstance(num, float) else str(num))
    name = (attrs.get("site_str_name") or "").strip()
    if name:
        parts.append(name)
    sfx = (attrs.get("site_str_sfx") or "").strip()
    if sfx:
        parts.append(sfx)
    city = (attrs.get("site_city") or "").strip()
    zip_code = (attrs.get("site_zip") or "").strip()
    county_addr = " ".join(parts)
    if city:
        county_addr += ", %s" % city
    if zip_code:
        county_addr += " %s" % zip_code

    appraised = attrs.get("total_appraised_val")
    tier, multiplier = classify_tier(appraised)

    state_class = (attrs.get("state_class") or "").strip()
    prop_type = STATE_CLASS_MAP.get(state_class, "unknown")

    record = {
        "hcad_account": attrs.get("HCAD_NUM") or attrs.get("acct_num"),
        "county_address": county_addr,
        "land_sqft": attrs.get("land_sqft"),
        "acreage": float(attrs.get("StatedArea") or 0) or attrs.get("acreage_1"),
        "property_type": prop_type,
        "state_class": state_class,
        "land_use": (attrs.get("land_use") or "").strip(),
        "neighborhood_code": str(attrs.get("nh_cd") or ""),
        "neighborhood_desc": (attrs.get("dscr") or "").strip(),
        "appraised_value": appraised,
        "market_value": attrs.get("total_market_val"),
        "land_value": attrs.get("land_value"),
        "building_value": attrs.get("bld_value"),
        "tax_value": attrs.get("tax_value"),
        "owner_name": (attrs.get("owner_name_1") or "").strip(),
        "value_tier": tier,
        "price_multiplier": multiplier,
        "fetched_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "updated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if contact_id:
        record["contact_id"] = contact_id
    return record


def upsert_property(record, env=None):
    """Upsert a property_data record to Supabase (on conflict contact_id)."""
    return supabase_request(
        "POST",
        "property_data",
        body=record,
        env=env,
        headers_extra={
            "Prefer": "resolution=merge-duplicates",
        },
    )


# ── CLI Commands ────────────────────────────────────────────
def cmd_enrich_all(refresh=False, dry_run=False):
    """Enrich all contacts that have lat/lng but no property_data record."""
    env = _load_env()

    # Get all contacts with lat/lng
    contacts = supabase_request(
        "GET",
        "contacts?select=id,name,lat,lng,street_address&lat=not.is.null&order=name",
        env=env,
    )
    if not contacts:
        print("No geocoded contacts found.")
        return

    print("Found %d geocoded contacts." % len(contacts))

    if not refresh:
        # Filter out already-enriched contacts
        existing = supabase_request(
            "GET",
            "property_data?select=contact_id",
            env=env,
        )
        existing_ids = set(r["contact_id"] for r in (existing or []))
        contacts = [c for c in contacts if c["id"] not in existing_ids]
        print("Skipping %d already enriched. %d to enrich." % (len(existing_ids), len(contacts)))

    if not contacts:
        print("All contacts already enriched!")
        return

    success = 0
    no_data = 0
    errors = 0

    for i, contact in enumerate(contacts):
        name = contact.get("name", "Unknown")
        lat = contact["lat"]
        lng = contact["lng"]
        addr = contact.get("street_address", "")
        print("\n[%d/%d] %s (%.4f, %.4f) %s" % (i + 1, len(contacts), name, lat, lng, addr or ""))

        attrs = query_hcad(lat, lng)
        if not attrs:
            print("  No HCAD parcel found.")
            no_data += 1
            continue

        record = parse_hcad_to_record(attrs, contact_id=contact["id"])
        appraised = record.get("appraised_value")
        tier = record.get("value_tier")
        mult = record.get("price_multiplier")
        owner = record.get("owner_name")
        county_addr = record.get("county_address")

        print("  County: %s" % county_addr)
        print("  Owner: %s" % owner)
        print("  Appraised: $%s → %s (x%.2f)" % (
            "{:,.0f}".format(appraised) if appraised else "N/A",
            tier, mult,
        ))
        print("  Land: %s sqft" % ("{:,.0f}".format(record["land_sqft"]) if record["land_sqft"] else "N/A"))

        if dry_run:
            print("  [DRY RUN] Would upsert.")
        else:
            try:
                upsert_property(record, env=env)
                print("  Saved.")
                success += 1
            except Exception as e:
                print("  ERROR saving: %s" % e)
                errors += 1

        # Rate limit: ~0.3s per HCAD call, be nice
        time.sleep(0.3)

    print("\n── Summary ──")
    print("Enriched: %d | No HCAD data: %d | Errors: %d" % (success, no_data, errors))


def cmd_address_lookup(address):
    """Look up a single address — geocode + HCAD."""
    env = _load_env()
    api_key = env.get("GOOGLE_PLACES_API_KEY") or env.get("GOOGLE_GEOCODE_API_KEY", "")
    if not api_key:
        print("ERROR: GOOGLE_PLACES_API_KEY or GOOGLE_GEOCODE_API_KEY not found in env.")
        sys.exit(1)

    print("Geocoding: %s" % address)
    coords = geocode_address(address, api_key)
    if not coords:
        print("Could not geocode address.")
        sys.exit(1)

    lat, lng = coords
    print("Coordinates: %.6f, %.6f" % (lat, lng))
    print()

    attrs = query_hcad(lat, lng)
    if not attrs:
        print("No HCAD parcel found at these coordinates.")
        sys.exit(1)

    record = parse_hcad_to_record(attrs)

    print("── HCAD Property Record ──")
    print("County Address:    %s" % record["county_address"])
    print("HCAD Account:      %s" % record["hcad_account"])
    print("Owner:             %s" % record["owner_name"])
    print("State Class:       %s (%s)" % (record["state_class"], record["property_type"]))
    print("Land Use:          %s" % record["land_use"])
    print("Neighborhood:      %s (%s)" % (record["neighborhood_desc"], record["neighborhood_code"]))
    print()
    print("Land Sqft:         %s" % ("{:,.0f}".format(record["land_sqft"]) if record["land_sqft"] else "N/A"))
    print("Acreage:           %s" % record["acreage"])
    print()
    print("Appraised Value:   $%s" % ("{:,.0f}".format(record["appraised_value"]) if record["appraised_value"] else "N/A"))
    print("Market Value:      $%s" % ("{:,.0f}".format(record["market_value"]) if record["market_value"] else "N/A"))
    print("Land Value:        $%s" % ("{:,.0f}".format(record["land_value"]) if record["land_value"] else "N/A"))
    print("Building Value:    $%s" % ("{:,.0f}".format(record["building_value"]) if record["building_value"] else "N/A"))
    print("Tax Value:         $%s" % ("{:,.0f}".format(record["tax_value"]) if record["tax_value"] else "N/A"))
    print()
    print("── Pricing Intelligence ──")
    print("Value Tier:        %s" % record["value_tier"].upper())
    print("Price Multiplier:  x%.2f" % record["price_multiplier"])

    return record


def cmd_contact_enrich(contact_id, dry_run=False):
    """Enrich a specific contact by ID."""
    env = _load_env()

    contacts = supabase_request(
        "GET",
        "contacts?select=id,name,lat,lng,street_address&id=eq.%s" % contact_id,
        env=env,
    )
    if not contacts:
        print("Contact not found: %s" % contact_id)
        sys.exit(1)

    c = contacts[0]
    if not c.get("lat") or not c.get("lng"):
        print("Contact %s has no lat/lng. Geocode first." % c.get("name", contact_id))
        sys.exit(1)

    print("Enriching: %s (%.4f, %.4f)" % (c["name"], c["lat"], c["lng"]))

    attrs = query_hcad(c["lat"], c["lng"])
    if not attrs:
        print("No HCAD parcel found.")
        sys.exit(1)

    record = parse_hcad_to_record(attrs, contact_id=c["id"])

    print("County: %s" % record["county_address"])
    print("Owner: %s" % record["owner_name"])
    print("Appraised: $%s → %s (x%.2f)" % (
        "{:,.0f}".format(record["appraised_value"]) if record["appraised_value"] else "N/A",
        record["value_tier"], record["price_multiplier"],
    ))

    if dry_run:
        print("[DRY RUN] Would upsert.")
    else:
        upsert_property(record, env=env)
        print("Saved to property_data.")


def cmd_stats():
    """Show tier distribution from property_data."""
    env = _load_env()

    records = supabase_request(
        "GET",
        "property_data?select=value_tier,price_multiplier,appraised_value,contact_id",
        env=env,
    )
    if not records:
        print("No property data found. Run enrichment first.")
        return

    # Tier distribution
    tiers = {}
    total_val = 0
    for r in records:
        t = r.get("value_tier", "unknown")
        tiers[t] = tiers.get(t, 0) + 1
        total_val += (r.get("appraised_value") or 0)

    print("── Property Data Stats ──")
    print("Total enriched:   %d contacts" % len(records))
    print("Total appraised:  $%s" % "{:,.0f}".format(total_val))
    print("Avg appraised:    $%s" % "{:,.0f}".format(total_val / len(records) if records else 0))
    print()
    print("── Tier Distribution ──")

    tier_order = ["standard", "premium", "luxury", "ultra", "elite"]
    for t in tier_order:
        count = tiers.get(t, 0)
        pct = (count / len(records) * 100) if records else 0
        bar = "#" * int(pct / 2)
        mult = dict((name, m) for _, name, m in TIERS).get(t, 1.0)
        print("  %-10s %3d (%5.1f%%) x%.2f  %s" % (t, count, pct, mult, bar))


def cmd_setup(db_password):
    """Create the property_data table in Supabase via direct postgres connection."""
    try:
        import psycopg2
    except ImportError:
        print("ERROR: psycopg2 not installed. Run: pip3 install psycopg2-binary")
        sys.exit(1)

    PROJECT_REF = "briokwdoonawhxisbydy"
    DB_HOST = "aws-0-us-east-1.pooler.supabase.com"
    DB_PORT = 6543
    DB_NAME = "postgres"
    DB_USER = "postgres." + PROJECT_REF

    print("Connecting to Supabase PostgreSQL...")
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=db_password,
        sslmode="require",
    )
    conn.autocommit = True
    cur = conn.cursor()

    ddl = """
    CREATE TABLE IF NOT EXISTS property_data (
      id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
      contact_id UUID REFERENCES contacts(id) ON DELETE CASCADE,
      hcad_account TEXT,
      county_address TEXT,
      building_sqft NUMERIC,
      land_sqft NUMERIC,
      acreage NUMERIC,
      property_type TEXT,
      state_class TEXT,
      land_use TEXT,
      neighborhood_code TEXT,
      neighborhood_desc TEXT,
      appraised_value NUMERIC,
      market_value NUMERIC,
      land_value NUMERIC,
      building_value NUMERIC,
      tax_value NUMERIC,
      owner_name TEXT,
      value_tier TEXT,
      price_multiplier NUMERIC DEFAULT 1.0,
      fetched_at TIMESTAMPTZ DEFAULT NOW(),
      created_at TIMESTAMPTZ DEFAULT NOW(),
      updated_at TIMESTAMPTZ DEFAULT NOW(),
      UNIQUE(contact_id)
    );

    CREATE INDEX IF NOT EXISTS idx_property_contact ON property_data(contact_id);
    CREATE INDEX IF NOT EXISTS idx_property_tier ON property_data(value_tier);
    CREATE INDEX IF NOT EXISTS idx_property_appraised ON property_data(appraised_value);
    CREATE INDEX IF NOT EXISTS idx_property_hcad ON property_data(hcad_account);

    ALTER TABLE property_data ENABLE ROW LEVEL SECURITY;

    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'property_data' AND policyname = 'Service role full access'
      ) THEN
        CREATE POLICY "Service role full access" ON property_data
          FOR ALL USING (auth.role() = 'service_role')
          WITH CHECK (auth.role() = 'service_role');
      END IF;
    END
    $$;
    """

    print("Creating property_data table...")
    cur.execute(ddl)
    print("Table created successfully.")

    # Verify
    cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = 'property_data'")
    count = cur.fetchone()[0]
    print("Verified: property_data table exists (%d)" % count)

    cur.close()
    conn.close()
    print("Done.")


# ── Main ────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="HCAD Property Enrichment for ACS Contacts")
    parser.add_argument("--address", type=str, help="Look up a single address")
    parser.add_argument("--contact-id", type=str, help="Enrich a specific contact by UUID")
    parser.add_argument("--refresh", action="store_true", help="Re-enrich all contacts (overwrite existing)")
    parser.add_argument("--stats", action="store_true", help="Show tier distribution stats")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing to Supabase")
    parser.add_argument("--setup", type=str, metavar="DB_PASSWORD", help="Create property_data table (pass DB password)")

    args = parser.parse_args()

    if args.setup:
        cmd_setup(args.setup)
    elif args.stats:
        cmd_stats()
    elif args.address:
        cmd_address_lookup(args.address)
    elif args.contact_id:
        cmd_contact_enrich(args.contact_id, dry_run=args.dry_run)
    else:
        cmd_enrich_all(refresh=args.refresh, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
