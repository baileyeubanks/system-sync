"""Brand identity API — serves brand specs and asset listings."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/brand", tags=["brand"])

BRAND_BASE = Path("/Users/_mxappservice/blaze-data/brand")


def _load_json(path: Path) -> dict:
    if not path.exists():
        raise HTTPException(status_code=404, detail="Not found: %s" % path.name)
    with open(path) as f:
        return json.load(f)


@router.get("/{bu}")
def get_brand(bu: str):
    """Return full brand.json for a business unit (acs or cc)."""
    bu_lower = bu.lower()
    if bu_lower not in ("acs", "cc"):
        raise HTTPException(status_code=400, detail="Invalid business unit. Use 'acs' or 'cc'.")
    return _load_json(BRAND_BASE / bu_lower / "brand.json")


@router.get("/{bu}/assets")
def list_assets(bu: str, category: Optional[str] = Query(None, description="Filter by category: logo, photo, icon, template")):
    """List available brand assets for a business unit."""
    bu_lower = bu.lower()
    if bu_lower not in ("acs", "cc"):
        raise HTTPException(status_code=400, detail="Invalid business unit. Use 'acs' or 'cc'.")

    bu_dir = BRAND_BASE / bu_lower
    if not bu_dir.exists():
        raise HTTPException(status_code=404, detail="Brand directory not found for %s" % bu)

    # Map directory names to categories
    cat_dirs = {
        "logo": "logos",
        "photo": "photos",
        "icon": "icons",
        "template": "templates",
    }

    results = []

    if category:
        dir_name = cat_dirs.get(category.lower())
        if not dir_name:
            raise HTTPException(status_code=400, detail="Invalid category. Use: logo, photo, icon, template")
        scan_dirs = [(category.lower(), bu_dir / dir_name)]
    else:
        scan_dirs = [(cat, bu_dir / dname) for cat, dname in cat_dirs.items()]

    for cat, dirpath in scan_dirs:
        if not dirpath.exists():
            continue
        for f in sorted(dirpath.iterdir()):
            if f.is_file() and not f.name.startswith("."):
                results.append({
                    "category": cat,
                    "filename": f.name,
                    "path": str(f),
                    "size_bytes": f.stat().st_size,
                    "extension": f.suffix.lower(),
                })

    return {"business_unit": bu_lower, "asset_count": len(results), "assets": results}


@router.get("/{bu}/assets/search")
def search_assets(
    bu: str,
    q: str = Query(..., description="Search term (matches filename)"),
    category: Optional[str] = Query(None),
):
    """Search brand assets by filename keyword."""
    bu_lower = bu.lower()
    if bu_lower not in ("acs", "cc"):
        raise HTTPException(status_code=400, detail="Invalid business unit. Use 'acs' or 'cc'.")

    bu_dir = BRAND_BASE / bu_lower
    if not bu_dir.exists():
        raise HTTPException(status_code=404, detail="Brand directory not found for %s" % bu)

    q_lower = q.lower()
    results = []

    cat_dirs = {"logo": "logos", "photo": "photos", "icon": "icons", "template": "templates"}
    if category:
        dir_name = cat_dirs.get(category.lower())
        scan_dirs = [(category.lower(), bu_dir / dir_name)] if dir_name else []
    else:
        scan_dirs = [(cat, bu_dir / dname) for cat, dname in cat_dirs.items()]

    for cat, dirpath in scan_dirs:
        if not dirpath.exists():
            continue
        for f in sorted(dirpath.iterdir()):
            if f.is_file() and not f.name.startswith(".") and q_lower in f.name.lower():
                results.append({
                    "category": cat,
                    "filename": f.name,
                    "path": str(f),
                    "size_bytes": f.stat().st_size,
                    "extension": f.suffix.lower(),
                })

    return {"business_unit": bu_lower, "query": q, "result_count": len(results), "results": results}


@router.get("/shared/info")
def get_shared_info():
    """Return shared company info (people, address, conventions)."""
    return _load_json(BRAND_BASE / "shared" / "company-info.json")
