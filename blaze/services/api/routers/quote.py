"""Quote generation and management API."""
from __future__ import annotations

import base64
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/quote", tags=["quote"])

BRAND_BASE = Path("/Users/_mxappservice/blaze-data/brand")
QUOTES_DIR = Path("/Users/_mxappservice/blaze-data/quotes")
QUOTES_DB = QUOTES_DIR / "quotes.db"
RENDERER = BRAND_BASE / "shared" / "quote-engine" / "renderer.py"
PYTHON = "/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3"


# ── Models ──

class LineItem(BaseModel):
    name: str
    description: Optional[str] = ""
    quantity: int = 1
    price: float = 0.0


class QuoteRequest(BaseModel):
    business_unit: str  # ACS or CC
    title: Optional[str] = "Quote"
    client_name: str
    client_email: Optional[str] = ""
    client_phone: Optional[str] = ""
    client_address: Optional[str] = ""
    line_items: List[LineItem]
    tax: float = 0.0
    discount: float = 0.0
    notes: Optional[str] = ""
    legal_terms: Optional[str] = ""
    status: Optional[str] = "draft"


# ── DB Setup ──

def _ensure_db():
    QUOTES_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(QUOTES_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quote_number TEXT UNIQUE NOT NULL,
            business_unit TEXT NOT NULL,
            client_name TEXT NOT NULL,
            client_email TEXT,
            client_phone TEXT,
            client_address TEXT,
            title TEXT,
            status TEXT DEFAULT 'draft',
            line_items_json TEXT,
            subtotal REAL,
            tax REAL DEFAULT 0,
            discount REAL DEFAULT 0,
            total REAL,
            notes TEXT,
            legal_terms TEXT,
            pdf_path TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()


def _next_quote_number(bu):
    """Generate next sequential quote number: PREFIX-YEAR-XXXX."""
    bu_upper = bu.upper()
    year = datetime.now().year
    prefix = bu_upper

    conn = sqlite3.connect(str(QUOTES_DB))
    row = conn.execute(
        "SELECT quote_number FROM quotes WHERE business_unit = ? ORDER BY id DESC LIMIT 1",
        (bu_upper,)
    ).fetchone()
    conn.close()

    if row:
        # Parse last sequence number
        parts = row[0].split("-")
        if len(parts) == 3 and parts[1] == str(year):
            seq = int(parts[2]) + 1
        else:
            seq = 1
    else:
        seq = 1

    return "%s-%d-%04d" % (prefix, year, seq)


# ── Endpoints ──

@router.post("/generate")
def generate_quote(req: QuoteRequest):
    """Generate a quote PDF and store in database."""
    _ensure_db()

    bu = req.business_unit.upper()
    if bu not in ("ACS", "CC"):
        raise HTTPException(status_code=400, detail="Invalid business_unit. Use 'ACS' or 'CC'.")

    # Generate quote number
    quote_number = _next_quote_number(bu)

    # Build line items with totals
    items = []
    for li in req.line_items:
        total = li.quantity * li.price
        items.append({
            "name": li.name,
            "description": li.description or "",
            "quantity": li.quantity,
            "price": li.price,
            "total": total,
        })

    subtotal = sum(i["total"] for i in items)
    total = subtotal + req.tax - req.discount

    # Build quote data for renderer
    quote_data = {
        "title": req.title or "Quote",
        "quote_number": quote_number,
        "status": req.status or "draft",
        "client_name": req.client_name,
        "client_email": req.client_email or "",
        "client_phone": req.client_phone or "",
        "client_address": req.client_address or "",
        "line_items": items,
        "tax": req.tax,
        "discount": req.discount,
        "notes": req.notes or "",
        "legal_terms": req.legal_terms or "",
    }

    # Save JSON for renderer
    bu_quotes_dir = QUOTES_DIR / bu.lower()
    bu_quotes_dir.mkdir(parents=True, exist_ok=True)
    json_path = bu_quotes_dir / ("%s.json" % quote_number)
    pdf_path = bu_quotes_dir / ("%s.pdf" % quote_number)

    with open(json_path, "w") as f:
        json.dump(quote_data, f, indent=2)

    # Call renderer
    env = os.environ.copy()
    env["DYLD_LIBRARY_PATH"] = "/opt/homebrew/lib"
    result = subprocess.run(
        [PYTHON, str(RENDERER), "--bu", bu, "--json", str(json_path), "--output", str(pdf_path)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    if result.returncode != 0:
        # Try HTML fallback
        html_path = str(pdf_path).replace(".pdf", ".html")
        result2 = subprocess.run(
            [PYTHON, str(RENDERER), "--bu", bu, "--json", str(json_path), "--output", html_path, "--html-only"],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        if result2.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail="Renderer failed: %s" % (result.stderr or result2.stderr)[:500],
            )
        final_path = html_path
    else:
        final_path = str(pdf_path)

    # Store in DB
    conn = sqlite3.connect(str(QUOTES_DB))
    conn.execute("""
        INSERT INTO quotes (quote_number, business_unit, client_name, client_email,
            client_phone, client_address, title, status, line_items_json,
            subtotal, tax, discount, total, notes, legal_terms, pdf_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        quote_number, bu, req.client_name, req.client_email or "",
        req.client_phone or "", req.client_address or "",
        req.title or "Quote", req.status or "draft",
        json.dumps(items), subtotal, req.tax, req.discount, total,
        req.notes or "", req.legal_terms or "", final_path,
    ))
    conn.commit()
    conn.close()

    return {
        "ok": True,
        "quote_number": quote_number,
        "business_unit": bu,
        "client_name": req.client_name,
        "subtotal": subtotal,
        "tax": req.tax,
        "discount": req.discount,
        "total": total,
        "status": req.status or "draft",
        "pdf_path": final_path,
        "line_item_count": len(items),
    }


@router.get("/list")
def list_quotes(bu: Optional[str] = None, status: Optional[str] = None, limit: int = 50):
    """List quotes, optionally filtered by business unit and/or status."""
    _ensure_db()
    conn = sqlite3.connect(str(QUOTES_DB))
    conn.row_factory = sqlite3.Row

    query = "SELECT * FROM quotes WHERE 1=1"
    params = []  # type: List[Any]
    if bu:
        query += " AND business_unit = ?"
        params.append(bu.upper())
    if status:
        query += " AND status = ?"
        params.append(status.lower())
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()

    return {
        "count": len(rows),
        "quotes": [dict(r) for r in rows],
    }


@router.get("/{quote_number}")
def get_quote(quote_number: str):
    """Get a specific quote by number."""
    _ensure_db()
    conn = sqlite3.connect(str(QUOTES_DB))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM quotes WHERE quote_number = ?", (quote_number,)).fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Quote not found: %s" % quote_number)

    data = dict(row)
    if data.get("line_items_json"):
        data["line_items"] = json.loads(data["line_items_json"])
        del data["line_items_json"]
    return data


@router.patch("/{quote_number}/status")
def update_quote_status(quote_number: str, status: str):
    """Update quote status (draft, sent, invoiced, expired, paid)."""
    valid = {"draft", "sent", "invoiced", "expired", "paid"}
    if status.lower() not in valid:
        raise HTTPException(status_code=400, detail="Invalid status. Use: %s" % ", ".join(sorted(valid)))

    _ensure_db()
    conn = sqlite3.connect(str(QUOTES_DB))
    cur = conn.execute(
        "UPDATE quotes SET status = ?, updated_at = datetime('now') WHERE quote_number = ?",
        (status.lower(), quote_number)
    )
    conn.commit()
    conn.close()

    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Quote not found: %s" % quote_number)

    return {"ok": True, "quote_number": quote_number, "status": status.lower()}
