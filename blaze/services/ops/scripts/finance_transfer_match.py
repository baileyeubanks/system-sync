#!/usr/bin/env python3
"""
Finance Engine — Transfer Matching
Detects inter-account transfers by finding opposite-sign transactions
of the same amount within a ±3 day window across different accounts.

Usage:
    python3 finance_transfer_match.py              # Run matching
    python3 finance_transfer_match.py --dry-run    # Preview matches
    python3 finance_transfer_match.py --status     # Show transfer stats

Python 3.9 compatible (Mac Mini /usr/bin/python3)
"""

import argparse
import json
import logging
import os
import sys
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timedelta
import uuid

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SUPABASE_URL = "https://briokwdoonawhxisbydy.supabase.co"
SUPABASE_SERVICE_KEY = os.environ.get(
    "SUPABASE_SERVICE_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJyaW9rd2Rvb25hd2h4aXNieWR5Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3MTU1Njc2MiwiZXhwIjoyMDg3MTMyNzYyfQ.5V1BsTrqIHGKUUHYJ3PBpL9re_WzKqOzKoQ94dc3me8",
)

LOG_DIR = os.path.expanduser("~/blaze-data/finance/logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            os.path.join(LOG_DIR, "transfer_match_%s.log" % datetime.now().strftime("%Y%m%d"))
        ),
    ],
)
log = logging.getLogger("finance_transfer_match")

# Known transfer keywords
TRANSFER_KEYWORDS = [
    "zelle", "venmo", "cashapp", "cash app", "paypal",
    "apple cash", "transfer", "wire", "ach",
    "internal", "xfer",
]

# ---------------------------------------------------------------------------
# Supabase helpers
# ---------------------------------------------------------------------------

def supa_request(method, table, body=None, params=None):
    """Generic Supabase REST API request."""
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": "Bearer %s" % SUPABASE_SERVICE_KEY,
        "Content-Type": "application/json",
        "Accept-Profile": "finance",
        "Content-Profile": "finance",
    }
    url = "%s/rest/v1/%s" % (SUPABASE_URL, table)
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    resp = urllib.request.urlopen(req)
    text = resp.read().decode("utf-8")
    return json.loads(text) if text.strip() else []


def supa_patch(table, body, filters):
    """PATCH with filters."""
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": "Bearer %s" % SUPABASE_SERVICE_KEY,
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
        "Accept-Profile": "finance",
        "Content-Profile": "finance",
    }
    url = "%s/rest/v1/%s?%s" % (SUPABASE_URL, table, urllib.parse.urlencode(filters))
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="PATCH")
    urllib.request.urlopen(req)


# ---------------------------------------------------------------------------
# Transfer matching logic
# ---------------------------------------------------------------------------

def fetch_all_transactions():
    """Fetch all transactions for matching. Paginate through results."""
    all_txns = []
    offset = 0
    batch = 1000

    while True:
        txns = supa_request("GET", "raw_transactions", params={
            "select": "id,account_id,date,description,amount,is_transfer,transfer_match_id",
            "order": "date.asc",
            "offset": str(offset),
            "limit": str(batch),
        })
        if not txns:
            break
        all_txns.extend(txns)
        if len(txns) < batch:
            break
        offset += batch

    return all_txns


def find_matches(transactions, max_day_gap=3):
    """Find transfer pairs: opposite sign, same absolute amount, different accounts, within day gap."""

    # Index by absolute amount for fast lookup
    by_amount = {}
    for tx in transactions:
        amt = abs(float(tx["amount"]))
        key = "%.2f" % amt
        if key not in by_amount:
            by_amount[key] = []
        by_amount[key].append(tx)

    matches = []
    matched_ids = set()

    for key, group in by_amount.items():
        if len(group) < 2:
            continue

        # Find pairs with opposite signs and different accounts
        debits = [t for t in group if float(t["amount"]) < 0]
        credits = [t for t in group if float(t["amount"]) > 0]

        for d in debits:
            if d["id"] in matched_ids:
                continue
            d_date = datetime.strptime(d["date"], "%Y-%m-%d")

            best_match = None
            best_gap = max_day_gap + 1

            for c in credits:
                if c["id"] in matched_ids:
                    continue
                if c["account_id"] == d["account_id"]:
                    continue  # Same account — not a transfer

                c_date = datetime.strptime(c["date"], "%Y-%m-%d")
                gap = abs((d_date - c_date).days)

                if gap <= max_day_gap and gap < best_gap:
                    # Boost confidence if description suggests transfer
                    desc_lower = (d["description"] + " " + c["description"]).lower()
                    is_transfer_desc = any(kw in desc_lower for kw in TRANSFER_KEYWORDS)

                    best_match = c
                    best_gap = gap

            if best_match:
                match_id = str(uuid.uuid4())
                matches.append({
                    "debit": d,
                    "credit": best_match,
                    "match_id": match_id,
                    "gap_days": best_gap,
                    "amount": key,
                })
                matched_ids.add(d["id"])
                matched_ids.add(best_match["id"])

    return matches


def apply_matches(matches, dry_run=False):
    """Write transfer match results to database."""
    if dry_run:
        print("\nTransfer Matches Found: %d" % len(matches))
        print("=" * 100)
        for m in matches[:50]:  # Show first 50
            d = m["debit"]
            c = m["credit"]
            print("\n  $%s (gap: %d days)" % (m["amount"], m["gap_days"]))
            print("    DEBIT:  [%s] %s" % (d["date"], d["description"][:60]))
            print("    CREDIT: [%s] %s" % (c["date"], c["description"][:60]))
        if len(matches) > 50:
            print("\n  ... and %d more" % (len(matches) - 50))
        return

    updated = 0
    for m in matches:
        match_id = m["match_id"]
        try:
            supa_patch("raw_transactions", {
                "is_transfer": True,
                "transfer_match_id": match_id,
            }, {"id": "eq.%s" % m["debit"]["id"]})

            supa_patch("raw_transactions", {
                "is_transfer": True,
                "transfer_match_id": match_id,
            }, {"id": "eq.%s" % m["credit"]["id"]})

            updated += 1
        except Exception as e:
            log.error("Failed to update transfer pair %s: %s", match_id[:8], e)

    log.info("Marked %d transfer pairs (%d transactions)", updated, updated * 2)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def show_status():
    """Show transfer matching statistics."""
    all_txns = fetch_all_transactions()
    total = len(all_txns)
    transfers = [t for t in all_txns if t.get("is_transfer")]
    non_transfers = total - len(transfers)

    # Sum transfer amounts
    transfer_total = sum(abs(float(t["amount"])) for t in transfers if float(t["amount"]) < 0)

    print("\nTransfer Matching Status")
    print("=" * 50)
    print("Total transactions:    %d" % total)
    print("Transfers detected:    %d (%.1f%%)" % (len(transfers), 100 * len(transfers) / total if total else 0))
    print("Non-transfer:          %d" % non_transfers)
    print("Transfer volume:       $%.2f" % transfer_total)

    if transfers:
        # Unique match IDs
        match_ids = set(t.get("transfer_match_id") for t in transfers if t.get("transfer_match_id"))
        print("Unique transfer pairs: %d" % len(match_ids))
    print("")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Finance Engine — Transfer Matching")
    parser.add_argument("--dry-run", action="store_true", help="Preview matches without writing")
    parser.add_argument("--status", action="store_true", help="Show transfer stats")
    parser.add_argument("--max-gap", type=int, default=3, help="Max day gap for matching (default: 3)")
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    log.info("Fetching all transactions...")
    txns = fetch_all_transactions()
    log.info("Loaded %d transactions", len(txns))

    # Only match unmatched transactions
    unmatched = [t for t in txns if not t.get("is_transfer")]
    log.info("Unmatched transactions: %d", len(unmatched))

    log.info("Finding transfer pairs (max gap: %d days)...", args.max_gap)
    matches = find_matches(unmatched, max_day_gap=args.max_gap)
    log.info("Found %d transfer pairs", len(matches))

    if matches:
        apply_matches(matches, dry_run=args.dry_run)
    else:
        log.info("No new transfer matches found.")


if __name__ == "__main__":
    main()
