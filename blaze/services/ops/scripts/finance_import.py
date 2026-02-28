#!/usr/bin/env python3
"""
Finance Engine — PDF Bank Statement Import Pipeline
Extracts transactions from bank statement PDFs and loads to Supabase.

Usage:
    python3 finance_import.py --file statement.pdf --account <account_id>
    python3 finance_import.py --dir ~/blaze-data/finance/statements/chase/ --account <account_id>
    python3 finance_import.py --scan <nas_path> --entity-map <json>
    python3 finance_import.py --status
    python3 finance_import.py --list-accounts

Dependencies: pdfplumber, psycopg2-binary
Python 3.9 compatible (Mac Mini /usr/bin/python3)
"""

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from datetime import datetime, date
from decimal import Decimal, InvalidOperation

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SUPABASE_URL = "https://briokwdoonawhxisbydy.supabase.co"
SUPABASE_SERVICE_KEY = os.environ.get(
    "SUPABASE_SERVICE_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJyaW9rd2Rvb25hd2h4aXNieWR5Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3MTU1Njc2MiwiZXhwIjoyMDg3MTMyNzYyfQ.5V1BsTrqIHGKUUHYJ3PBpL9re_WzKqOzKoQ94dc3me8",
)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

LOG_DIR = os.path.expanduser("~/blaze-data/finance/logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            os.path.join(LOG_DIR, "import_%s.log" % datetime.now().strftime("%Y%m%d"))
        ),
    ],
)
log = logging.getLogger("finance_import")

# ---------------------------------------------------------------------------
# Supabase REST helpers
# ---------------------------------------------------------------------------

import urllib.request
import urllib.error


def supa_request(method, table, data=None, params=None):
    """Make a request to Supabase PostgREST API (finance schema)."""
    url = "%s/rest/v1/%s" % (SUPABASE_URL, table)
    if params:
        qs = []
        for k, v in params.items():
            qs.append("%s=%s" % (k, v))
        url += "?" + "&".join(qs)

    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": "Bearer %s" % SUPABASE_SERVICE_KEY,
        "Content-Type": "application/json",
        "Prefer": "return=representation",
        "Accept-Profile": "finance",
        "Content-Profile": "finance",
    }

    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)

    try:
        resp = urllib.request.urlopen(req)
        text = resp.read().decode("utf-8")
        return json.loads(text) if text.strip() else []
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        log.error("Supabase %s %s failed (%d): %s", method, table, e.code, error_body)
        raise


def supa_insert(table, rows, on_conflict="import_hash"):
    """Insert rows, skip duplicates via ON CONFLICT DO NOTHING."""
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
    url = "%s/rest/v1/%s?on_conflict=%s" % (SUPABASE_URL, table, on_conflict)
    body = json.dumps(rows).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        resp = urllib.request.urlopen(req)
        text = resp.read().decode("utf-8")
        return json.loads(text) if text.strip() else []
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        log.error("Insert to %s failed (%d): %s", table, e.code, error_body)
        raise


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_import_hash(account_id, tx_date, amount, description):
    raw = "%s|%s|%.2f|%s" % (
        account_id, tx_date, float(amount), description[:50].strip().lower(),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def file_hash(filepath):
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_amount(raw):
    if raw is None:
        return None
    raw = str(raw).strip()
    if not raw or raw == "-":
        return None
    clean = raw.replace("$", "").replace(",", "").replace(" ", "")
    if clean.startswith("(") and clean.endswith(")"):
        clean = "-" + clean[1:-1]
    try:
        return Decimal(clean)
    except (InvalidOperation, ValueError):
        return None


def parse_date(raw, year_hint=None):
    if raw is None:
        return None
    raw = str(raw).strip()
    if not raw:
        return None
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})$", raw)
    if m and year_hint:
        try:
            return date(year_hint, int(m.group(1)), int(m.group(2)))
        except ValueError:
            pass
    return None


def extract_text_pages(filepath):
    import pdfplumber
    pages = []
    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            pages.append(text)
    return pages


def detect_institution(text):
    text_upper = text.upper()
    if "BANK OF AMERICA" in text_upper:
        return "boa"
    if "JPMORGAN CHASE" in text_upper or "CHASE BANK" in text_upper:
        return "chase"
    if "WELLS FARGO" in text_upper:
        return "wells_fargo"
    if "CAPITAL ONE" in text_upper:
        return "capital_one"
    if "AMERICAN EXPRESS" in text_upper or "AMEX" in text_upper:
        return "amex"
    if "PAYPAL" in text_upper:
        return "paypal"
    return "generic"


# ---------------------------------------------------------------------------
# Bank of America parser
# ---------------------------------------------------------------------------

def parse_boa(filepath, year_hint=None):
    """Parse Bank of America statement PDF."""
    pages = extract_text_pages(filepath)
    if not pages:
        return [], {}

    # Detect statement period and year from first pages
    full_header = "\n".join(pages[:2])
    if not year_hint:
        m = re.search(r"for\s+\w+\s+\d+,?\s+(\d{4})\s+to", full_header)
        if m:
            year_hint = int(m.group(1))
        else:
            m = re.search(r"20\d{2}", full_header)
            if m:
                year_hint = int(m.group())
    if not year_hint:
        year_hint = datetime.now().year

    # Detect account numbers
    accounts_found = {}
    for m in re.finditer(r"(\d{4}\s+\d{4}\s+\d{4})", full_header):
        acct_num = m.group(1).replace(" ", "")
        last4 = acct_num[-4:]
        accounts_found[last4] = acct_num

    transactions = []
    current_section = None  # 'deposit', 'withdrawal', 'fee', etc.
    current_sign = 1  # 1 for deposits, -1 for withdrawals

    # BofA patterns
    # Transaction line: MM/DD/YY description amount
    tx_re = re.compile(
        r"^(\d{2}/\d{2}/\d{2})\s+"  # date MM/DD/YY
        r"(.+?)\s+"                   # description
        r"(-?[\d,]+\.\d{2})$"        # amount
    )
    # Sometimes amount has no leading dash, sign comes from section
    tx_re2 = re.compile(
        r"^(\d{2}/\d{2}/\d{2})\s+"
        r"(.+?)\s+"
        r"([\d,]+\.\d{2})$"
    )

    for page_num, page_text in enumerate(pages):
        for line in page_text.split("\n"):
            line = line.strip()
            if not line:
                continue

            # Detect sections to determine sign
            line_upper = line.upper()
            if "DEPOSITS AND OTHER ADDITIONS" in line_upper or "DEPOSITS AND OTHER CREDITS" in line_upper:
                current_section = "deposit"
                current_sign = 1
                continue
            if "WITHDRAWALS AND OTHER SUBTRACTIONS" in line_upper or "ATM AND DEBIT CARD SUBTRACTIONS" in line_upper:
                current_section = "withdrawal"
                current_sign = -1
                continue
            if "OTHER SUBTRACTIONS" in line_upper and "CONTINUED" not in line_upper:
                current_section = "withdrawal"
                current_sign = -1
                continue
            if "SERVICE FEES" in line_upper:
                current_section = "fee"
                current_sign = -1
                continue
            if "TOTAL " in line_upper and ("DEPOSITS" in line_upper or "SUBTRACTIONS" in line_upper or "FEES" in line_upper):
                continue  # Skip total lines
            if "ENDING BALANCE" in line_upper or "BEGINNING BALANCE" in line_upper:
                continue

            # Try to parse transaction line
            m = tx_re.match(line)
            if m:
                tx_date = parse_date(m.group(1), year_hint)
                desc = m.group(2).strip()
                raw_amount = parse_amount(m.group(3))
                if tx_date and raw_amount is not None and len(desc) > 2:
                    # If amount already has sign, use it; otherwise apply section sign
                    if raw_amount < 0:
                        amount = raw_amount
                    else:
                        amount = raw_amount * current_sign
                    transactions.append({
                        "date": str(tx_date),
                        "description": desc,
                        "amount": str(amount),
                        "balance": None,
                        "raw_text": line,
                        "source_page": page_num + 1,
                    })
                continue

            m2 = tx_re2.match(line)
            if m2 and current_section:
                tx_date = parse_date(m2.group(1), year_hint)
                desc = m2.group(2).strip()
                raw_amount = parse_amount(m2.group(3))
                if tx_date and raw_amount is not None and len(desc) > 2:
                    amount = raw_amount * current_sign
                    transactions.append({
                        "date": str(tx_date),
                        "description": desc,
                        "amount": str(amount),
                        "balance": None,
                        "raw_text": line,
                        "source_page": page_num + 1,
                    })

    return transactions, accounts_found


# ---------------------------------------------------------------------------
# Generic parser (regex fallback)
# ---------------------------------------------------------------------------

TX_PATTERN = re.compile(
    r"(\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)"
    r"\s+"
    r"(.+?)"
    r"\s+"
    r"(-?\$?[\d,]+\.?\d{0,2})"
    r"(?:\s+(-?\$?[\d,]+\.?\d{0,2}))?"
)


def parse_generic(filepath, year_hint=None):
    pages = extract_text_pages(filepath)
    if not pages:
        return [], {}

    if not year_hint:
        for pt in pages[:2]:
            m = re.search(r"20\d{2}", pt)
            if m:
                year_hint = int(m.group())
                break
        if not year_hint:
            year_hint = datetime.now().year

    transactions = []
    for page_num, page_text in enumerate(pages):
        for line in page_text.split("\n"):
            line = line.strip()
            if not line:
                continue
            match = TX_PATTERN.search(line)
            if not match:
                continue
            tx_date = parse_date(match.group(1), year_hint)
            desc = match.group(2).strip()
            amount = parse_amount(match.group(3))
            balance = parse_amount(match.group(4)) if match.group(4) else None
            if tx_date and amount is not None and len(desc) > 2:
                transactions.append({
                    "date": str(tx_date),
                    "description": desc,
                    "amount": str(amount),
                    "balance": str(balance) if balance is not None else None,
                    "raw_text": line,
                    "source_page": page_num + 1,
                })
    return transactions, {}


# ---------------------------------------------------------------------------
# LLM fallback parser
# ---------------------------------------------------------------------------

def parse_with_llm(filepath):
    if not OPENAI_API_KEY:
        log.warning("No OPENAI_API_KEY — cannot use LLM fallback")
        return []

    pages = extract_text_pages(filepath)
    if not pages:
        return []

    all_transactions = []
    for page_num, page_text in enumerate(pages):
        if len(page_text.strip()) < 50:
            continue

        prompt = (
            "Extract all financial transactions from this bank statement page text.\n"
            "Return a JSON object with key \"transactions\" containing an array:\n"
            '{\"transactions\": [{\"date\": \"YYYY-MM-DD\", \"description\": \"...\", \"amount\": -12.50}]}\n'
            "Rules:\n"
            "- Negative amount = money OUT (debit/payment/withdrawal)\n"
            "- Positive amount = money IN (credit/deposit)\n"
            "- Skip headers, footers, subtotals, balance summaries, total lines\n"
            "- Only include actual transaction line items\n\n"
            "Page text:\n" + page_text[:4000]
        )

        try:
            req_data = json.dumps({
                "model": "gpt-4.1-mini",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "response_format": {"type": "json_object"},
            }).encode("utf-8")

            req = urllib.request.Request(
                "https://api.openai.com/v1/chat/completions",
                data=req_data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer %s" % OPENAI_API_KEY,
                },
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=60)
            result = json.loads(resp.read().decode("utf-8"))
            content = result["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            txs = parsed.get("transactions", []) if isinstance(parsed, dict) else parsed

            for tx in txs:
                all_transactions.append({
                    "date": tx.get("date", ""),
                    "description": tx.get("description", ""),
                    "amount": str(tx.get("amount", 0)),
                    "balance": None,
                    "raw_text": "",
                    "source_page": page_num + 1,
                })
        except Exception as e:
            log.warning("LLM extraction failed for page %d: %s", page_num + 1, e)
            continue

    return all_transactions


# ---------------------------------------------------------------------------
# CSV parsers
# ---------------------------------------------------------------------------

def parse_csv_generic(filepath):
    import csv
    transactions = []
    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        headers_lower = [h.lower() for h in headers]

        date_col = next((h for h, hl in zip(headers, headers_lower) if hl in ("date", "posting date", "trans date", "transaction date", "datetime")), None)
        desc_col = next((h for h, hl in zip(headers, headers_lower) if hl in ("description", "memo", "name", "note", "details", "merchant")), None)
        amount_col = next((h for h, hl in zip(headers, headers_lower) if hl in ("amount", "amount (total)", "gross", "net")), None)
        balance_col = next((h for h, hl in zip(headers, headers_lower) if hl in ("balance", "running balance")), None)

        if not date_col or not amount_col:
            log.warning("CSV missing Date or Amount column. Headers: %s", headers)
            return [], {}

        for row in reader:
            tx_date = parse_date(row.get(date_col, ""))
            desc = row.get(desc_col, "") if desc_col else ""
            amount = parse_amount(row.get(amount_col, ""))
            balance = parse_amount(row.get(balance_col, "")) if balance_col else None
            if tx_date and amount is not None:
                transactions.append({
                    "date": str(tx_date),
                    "description": desc.strip() or "(no description)",
                    "amount": str(amount),
                    "balance": str(balance) if balance is not None else None,
                    "raw_text": json.dumps(row),
                    "source_page": 0,
                })
    return transactions, {}


# ---------------------------------------------------------------------------
# Parser dispatcher
# ---------------------------------------------------------------------------

PARSERS = {
    "boa": parse_boa,
    "chase": parse_generic,  # Will build Chase-specific later
    "wells_fargo": parse_generic,
    "generic": parse_generic,
}


def parse_file(filepath):
    ext = os.path.splitext(filepath)[1].lower()
    if ext == ".csv":
        return parse_csv_generic(filepath)
    if ext != ".pdf":
        log.warning("Unsupported file type: %s", ext)
        return [], {}

    pages = extract_text_pages(filepath)
    if not pages:
        log.warning("No text extracted from PDF: %s", filepath)
        return [], {}

    full_text = "\n".join(pages[:2])
    institution = detect_institution(full_text)
    log.info("Detected institution: %s", institution)

    parser = PARSERS.get(institution, parse_generic)
    transactions, meta = parser(filepath)

    if len(transactions) < 3 and len(pages) > 1:
        log.info("Parser found only %d transactions, trying LLM fallback...", len(transactions))
        llm_txs = parse_with_llm(filepath)
        if len(llm_txs) > len(transactions):
            log.info("LLM found %d transactions (vs %d from parser)", len(llm_txs), len(transactions))
            transactions = llm_txs

    return transactions, {"institution": institution}


# ---------------------------------------------------------------------------
# Import to Supabase
# ---------------------------------------------------------------------------

def import_transactions(filepath, account_id):
    basename = os.path.basename(filepath)
    fhash = file_hash(filepath)

    log.info("Processing: %s", basename)

    # Create import job
    try:
        job = supa_insert("import_jobs", [{
            "filename": basename,
            "file_hash": fhash,
            "account_id": account_id,
            "status": "processing",
            "started_at": datetime.utcnow().isoformat(),
        }], on_conflict="file_hash")
    except Exception:
        log.warning("File already imported (duplicate hash) or error: %s", basename)
        return 0

    if not job:
        log.warning("File already imported (duplicate hash): %s", basename)
        return 0

    job_id = job[0]["id"]

    try:
        transactions, meta = parse_file(filepath)
        institution = meta.get("institution", "unknown")
        log.info("Extracted %d transactions (%s)", len(transactions), institution)

        if not transactions:
            supa_request("PATCH", "import_jobs", {
                "status": "completed",
                "institution": institution,
                "rows_extracted": 0,
                "rows_inserted": 0,
                "completed_at": datetime.utcnow().isoformat(),
            }, {"id": "eq.%s" % job_id})
            return 0

        # Build rows
        rows = []
        for tx in transactions:
            import_h = make_import_hash(account_id, tx["date"], tx["amount"], tx["description"])
            rows.append({
                "account_id": account_id,
                "date": tx["date"],
                "description": tx["description"],
                "amount": tx["amount"],
                "balance": tx.get("balance"),
                "raw_text": tx.get("raw_text", ""),
                "source_file": basename,
                "source_page": tx.get("source_page"),
                "import_job_id": job_id,
                "import_hash": import_h,
            })

        # Insert in batches of 100
        total_inserted = 0
        batch_size = 100
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]
            try:
                inserted = supa_insert("raw_transactions", batch)
                total_inserted += len(inserted) if inserted else 0
            except Exception as e:
                log.warning("Batch insert error at offset %d: %s", i, e)

        num_skipped = len(rows) - total_inserted
        log.info("Inserted %d, skipped %d duplicates", total_inserted, num_skipped)

        supa_request("PATCH", "import_jobs", {
            "status": "completed",
            "institution": institution,
            "rows_extracted": len(transactions),
            "rows_inserted": total_inserted,
            "rows_skipped": num_skipped,
            "completed_at": datetime.utcnow().isoformat(),
        }, {"id": "eq.%s" % job_id})

        return total_inserted

    except Exception as e:
        log.error("Import failed for %s: %s", basename, e)
        import traceback
        traceback.print_exc()
        supa_request("PATCH", "import_jobs", {
            "status": "failed",
            "error_text": str(e)[:500],
            "completed_at": datetime.utcnow().isoformat(),
        }, {"id": "eq.%s" % job_id})
        return 0


# ---------------------------------------------------------------------------
# Batch scan (NAS directory structure)
# ---------------------------------------------------------------------------

def scan_nas_directory(nas_path, account_map):
    """Scan NAS tax directory and import all PDFs.

    account_map: dict mapping directory name patterns to account IDs.
    Example: {"BRSE": "967606a8-...", "CC": "0c8ac59a-..."}
    """
    if not os.path.isdir(nas_path):
        log.error("Directory not found: %s", nas_path)
        return

    total_files = 0
    total_imported = 0

    for dirpath, dirnames, filenames in os.walk(nas_path):
        pdf_files = [f for f in filenames if f.lower().endswith(".pdf")]
        if not pdf_files:
            continue

        # Determine account from directory name
        dirname = os.path.basename(dirpath)
        account_id = None
        for pattern, acct_id in account_map.items():
            if pattern.upper() in dirname.upper():
                account_id = acct_id
                break

        if not account_id:
            log.warning("No account mapping for directory: %s (skipping)", dirname)
            continue

        log.info("Processing directory: %s (%d PDFs) -> account %s", dirname, len(pdf_files), account_id[:8])

        for f in sorted(pdf_files):
            filepath = os.path.join(dirpath, f)
            total_files += 1
            count = import_transactions(filepath, account_id)
            total_imported += count

    log.info("Scan complete: %d files processed, %d transactions imported", total_files, total_imported)
    return total_imported


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def list_accounts():
    accounts = supa_request("GET", "accounts", params={"select": "*,entities(name)", "order": "name"})
    if not accounts:
        print("No accounts found.")
        return
    print("\nFinance Accounts:")
    print("-" * 90)
    for a in accounts:
        entity_name = a.get("entities", {}).get("name", "") if a.get("entities") else ""
        print("  %s  %-40s  %-10s  %s" % (a["id"][:8], a["name"], a["type"], entity_name))
    print("")


def show_status():
    jobs = supa_request("GET", "import_jobs", params={
        "select": "*",
        "order": "created_at.desc",
        "limit": "30",
    })
    if not jobs:
        print("No import jobs found.")
        return
    print("\nRecent Import Jobs:")
    print("-" * 100)
    print("%-8s  %-35s  %-12s  %6s  %6s  %6s" % (
        "ID", "Filename", "Status", "Found", "New", "Skip"))
    print("-" * 100)
    for j in jobs:
        print("%-8s  %-35s  %-12s  %6s  %6s  %6s" % (
            j["id"][:8],
            j["filename"][:35],
            j["status"],
            j.get("rows_extracted", ""),
            j.get("rows_inserted", ""),
            j.get("rows_skipped", ""),
        ))
    print("")

    # Summary
    txn_count = supa_request("GET", "raw_transactions", params={
        "select": "id",
        "limit": "0",
    })
    # Get actual count via header — use a different approach
    print("Use Supabase dashboard to check total row count.")


def main():
    parser = argparse.ArgumentParser(description="Finance Engine — Import Pipeline")
    parser.add_argument("--file", help="Path to a single PDF or CSV file")
    parser.add_argument("--dir", help="Path to directory of files to import")
    parser.add_argument("--account", help="Account ID (UUID or first 8 chars)")
    parser.add_argument("--scan", help="Scan NAS directory structure (e.g., ~/mnt/CC_NAS/BRSE_TAX\\ 2022-2024/)")
    parser.add_argument("--brse-account", help="Account ID for BRSE (Bailey personal) statements")
    parser.add_argument("--cc-account", help="Account ID for CC (Content Co-op) statements")
    parser.add_argument("--status", action="store_true", help="Show import job status")
    parser.add_argument("--list-accounts", action="store_true", help="List finance accounts")
    args = parser.parse_args()

    if args.list_accounts:
        list_accounts()
        return

    if args.status:
        show_status()
        return

    if args.file:
        if not args.account:
            print("ERROR: --account required when importing")
            sys.exit(1)
        if not os.path.exists(args.file):
            print("ERROR: File not found: %s" % args.file)
            sys.exit(1)
        # Resolve short account ID
        account_id = resolve_account_id(args.account)
        count = import_transactions(args.file, account_id)
        print("Imported %d transactions from %s" % (count, args.file))
        return

    if args.dir:
        if not args.account:
            print("ERROR: --account required when importing")
            sys.exit(1)
        if not os.path.isdir(args.dir):
            print("ERROR: Directory not found: %s" % args.dir)
            sys.exit(1)
        account_id = resolve_account_id(args.account)
        files = sorted([
            os.path.join(args.dir, f)
            for f in os.listdir(args.dir)
            if f.lower().endswith((".pdf", ".csv"))
        ])
        if not files:
            print("No PDF/CSV files found in %s" % args.dir)
            return
        total = 0
        for f in files:
            count = import_transactions(f, account_id)
            total += count
        print("\nBatch complete: %d files, %d total transactions imported" % (len(files), total))
        return

    if args.scan:
        if not args.brse_account or not args.cc_account:
            print("ERROR: --brse-account and --cc-account required for --scan")
            print("Use --list-accounts to find the account IDs")
            sys.exit(1)
        brse_id = resolve_account_id(args.brse_account)
        cc_id = resolve_account_id(args.cc_account)
        account_map = {
            "BRSE": brse_id,
            "CC_Bank": cc_id,
        }
        scan_nas_directory(args.scan, account_map)
        return

    parser.print_help()


def resolve_account_id(short_id):
    """Resolve a short account ID (first 8 chars) to full UUID."""
    if len(short_id) > 8:
        return short_id  # Already full UUID
    accounts = supa_request("GET", "accounts", params={"select": "id"})
    for a in accounts:
        if a["id"].startswith(short_id):
            return a["id"]
    log.error("Account not found for ID prefix: %s", short_id)
    sys.exit(1)


if __name__ == "__main__":
    main()
