#!/usr/bin/env python3
"""
Finance Engine — AI Transaction Categorization
Batch-categorizes uncategorized transactions using GPT-4.1-mini.
Enriches merchant data and maps to IRS Schedule C tax lines.

Usage:
    python3 finance_categorize.py --batch 50      # Process 50 uncategorized txns
    python3 finance_categorize.py --batch 50 --dry-run  # Preview without writing
    python3 finance_categorize.py --status         # Show categorization stats
    python3 finance_categorize.py --all            # Process ALL uncategorized

Dependencies: openai (or raw HTTP), urllib (stdlib)
Python 3.9 compatible (Mac Mini /usr/bin/python3)
"""

import argparse
import json
import logging
import os
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SUPABASE_URL = "https://briokwdoonawhxisbydy.supabase.co"
SUPABASE_SERVICE_KEY = os.environ.get(
    "SUPABASE_SERVICE_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJyaW9rd2Rvb25hd2h4aXNieWR5Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3MTU1Njc2MiwiZXhwIjoyMDg3MTMyNzYyfQ.5V1BsTrqIHGKUUHYJ3PBpL9re_WzKqOzKoQ94dc3me8",
)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
if not OPENAI_API_KEY:
    # Try reading from OpenClaw config
    _oc_path = os.path.expanduser("~/.openclaw/openclaw.json")
    if os.path.exists(_oc_path):
        try:
            import json as _j
            with open(_oc_path) as _f:
                _oc = _j.load(_f)
                OPENAI_API_KEY = (_oc.get("env", {}).get("vars", {}).get("OPENAI_API_KEY", "")
                                  or _oc.get("env", {}).get("OPENAI_API_KEY", ""))
        except Exception:
            pass

LOG_DIR = os.path.expanduser("~/blaze-data/finance/logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            os.path.join(LOG_DIR, "categorize_%s.log" % datetime.now().strftime("%Y%m%d"))
        ),
    ],
)
log = logging.getLogger("finance_categorize")

# ---------------------------------------------------------------------------
# Known accounts for context
# ---------------------------------------------------------------------------

ACCOUNT_CONTEXT = {
    "967606a8": {"name": "Bailey Personal Checking", "entity": "personal"},
    "e82a7359": {"name": "Bailey Personal Savings", "entity": "personal"},
    "0c8ac59a": {"name": "Content Co-op Business Checking", "entity": "content_coop"},
    "d7da030b": {"name": "Content Co-op Business Savings", "entity": "content_coop"},
    "8a2fb168": {"name": "ACS Business Checking", "entity": "acs"},
    "f0ab3fd9": {"name": "ACS Business Savings", "entity": "acs"},
    "f45c0e7b": {"name": "Caio Personal Checking", "entity": "personal"},
}

# ---------------------------------------------------------------------------
# Category taxonomy
# ---------------------------------------------------------------------------

CATEGORIES = {
    "income": ["salary", "freelance", "client_payment", "refund", "interest", "dividends", "other_income"],
    "transfer": ["internal_transfer", "zelle", "venmo", "cashapp", "paypal", "wire", "ach"],
    "loan_payment": ["mortgage", "auto_loan", "student_loan", "personal_loan", "credit_card_payment"],
    "bank_fee": ["overdraft", "monthly_fee", "atm_fee", "wire_fee", "nsf_fee"],
    "food_drink": ["restaurant", "fast_food", "coffee", "groceries", "bar", "delivery"],
    "entertainment": ["streaming", "gaming", "movies", "concerts", "sports"],
    "shopping": ["clothing", "electronics", "amazon", "general_retail", "online_shopping"],
    "home": ["rent", "furnishing", "maintenance", "cleaning", "security"],
    "medical": ["doctor", "pharmacy", "dental", "vision", "insurance_medical"],
    "transportation": ["gas", "parking", "rideshare", "auto_insurance", "auto_repair", "toll"],
    "travel": ["flight", "hotel", "rental_car", "travel_expense"],
    "utilities": ["electric", "water", "gas_utility", "internet", "phone", "cable"],
    "business_expense": ["software", "equipment", "marketing", "advertising", "supplies",
                        "professional_services", "insurance_business", "shipping", "hosting"],
    "payroll": ["employee_salary", "payroll_tax", "benefits"],
    "contractor_payment": ["freelancer", "1099_contractor", "subcontractor"],
    "tax_payment": ["estimated_tax", "state_tax", "payroll_tax_payment", "sales_tax"],
}

# IRS Schedule C line mapping
TAX_LINE_MAP = {
    "advertising": "Line 8",
    "marketing": "Line 8",
    "commission": "Line 10",
    "bank_fee": "Line 10",
    "wire_fee": "Line 10",
    "contractor_payment": "Line 11",
    "freelancer": "Line 11",
    "1099_contractor": "Line 11",
    "subcontractor": "Line 11",
    "equipment": "Line 13",
    "insurance_business": "Line 15",
    "auto_insurance": "Line 15",
    "professional_services": "Line 17",
    "software": "Line 18",
    "supplies": "Line 18",
    "hosting": "Line 18",
    "rent": "Line 20b",
    "flight": "Line 24a",
    "hotel": "Line 24a",
    "travel_expense": "Line 24a",
    "rental_car": "Line 24a",
    "restaurant": "Line 24b",
    "fast_food": "Line 24b",
    "coffee": "Line 24b",
    "bar": "Line 24b",
    "delivery": "Line 24b",
    "electric": "Line 25",
    "internet": "Line 25",
    "phone": "Line 25",
    "water": "Line 25",
    "employee_salary": "Line 26",
    "payroll_tax": "Line 26",
}

# Deductible percentage overrides (meals = 50%)
DEDUCTIBLE_PCT = {
    "restaurant": 0.50,
    "fast_food": 0.50,
    "coffee": 0.50,
    "bar": 0.50,
    "delivery": 0.50,
}

# ---------------------------------------------------------------------------
# Known merchant patterns (fast pre-classification, no AI needed)
# ---------------------------------------------------------------------------

MERCHANT_PATTERNS = [
    # Income / Payments received
    (r"STRIPE.*PMNT RCVD", "income", "client_payment", "Stripe Payment", True),
    (r"PAYPAL TRANSFER", "transfer", "paypal", "PayPal", False),
    (r"Zelle payment from", "transfer", "zelle", None, False),  # merchant = sender name
    (r"DIRECT DEP", "income", "salary", None, True),
    (r"ACH CREDIT", "income", "other_income", None, True),

    # Common merchants
    (r"AMZN MKTP|AMAZON\.COM|AMAZON PRIME", "shopping", "amazon", "Amazon", None),
    (r"UBER\s+EATS|UBEREATS", "food_drink", "delivery", "Uber Eats", None),
    (r"UBER\s+\*TRIP|UBER\s+BV", "transportation", "rideshare", "Uber", None),
    (r"LYFT", "transportation", "rideshare", "Lyft", None),
    (r"DOORDASH", "food_drink", "delivery", "DoorDash", None),
    (r"GRUBHUB", "food_drink", "delivery", "Grubhub", None),
    (r"STARBUCKS", "food_drink", "coffee", "Starbucks", None),
    (r"DUNKIN", "food_drink", "coffee", "Dunkin' Donuts", None),
    (r"CHICK-FIL-A", "food_drink", "fast_food", "Chick-fil-A", None),
    (r"MCDONALD", "food_drink", "fast_food", "McDonald's", None),
    (r"TACO BELL", "food_drink", "fast_food", "Taco Bell", None),
    (r"WENDY", "food_drink", "fast_food", "Wendy's", None),
    (r"WHATABURGER", "food_drink", "fast_food", "Whataburger", None),
    (r"SUBWAY ", "food_drink", "fast_food", "Subway", None),
    (r"CHIPOTLE", "food_drink", "fast_food", "Chipotle", None),
    (r"WALMART", "shopping", "general_retail", "Walmart", None),
    (r"TARGET\s", "shopping", "general_retail", "Target", None),
    (r"COSTCO", "shopping", "general_retail", "Costco", None),
    (r"KROGER|RANDALLS|HEB\s|H-E-B", "food_drink", "groceries", None, None),
    (r"SHELL OIL|EXXON|CHEVRON|BP\s|VALERO|MURPHY|RACETRAC|BUCKY", "transportation", "gas", None, None),
    (r"WELLS FARGO MTG|WELLS FARGO MORT", "loan_payment", "mortgage", "Wells Fargo Mortgage", False),
    (r"FIFTH THIRD BANK", "loan_payment", "auto_loan", "Fifth Third Bank", False),
    (r"NETFLIX", "entertainment", "streaming", "Netflix", None),
    (r"SPOTIFY", "entertainment", "streaming", "Spotify", None),
    (r"HULU", "entertainment", "streaming", "Hulu", None),
    (r"APPLE\.COM/BILL", "entertainment", "streaming", "Apple Services", None),
    (r"GOOGLE \*", "business_expense", "software", "Google", None),
    (r"ADOBE", "business_expense", "software", "Adobe", None),
    (r"CANVA", "business_expense", "software", "Canva", None),
    (r"GODADDY|NAMECHEAP", "business_expense", "hosting", None, True),
    (r"DIGITAL OCEAN|DIGITALOCEAN|AWS|AMAZON WEB", "business_expense", "hosting", None, True),
    (r"ATT\*|AT&T", "utilities", "phone", "AT&T", None),
    (r"T-MOBILE", "utilities", "phone", "T-Mobile", None),
    (r"VERIZON", "utilities", "phone", "Verizon", None),
    (r"COMCAST|XFINITY", "utilities", "internet", "Xfinity", None),
    (r"CENTERPOINT", "utilities", "electric", "CenterPoint Energy", None),
    (r"CITY OF HOUSTON", "utilities", "water", "City of Houston", None),
    (r"STATE FARM|GEICO|PROGRESSIVE|ALLSTATE", "transportation", "auto_insurance", None, None),
    (r"SQ \*", "shopping", "general_retail", None, None),  # Square POS — merchant varies
    (r"VENMO\s+(PAYMENT|CASHOUT)", "transfer", "venmo", "Venmo", False),
    (r"CASHAPP", "transfer", "cashapp", "CashApp", False),
    (r"APPLE CASH", "transfer", "internal_transfer", "Apple Cash", False),
    (r"Online Banking transfer|ONLINE TRANSFER|TRANSFER TO CHK|TRANSFER FROM CHK", "transfer", "internal_transfer", None, False),
    (r"Zelle Transfer|ZELLE (?:PAYMENT|SEND)", "transfer", "zelle", None, False),
    (r"OVERDRAFT|NON-SUFFICIENT|\bNSF\b", "bank_fee", "overdraft", None, False),
    (r"MONTHLY MAINTENANCE|MONTHLY SERVICE|MAINT FEE", "bank_fee", "monthly_fee", None, False),
    (r"ATM FEE|ATM REBATE", "bank_fee", "atm_fee", None, False),
    (r"BKOFAMERICA ATM.*DEPOSIT|ATM.*DEPOSIT", "income", "other_income", "ATM Deposit", False),
    (r"BKOFAMERICA ATM.*WITHDRWL|ATM.*WITHDRWL", "transfer", "internal_transfer", "ATM Withdrawal", False),
    (r"WF HOME MTG|WELLS FARGO HOME", "loan_payment", "mortgage", "Wells Fargo Mortgage", False),
    (r"VENMO DES:CASHOUT", "transfer", "venmo", "Venmo", False),
    (r"VENMO DES:PAYMENT", "transfer", "venmo", "Venmo", False),
    (r"PAYPAL DES:TRANSFER|PAYPAL DES:INST XFER", "transfer", "paypal", "PayPal", False),
    (r"WISE ", "transfer", "wire", "Wise", False),
]

import re
COMPILED_PATTERNS = [(re.compile(p, re.IGNORECASE), cat, sub, merch, ded)
                     for p, cat, sub, merch, ded in MERCHANT_PATTERNS]


# ---------------------------------------------------------------------------
# Supabase helpers (same as finance_import.py)
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


def supa_insert(table, rows, on_conflict=None):
    """Insert rows with optional conflict resolution."""
    if not rows:
        return []
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": "Bearer %s" % SUPABASE_SERVICE_KEY,
        "Content-Type": "application/json",
        "Prefer": "return=representation,resolution=ignore-duplicates",
        "Accept-Profile": "finance",
        "Content-Profile": "finance",
    }
    url = "%s/rest/v1/%s" % (SUPABASE_URL, table)
    if on_conflict:
        url += "?on_conflict=%s" % on_conflict
    data = json.dumps(rows).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        resp = urllib.request.urlopen(req)
        text = resp.read().decode("utf-8")
        return json.loads(text) if text.strip() else []
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        log.error("Insert to %s failed (%d): %s", table, e.code, error_body)
        return []


# ---------------------------------------------------------------------------
# Pattern-based pre-classification (free, instant)
# ---------------------------------------------------------------------------

def pattern_classify(description):
    """Try to classify a transaction using known patterns."""
    for pattern, category, subcategory, merchant, is_ded in COMPILED_PATTERNS:
        if pattern.search(description):
            return {
                "category_primary": category,
                "category_detail": subcategory,
                "merchant_clean": merchant,
                "is_deductible": is_ded,
            }
    return None


def extract_zelle_sender(description):
    """Extract sender name from Zelle payment descriptions."""
    m = re.search(r"Zelle payment from (.+?)(?:\s+Conf#|\s*$)", description, re.IGNORECASE)
    if m:
        return m.group(1).strip().title()
    m = re.search(r"Zelle Transfer (.+?)(?:\s+Conf#|\s*$)", description, re.IGNORECASE)
    if m:
        return m.group(1).strip().title()
    return None


# ---------------------------------------------------------------------------
# GPT-4.1-mini categorization
# ---------------------------------------------------------------------------

def openai_categorize(transactions, account_context):
    """Send a batch of transactions to GPT-4.1-mini for categorization."""
    if not OPENAI_API_KEY:
        log.warning("No OPENAI_API_KEY set — skipping AI categorization")
        return []

    # Build the batch prompt
    tx_lines = []
    for i, tx in enumerate(transactions):
        tx_lines.append(
            "%d. [%s] %s  $%.2f" % (i + 1, tx["date"], tx["description"], float(tx["amount"]))
        )

    prompt = """You are a financial transaction categorizer for a small business owner in Houston, TX who runs two LLCs:
1. Content Co-op LLC — a video production / content marketing company
2. Astro Cleaning Services (ACS) LLC — a commercial cleaning company

The owner (Bailey) also has personal bank accounts. Transactions may be personal, Content Co-op business, or ACS business.

Account context: %s

Categorize each transaction below. Return a JSON array with one object per transaction:
{
  "index": 1,
  "category_primary": "one of: income, transfer, loan_payment, bank_fee, food_drink, entertainment, shopping, home, medical, transportation, travel, utilities, business_expense, payroll, contractor_payment, tax_payment",
  "category_detail": "subcategory string",
  "merchant_name": "clean merchant name or null",
  "business_entity": "content_coop, acs, personal, or mixed",
  "is_deductible": true/false,
  "deductible_pct": 1.0 (or 0.5 for meals),
  "tax_category": "Schedule C line reference or null",
  "confidence": 0.0 to 1.0
}

Key rules:
- Zelle/Venmo/CashApp between Bailey & Caio or between accounts = transfer
- Stripe payments received = income (client_payment)
- "ASTRO CLEAN" anything = ACS business
- Software subscriptions (Adobe, Google, etc.) = Content Co-op business expense
- Mortgage = personal loan_payment
- Gas stations could be personal OR business (default personal unless clearly fleet)
- Food/restaurants = personal unless clearly a business meal

Transactions:
%s

Return ONLY the JSON array, no markdown formatting.""" % (
        json.dumps(account_context),
        "\n".join(tx_lines),
    )

    headers = {
        "Authorization": "Bearer %s" % OPENAI_API_KEY,
        "Content-Type": "application/json",
    }

    body = {
        "model": "gpt-4.1-mini",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 4000,
    }

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        resp = urllib.request.urlopen(req, timeout=60)
        result = json.loads(resp.read().decode("utf-8"))
        content = result["choices"][0]["message"]["content"].strip()

        # Strip markdown code fences if present
        if content.startswith("```"):
            content = content.split("\n", 1)[1]
        if content.endswith("```"):
            content = content.rsplit("```", 1)[0]
        content = content.strip()

        categories = json.loads(content)
        return categories

    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        log.error("OpenAI API error (%d): %s", e.code, error_body[:500])
        return []
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        log.error("Failed to parse OpenAI response: %s", e)
        return []
    except Exception as e:
        log.error("OpenAI request failed: %s", e)
        return []


# ---------------------------------------------------------------------------
# Merchant upsert
# ---------------------------------------------------------------------------

def upsert_merchant(raw_name, clean_name=None, category=None, subcategory=None):
    """Insert or get existing merchant by raw_name."""
    # Check if exists
    existing = supa_request("GET", "merchants", params={
        "select": "id",
        "raw_name": "eq.%s" % raw_name,
        "limit": "1",
    })
    if existing:
        return existing[0]["id"]

    # Insert new
    row = {
        "raw_name": raw_name,
        "clean_name": clean_name,
        "category": category,
        "subcategory": subcategory,
    }
    result = supa_insert("merchants", [row], on_conflict="raw_name")
    if result:
        return result[0]["id"]
    return None


# ---------------------------------------------------------------------------
# Main categorization pipeline
# ---------------------------------------------------------------------------

def get_account_context(account_id):
    """Get entity context for an account."""
    prefix = account_id[:8]
    for key, ctx in ACCOUNT_CONTEXT.items():
        if prefix == key:
            return ctx
    return {"name": "Unknown", "entity": "personal"}


def categorize_batch(batch_size=50, dry_run=False):
    """Fetch and categorize a batch of uncategorized transactions."""

    # Fetch uncategorized transactions
    txns = supa_request("GET", "raw_transactions", params={
        "select": "id,account_id,date,description,amount",
        "ai_categorized": "eq.false",
        "order": "date.asc",
        "limit": str(batch_size),
    })

    if not txns:
        log.info("No uncategorized transactions found.")
        return 0

    log.info("Processing %d uncategorized transactions", len(txns))

    # Phase 1: Pattern-based classification (free, instant)
    pattern_hits = 0
    ai_needed = []

    for tx in txns:
        result = pattern_classify(tx["description"])
        if result:
            pattern_hits += 1
            tx["_cat"] = result

            # Special handling for Zelle — extract sender as merchant
            if result["category_detail"] == "zelle" and not result.get("merchant_clean"):
                sender = extract_zelle_sender(tx["description"])
                if sender:
                    result["merchant_clean"] = sender

            # Determine business entity from account
            ctx = get_account_context(tx["account_id"])
            if result["category_primary"] == "transfer":
                tx["_cat"]["business_entity"] = ctx["entity"]
            elif ctx["entity"] != "personal":
                tx["_cat"]["business_entity"] = ctx["entity"]
            else:
                tx["_cat"]["business_entity"] = "personal"

            # Tax line
            detail = result["category_detail"]
            tx["_cat"]["tax_category"] = TAX_LINE_MAP.get(detail)
            tx["_cat"]["deductible_pct"] = DEDUCTIBLE_PCT.get(detail, 1.0)

        else:
            ai_needed.append(tx)

    log.info("Pattern matches: %d, AI needed: %d", pattern_hits, len(ai_needed))

    # Phase 2: AI classification for remaining
    if ai_needed and not dry_run:
        # Group by account for context
        by_account = {}
        for tx in ai_needed:
            aid = tx["account_id"]
            if aid not in by_account:
                by_account[aid] = []
            by_account[aid].append(tx)

        for aid, group in by_account.items():
            ctx = get_account_context(aid)
            # Process in sub-batches of 30 for AI (prompt size limit)
            for i in range(0, len(group), 30):
                sub = group[i:i + 30]
                log.info("AI categorizing %d transactions for %s", len(sub), ctx["name"])

                results = openai_categorize(sub, ctx)

                for cat_result in results:
                    idx = cat_result.get("index", 0) - 1
                    if 0 <= idx < len(sub):
                        sub[idx]["_cat"] = {
                            "category_primary": cat_result.get("category_primary", "shopping"),
                            "category_detail": cat_result.get("category_detail", ""),
                            "merchant_clean": cat_result.get("merchant_name"),
                            "business_entity": cat_result.get("business_entity", "personal"),
                            "is_deductible": cat_result.get("is_deductible", False),
                            "deductible_pct": cat_result.get("deductible_pct", 1.0),
                            "tax_category": cat_result.get("tax_category"),
                        }

                # Rate limit
                time.sleep(0.5)

    if dry_run:
        log.info("DRY RUN — not writing to database")
        for tx in txns:
            cat = tx.get("_cat", {})
            print("  [%s] %-50s $%8.2f -> %s / %s (%s)" % (
                tx["date"],
                tx["description"][:50],
                float(tx["amount"]),
                cat.get("category_primary", "?"),
                cat.get("category_detail", "?"),
                cat.get("business_entity", "?"),
            ))
        return len(txns)

    # Phase 3: Write results to database
    updated = 0
    for tx in txns:
        cat = tx.get("_cat")
        if not cat:
            continue

        # Upsert merchant if we have a clean name
        merchant_id = None
        raw_desc = tx["description"][:80]
        if cat.get("merchant_clean"):
            merchant_id = upsert_merchant(
                raw_name=raw_desc,
                clean_name=cat["merchant_clean"],
                category=cat.get("category_primary"),
                subcategory=cat.get("category_detail"),
            )

        # Update the transaction
        update = {
            "category_primary": cat.get("category_primary"),
            "category_detail": cat.get("category_detail"),
            "business_entity": cat.get("business_entity"),
            "is_deductible": cat.get("is_deductible"),
            "deductible_pct": cat.get("deductible_pct", 1.0),
            "tax_category": cat.get("tax_category"),
            "ai_categorized": True,
        }
        if merchant_id:
            update["merchant_id"] = merchant_id

        try:
            supa_patch("raw_transactions", update, {"id": "eq.%s" % tx["id"]})
            updated += 1
        except Exception as e:
            log.error("Failed to update tx %s: %s", tx["id"][:8], e)

    log.info("Updated %d transactions", updated)
    return updated


# ---------------------------------------------------------------------------
# Status report
# ---------------------------------------------------------------------------

def show_status():
    """Show categorization statistics."""
    # Count categorized vs uncategorized
    all_txns = supa_request("GET", "raw_transactions", params={
        "select": "id,ai_categorized",
    })

    total = len(all_txns)
    categorized = sum(1 for t in all_txns if t.get("ai_categorized"))
    uncategorized = total - categorized

    print("\nFinance Categorization Status")
    print("=" * 50)
    print("Total transactions:    %d" % total)
    print("Categorized:           %d (%.1f%%)" % (categorized, 100 * categorized / total if total else 0))
    print("Uncategorized:         %d" % uncategorized)

    if categorized > 0:
        # Category breakdown
        cats = supa_request("GET", "raw_transactions", params={
            "select": "category_primary",
            "ai_categorized": "eq.true",
        })
        counts = {}
        for c in cats:
            k = c.get("category_primary", "unknown")
            counts[k] = counts.get(k, 0) + 1

        print("\nCategory Breakdown:")
        print("-" * 40)
        for k, v in sorted(counts.items(), key=lambda x: -x[1]):
            print("  %-25s %5d" % (k, v))

    # Merchant count
    merchants = supa_request("GET", "merchants", params={"select": "id"})
    print("\nUnique merchants:      %d" % len(merchants))
    print("")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Finance Engine — AI Categorization")
    parser.add_argument("--batch", type=int, default=50, help="Batch size (default: 50)")
    parser.add_argument("--all", action="store_true", help="Process ALL uncategorized transactions")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--status", action="store_true", help="Show categorization stats")
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    if args.all:
        # Process everything in batches
        total = 0
        while True:
            count = categorize_batch(batch_size=args.batch, dry_run=args.dry_run)
            if count == 0:
                break
            total += count
            if args.dry_run:
                break
        log.info("All done. Total categorized: %d", total)
    else:
        categorize_batch(batch_size=args.batch, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
