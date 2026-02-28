"""
Property Data Router — HCAD property enrichment endpoints.

GET  /api/property/{contact_id}  — property data for a contact
POST /api/property/lookup        — lookup by raw address
POST /api/property/enrich        — trigger enrichment for a contact_id
GET  /api/property/stats         — tier distribution summary
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/api/property", tags=["property"])

# ── Supabase config ──
ENV_CACHE = Path("/Users/_mxappservice/.blaze/env_cache")
BLAZE_ENV = Path("/Users/_mxappservice/.blaze_env")
SCRIPTS_DIR = Path("/Users/_mxappservice/ACS_CC_AUTOBOT/blaze-v4/ops/scripts")
PYTHON = "/opt/homebrew/bin/python3"

HCAD_URL = "https://www.gis.hctx.net/arcgis/rest/services/HCAD/Parcels/MapServer/0/query"
GOOGLE_PLACES_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"

# Value tier thresholds (must match enricher)
TIERS = [
    (1500000, "elite",   1.60),
    (800000,  "ultra",   1.40),
    (500000,  "luxury",  1.25),
    (250000,  "premium", 1.12),
    (0,       "standard", 1.00),
]


def _load_env():
    env = {}
    for f in [ENV_CACHE, BLAZE_ENV]:
        if f.exists():
            for line in f.read_text().splitlines():
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    return env


def _supa_headers(env):
    return {
        "apikey": env["SUPABASE_SERVICE_KEY"],
        "Authorization": "Bearer %s" % env["SUPABASE_SERVICE_KEY"],
        "Content-Type": "application/json",
    }


def _supa_get(path, env=None):
    import requests
    if env is None:
        env = _load_env()
    url = "%s/rest/v1/%s" % (env["SUPABASE_URL"], path)
    resp = requests.get(url, headers=_supa_headers(env), timeout=15)
    if resp.status_code >= 400:
        return None
    return resp.json()


# ── Models ──
class AddressLookup(BaseModel):
    address: str

class ContactEnrich(BaseModel):
    contact_id: str


# ── Endpoints ──
@router.get("/{contact_id}")
def get_property(contact_id: str):
    """Get property data for a contact."""
    env = _load_env()
    records = _supa_get(
        "property_data?contact_id=eq.%s" % contact_id,
        env=env,
    )
    if not records:
        raise HTTPException(status_code=404, detail="No property data for contact %s" % contact_id)
    return records[0]


@router.post("/lookup")
def lookup_address(body: AddressLookup):
    """Look up property data for a raw address (geocode + HCAD in one call)."""
    import requests
    env = _load_env()
    api_key = env.get("GOOGLE_PLACES_API_KEY") or env.get("GOOGLE_GEOCODE_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=500, detail="Google API key not configured")

    # Geocode via Google Places
    resp = requests.get(GOOGLE_PLACES_URL, params={
        "query": body.address,
        "key": api_key,
    }, timeout=10)
    data = resp.json()
    results = data.get("results", [])
    if not results:
        raise HTTPException(status_code=404, detail="Could not geocode address")

    loc = results[0]["geometry"]["location"]
    lat, lng = loc["lat"], loc["lng"]

    # Query HCAD
    hcad_resp = requests.get(HCAD_URL, params={
        "where": "1=1",
        "geometry": json.dumps({"x": lng, "y": lat}),
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "f": "json",
    }, timeout=15)
    hcad_data = hcad_resp.json()
    features = hcad_data.get("features", [])
    if not features:
        raise HTTPException(status_code=404, detail="No HCAD parcel at (%.6f, %.6f)" % (lat, lng))

    attrs = features[0]["attributes"]

    # Build address from parts
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
    county_addr = " ".join(parts)
    city = (attrs.get("site_city") or "").strip()
    if city:
        county_addr += ", %s" % city

    appraised = attrs.get("total_appraised_val")
    tier = "standard"
    multiplier = 1.0
    if appraised:
        for threshold, t, m in TIERS:
            if appraised >= threshold:
                tier = t
                multiplier = m
                break

    return {
        "coordinates": {"lat": lat, "lng": lng},
        "hcad_account": attrs.get("HCAD_NUM") or attrs.get("acct_num"),
        "county_address": county_addr,
        "owner_name": (attrs.get("owner_name_1") or "").strip(),
        "state_class": (attrs.get("state_class") or "").strip(),
        "land_sqft": attrs.get("land_sqft"),
        "acreage": float(attrs.get("StatedArea") or 0),
        "appraised_value": appraised,
        "market_value": attrs.get("total_market_val"),
        "land_value": attrs.get("land_value"),
        "building_value": attrs.get("bld_value"),
        "tax_value": attrs.get("tax_value"),
        "neighborhood_code": str(attrs.get("nh_cd") or ""),
        "neighborhood_desc": (attrs.get("dscr") or "").strip(),
        "value_tier": tier,
        "price_multiplier": multiplier,
    }


@router.post("/enrich")
def enrich_contact(body: ContactEnrich):
    """Trigger HCAD enrichment for a specific contact."""
    result = subprocess.run(
        [PYTHON, str(SCRIPTS_DIR / "hcad_property_enricher.py"),
         "--contact-id", body.contact_id],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=result.stderr or result.stdout)
    return {"ok": True, "output": result.stdout}


@router.get("/stats/summary")
def property_stats():
    """Get tier distribution summary."""
    env = _load_env()
    records = _supa_get(
        "property_data?select=value_tier,price_multiplier,appraised_value",
        env=env,
    )
    if not records:
        return {"total": 0, "tiers": {}, "total_appraised": 0, "avg_appraised": 0}

    tiers = {}
    total_val = 0
    for r in records:
        t = r.get("value_tier", "unknown")
        tiers[t] = tiers.get(t, 0) + 1
        total_val += (r.get("appraised_value") or 0)

    return {
        "total": len(records),
        "tiers": tiers,
        "total_appraised": total_val,
        "avg_appraised": total_val / len(records) if records else 0,
    }
