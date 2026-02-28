#!/usr/bin/env python3
"""
acs_lead_score.py — Score incoming ACS leads from contacts.db
Runs: after wix_pipeline.py ingests new leads
Outputs: scores lead 0-100, sends Telegram alert if score >= 60

Scoring factors:
  - Houston zip proximity (25 pts)
  - Property type: commercial > residential (20 pts)  
  - Square footage sweet spot (20 pts)
  - Service frequency: recurring > one-time (20 pts)
  - Data completeness: name + phone + email (15 pts)

2026-02-22
"""
import sqlite3, json, os, re, urllib.request
from datetime import datetime
import sys; sys.path.insert(0, os.path.dirname(__file__)); import blaze_telegram as _tg

CONTACTS_DB = "/Users/_mxappservice/blaze-data/contacts/contacts.db"
BLAZE_API   = "http://127.0.0.1:8899"
LOG_PATH    = "/Users/_mxappservice/blaze-logs/acs-lead-score.log"
NOW = datetime.now().isoformat()

# Houston zip codes within 25 miles of core service area
HOUSTON_CORE_ZIPS = {
    "77002","77003","77004","77005","77006","77007","77008","77009","77010",
    "77011","77012","77013","77014","77015","77016","77017","77018","77019",
    "77020","77021","77022","77023","77024","77025","77026","77027","77028",
    "77029","77030","77031","77032","77033","77034","77035","77036","77037",
    "77038","77039","77040","77041","77042","77043","77044","77045","77046",
    "77047","77048","77049","77050","77051","77052","77053","77054","77055",
    "77056","77057","77058","77059","77060","77061","77062","77063","77064",
    "77065","77066","77067","77068","77069","77070","77071","77072","77073",
    "77074","77075","77076","77077","77078","77079","77080","77081","77082",
    "77083","77084","77085","77086","77087","77088","77089","77090","77091",
    "77092","77093","77094","77095","77096","77097","77098","77099",
}

COMMERCIAL_KEYWORDS = [
    "commercial", "office", "warehouse", "gym", "fitness", "medical", "clinic",
    "restaurant", "retail", "church", "school", "hotel", "apartment", "complex",
    "facility", "building", "corporate", "industrial", "manufacturing"
]


def extract_from_notes(notes):
    """Parse structured fields from Wix notes."""
    data = {}
    if not notes:
        return data
    for line in notes.split("|"):
        line = line.strip()
        if "Service:" in line:
            data["service"] = line.split("Service:")[-1].strip()
        elif "Sqft:" in line:
            try:
                data["sqft"] = int(line.split("Sqft:")[-1].strip())
            except ValueError:
                pass
        elif "Frequency:" in line:
            data["frequency"] = line.split("Frequency:")[-1].strip()
        elif "Zip:" in line:
            data["zip"] = line.split("Zip:")[-1].strip()
        elif "Estimate:" in line:
            m = re.search(r'\$(\d+)', line)
            if m:
                data["estimate"] = int(m.group(1))
    return data


def score_lead(contact):
    """Score a lead 0-100."""
    score = 0
    reasons = []
    notes = contact.get("notes") or ""
    parsed = extract_from_notes(notes)

    # 1. Zip proximity (25 pts)
    zip_code = parsed.get("zip", "")
    if zip_code[:5] in HOUSTON_CORE_ZIPS:
        score += 25
        reasons.append(f"Houston zip {zip_code[:5]} (+25)")
    elif zip_code.startswith("77") or zip_code.startswith("78"):
        score += 15
        reasons.append(f"Texas zip {zip_code[:5]} (+15)")

    # 2. Commercial vs residential (20 pts)
    service = parsed.get("service", "").lower()
    company = (contact.get("company") or "").lower()
    biz_tags = (contact.get("business_tags") or "").lower()
    combined = f"{service} {company} {biz_tags}"
    if any(kw in combined for kw in COMMERCIAL_KEYWORDS):
        score += 20
        reasons.append("Commercial property (+20)")
    elif "deep-clean" in service or "move" in service:
        score += 10
        reasons.append("Residential deep clean (+10)")
    else:
        score += 5

    # 3. Square footage sweet spot (20 pts)
    sqft = parsed.get("sqft", 0)
    if sqft >= 5000:
        score += 20; reasons.append(f"{sqft}sqft commercial (+20)")
    elif sqft >= 2000:
        score += 15; reasons.append(f"{sqft}sqft (+15)")
    elif sqft >= 1000:
        score += 10; reasons.append(f"{sqft}sqft (+10)")
    elif sqft > 0:
        score += 5

    # 4. Frequency: recurring preferred (20 pts)
    freq = parsed.get("frequency", "").lower()
    if any(w in freq for w in ["weekly","bi-weekly","monthly","recurring"]):
        score += 20; reasons.append(f"Recurring ({freq}) (+20)")
    elif "one-time" in freq:
        score += 5
    elif freq:
        score += 10

    # 5. Data completeness (15 pts)
    if contact.get("name") and "Unknown" not in contact["name"]: score += 5
    if contact.get("phone"): score += 5
    if contact.get("email"): score += 5
    if score >= 10: reasons.append("Data complete (+15)")

    return min(100, score), reasons


def estimate_deal_value(contact):
    """Rough monthly deal value estimate."""
    notes = contact.get("notes") or ""
    parsed = extract_from_notes(notes)
    estimate = parsed.get("estimate", 0)
    freq = parsed.get("frequency", "").lower()
    sqft = parsed.get("sqft", 0)

    if estimate:
        # Scale one-time by frequency to monthly recurring
        if "weekly" in freq: return estimate * 4
        if "bi-weekly" in freq: return estimate * 2
        if "monthly" in freq: return estimate
        return estimate  # one-time shown as-is

    # Estimate from sqft
    if sqft:
        rate = 0.12  # $/sqft for commercial cleaning
        return int(sqft * rate)
    return 0


def send_alert(contact, score, reasons, deal_value):
    """Send scored lead alert via Blaze API."""
    name = contact.get("name", "Unknown Lead")
    company = contact.get("company", "")
    phone = contact.get("phone", "")
    email = contact.get("email", "")

    co_str = f" @ {company}" if company else ""
    val_str = f"${deal_value:,}/mo" if deal_value else "est. TBD"

    msg_lines = [
        f"🔥 ACS LEAD SCORED [{score}/100]: *{name}{co_str}*",
        f"Value: {val_str}",
    ]
    if phone: msg_lines.append(f"📞 {phone}")
    if email: msg_lines.append(f"📧 {email}")
    msg_lines.append(f"Why: {' | '.join(reasons[:3])}")
    msg_lines.append("Reply CALL or SKIP")

    message = "\n".join(msg_lines)

    try:
        payload = json.dumps({"message": message, "channel": "telegram", "priority": "high"}).encode()
        req = urllib.request.Request(
            f"{BLAZE_API}/api/notify",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status == 200
    except Exception as e:
        print(f"Alert failed: {e}")
        return False


def run():
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    conn = sqlite3.connect(CONTACTS_DB, timeout=10)
    conn.row_factory = sqlite3.Row

    # Add acs_score column if not exists
    try:
        conn.execute("ALTER TABLE contacts ADD COLUMN acs_score INTEGER DEFAULT 0")
        conn.execute("ALTER TABLE contacts ADD COLUMN acs_scored_at TEXT")
        conn.commit()
    except Exception:
        pass

    # Score unscoredACS leads from Wix in last 24h
    leads = conn.execute("""
        SELECT * FROM contacts
        WHERE (source LIKE 'wix%')
          AND (acs_score IS NULL OR acs_score = 0)
          AND created_at >= datetime('now', '-24 hours')
        ORDER BY created_at DESC
    """).fetchall()

    print(f"Scoring {len(leads)} new ACS leads...")
    alerted = 0

    for lead in leads:
        contact = dict(lead)
        score, reasons = score_lead(contact)
        deal_value = estimate_deal_value(contact)

        # Write score back
        conn.execute(
            "UPDATE contacts SET acs_score=?, acs_scored_at=?, deal_value=? WHERE id=?",
            (score, NOW, f"${deal_value:,}" if deal_value else None, contact["id"])
        )

        log_line = f"[{score:3d}] {contact['name']:<30} {', '.join(reasons[:2])}"
        print(log_line)
        with open(LOG_PATH, "a") as f:
            f.write(f"{NOW[:10]} {log_line}\n")

        # Alert if score >= 60 (priority lead)
        if score >= 60:
            success = send_alert(contact, score, reasons, deal_value)
            if success:
                alerted += 1

    conn.commit()
    conn.close()
    print(f"\nScored {len(leads)} leads, alerted on {alerted} priority leads (score >= 60)")


if __name__ == "__main__":
    run()
