"""
quote_webhook.py — HTTP webhook receiver for ACS quote leads
Receives POST from astrocleanings.com quote engine, then:
  1. Stores lead in SQLite (leads.db)
  2. Creates/updates contact in Wix CRM via wix_api_manager
  3. Sends notification to Blaze knowledge system
  4. Returns JSON success/error

Runs on port 8089 via Tailscale Funnel for public access.
Python 3.9 compatible.
"""

import json, os, sys, sqlite3, logging, time
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from threading import Thread

# Add scripts dir to path for wix_api_manager
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

DATA_DIR = "/Users/_mxappservice/blaze-data"
LEADS_DB = os.path.join(DATA_DIR, "leads", "leads.db")
LOG_FILE = os.path.join(DATA_DIR, "logs", "quote_webhook.log")

# Logging
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
os.makedirs(os.path.dirname(LEADS_DB), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SQLite setup
# ---------------------------------------------------------------------------

def init_db():
    conn = sqlite3.connect(LEADS_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            phone TEXT,
            email TEXT,
            zip TEXT,
            service_type TEXT,
            sqft INTEGER,
            bedrooms INTEGER,
            bathrooms REAL,
            frequency TEXT,
            next_day BOOLEAN,
            addons TEXT,
            estimate REAL,
            estimate_low REAL,
            estimate_high REAL,
            wix_contact_id TEXT,
            status TEXT DEFAULT 'new',
            source TEXT DEFAULT 'website',
            raw_json TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    conn.commit()
    conn.close()
    logger.info("Leads DB initialized at %s" % LEADS_DB)


# ---------------------------------------------------------------------------
# Wix CRM integration
# ---------------------------------------------------------------------------

def push_to_wix_crm(lead):
    """Create or update contact in Wix ACS CRM. Returns wix_contact_id."""
    try:
        from wix_api_manager import get_wix
        api = get_wix()
        acs = api.acs

        name = lead.get("name", "").strip()
        parts = name.split(" ", 1) if name else ["Website", "Visitor"]
        first = parts[0] if parts else "Website"
        last = parts[1] if len(parts) > 1 else "Visitor"

        email = lead.get("email", "")
        phone = lead.get("phone", "")

        result = acs.create_contact(
            first_name=first,
            last_name=last,
            email=email if email else None,
            phone=phone if phone else None,
        )

        contact = result.get("contact", result)
        wix_id = contact.get("id", "")

        if wix_id:
            logger.info("Wix contact created: %s (%s %s)" % (wix_id, first, last))

            # Add label for quote request
            try:
                acs.post("/contacts/v4/contacts/%s/labels" % wix_id, {
                    "labelKeys": ["custom.quote-request"]
                })
            except Exception as e:
                logger.warning("Could not add label: %s" % e)

        return wix_id

    except Exception as e:
        logger.error("Wix CRM push failed: %s" % e)
        return ""


# ---------------------------------------------------------------------------
# Lead storage
# ---------------------------------------------------------------------------

def store_lead(lead, wix_id=""):
    """Store lead in SQLite and return lead ID."""
    conn = sqlite3.connect(LEADS_DB)
    now = datetime.now().isoformat()

    addons_str = json.dumps(lead.get("addons", []))
    estimate_range = lead.get("estimateRange", {})

    cursor = conn.execute("""
        INSERT INTO leads (
            name, phone, email, zip, service_type, sqft,
            bedrooms, bathrooms, frequency, next_day,
            addons, estimate, estimate_low, estimate_high,
            wix_contact_id, status, source, raw_json,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        lead.get("name", ""),
        lead.get("phone", ""),
        lead.get("email", ""),
        lead.get("zip", ""),
        lead.get("serviceType", ""),
        lead.get("sqft", 0),
        lead.get("bedrooms", 0),
        lead.get("bathrooms", 0),
        lead.get("frequency", ""),
        lead.get("nextDay", False),
        addons_str,
        lead.get("estimate", 0),
        estimate_range.get("low", 0),
        estimate_range.get("high", 0),
        wix_id,
        "new",
        "website",
        json.dumps(lead),
        now, now,
    ))

    lead_id = cursor.lastrowid
    conn.commit()
    conn.close()

    logger.info("Lead #%d stored: %s — $%s" % (
        lead_id,
        lead.get("name", "anonymous"),
        lead.get("estimate", "?"),
    ))
    return lead_id


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------

class QuoteHandler(BaseHTTPRequestHandler):

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json_response(self, code, data):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_GET(self):
        if self.path == "/webhook/quote" or self.path == "/webhook/quote/":
            self._json_response(200, {
                "status": "ok",
                "service": "ACS Quote Webhook",
                "version": "1.0",
            })
        elif self.path == "/webhook/quote/leads":
            # Return recent leads (for morning briefing)
            try:
                conn = sqlite3.connect(LEADS_DB)
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM leads WHERE status = 'new' ORDER BY created_at DESC LIMIT 20"
                ).fetchall()
                conn.close()
                leads = [dict(r) for r in rows]
                self._json_response(200, {"leads": leads, "count": len(leads)})
            except Exception as e:
                self._json_response(500, {"error": str(e)})
        else:
            self._json_response(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/webhook/quote" and self.path != "/webhook/quote/":
            self._json_response(404, {"error": "not found"})
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            lead = json.loads(body)
        except Exception as e:
            self._json_response(400, {"error": "Invalid JSON: %s" % e})
            return

        # Validate
        if not lead.get("phone") and not lead.get("email"):
            self._json_response(400, {"error": "phone or email required"})
            return

        logger.info("Received lead: %s (%s)" % (
            lead.get("name", "?"),
            lead.get("serviceType", "?"),
        ))

        # Push to Wix CRM (in background thread to not block response)
        wix_id_holder = [""]

        def wix_push():
            wix_id_holder[0] = push_to_wix_crm(lead)
            # Update the lead record with wix_id
            if wix_id_holder[0]:
                try:
                    conn = sqlite3.connect(LEADS_DB)
                    conn.execute(
                        "UPDATE leads SET wix_contact_id = ? WHERE id = ?",
                        (wix_id_holder[0], lead_id)
                    )
                    conn.commit()
                    conn.close()
                except Exception:
                    pass

        # Store lead first
        lead_id = store_lead(lead)

        # Wix push in background
        t = Thread(target=wix_push, daemon=True)
        t.start()

        self._json_response(200, {
            "status": "received",
            "leadId": lead_id,
            "message": "Quote lead captured. We'll be in touch!",
        })

    def log_message(self, format, *args):
        # Suppress default HTTP log spam
        pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    PORT = 8089
    init_db()
    server = HTTPServer(("127.0.0.1", PORT), QuoteHandler)
    logger.info("ACS Quote Webhook running on port %d" % PORT)
    logger.info("Endpoints:")
    logger.info("  POST /webhook/quote     — receive lead")
    logger.info("  GET  /webhook/quote      — health check")
    logger.info("  GET  /webhook/quote/leads — recent leads")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down webhook server")
        server.shutdown()


if __name__ == "__main__":
    main()
