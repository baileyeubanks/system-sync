import warnings
warnings.filterwarnings("ignore")

"""
google_api_manager.py — Unified Google API client for Blaze V4
Uses OAuth refresh tokens for all 4 accounts. No service account. No Antigravity.
Tokens live in: /Users/_mxappservice/.config/blaze/google/
"""

import os
import json
import logging
import threading
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone, timedelta
from pathlib import Path

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception, before_sleep_log

logger = logging.getLogger(__name__)

# --- Paths ----

CREDS_DIR = Path("/Users/_mxappservice/.config/blaze/google")
SECRETS_FILE = CREDS_DIR / "client_secrets.json"

TOKEN_FILES = {
    "bailey@contentco-op.com":   CREDS_DIR / "bailey_contentcoop.json",
    "caio@astrocleanings.com":   CREDS_DIR / "caio_astrocleanings.json",
    "blaze@contentco-op.com":    CREDS_DIR / "blaze_contentcoop.json",
    "baileyeubanks@gmail.com":   CREDS_DIR / "baileyeubanks_gmail.json",
}

# Fallback: extract from Antigravity for bailey@ if token file missing
ANTIGRAVITY_AUTH = Path("/Users/_mxappservice/.openclaw/agents/main/agent/auth.json")

# --- Retry ---

def _is_retryable(exc):
    if isinstance(exc, HttpError):
        return exc.resp.status in (429, 500, 502, 503, 504)
    return isinstance(exc, (TimeoutError, ConnectionError))

api_retry = retry(
    wait=wait_exponential(multiplier=2, min=2, max=60),
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(5),
    reraise=True,
)

# --- Token loader ---

def _load_credentials(email: str) -> Credentials:
    """Load and auto-refresh OAuth credentials for any account."""

    token_path = TOKEN_FILES.get(email)

    # Fallback for bailey@ -- extract from Antigravity if no token file yet
    if not token_path or not token_path.exists():
        if email == "bailey@contentco-op.com" and ANTIGRAVITY_AUTH.exists():
            logger.info("Using Antigravity token for %s", email)
            with open(ANTIGRAVITY_AUTH) as f:
                auth = json.load(f)
            ag = auth.get("google-antigravity", {})

            # Load client id/secret from client_secrets.json
            with open(SECRETS_FILE) as f:
                secrets = json.load(f)
            installed = secrets.get("installed", secrets.get("web", {}))

            creds = Credentials(
                token=ag.get("access_token"),
                refresh_token=ag.get("refresh_token"),
                token_uri="https://oauth2.googleapis.com/token",
                client_id=ag.get("client_id") or installed.get("client_id"),
                client_secret=ag.get("client_secret") or installed.get("client_secret"),
            )
        else:
            raise FileNotFoundError(
                "No token found for %s. Expected: %s" % (email, token_path)
            )
    else:
        # Load client_id/secret from secrets file for refresh
        with open(SECRETS_FILE) as f:
            secrets = json.load(f)
        installed = secrets.get("installed", secrets.get("web", {}))

        with open(token_path) as f:
            token_data = json.load(f)

        creds = Credentials(
            token=token_data.get("token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=token_data.get("client_id") or installed.get("client_id"),
            client_secret=token_data.get("client_secret") or installed.get("client_secret"),
            scopes=token_data.get("scopes"),
        )

    # Refresh if expired
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Persist refreshed token if we have a file for it
            if token_path and token_path.exists():
                with open(token_path) as f:
                    existing = json.load(f)
                existing["token"] = creds.token
                existing["expiry"] = creds.expiry.isoformat() if creds.expiry else None
                with open(token_path, "w") as f:
                    json.dump(existing, f, indent=2)
                logger.info("Token refreshed and saved for %s", email)
        else:
            raise RuntimeError("Token expired and no refresh token for %s" % email)

    return creds

# --- Workspace client ---

class WorkspaceClient:
    SERVICE_REGISTRY = {
        "gmail":    ("gmail",    "v1"),
        "calendar": ("calendar", "v3"),
        "people":   ("people",   "v1"),
        "drive":    ("drive",    "v3"),
        "docs":     ("docs",     "v1"),
        "sheets":   ("sheets",   "v4"),
        "slides":   ("slides",   "v1"),
    }

    def __init__(self, email: str):
        self._email = email
        self._lock = threading.Lock()
        self._services: Dict[str, Any] = {}
        self._creds = _load_credentials(email)
        logger.info("WorkspaceClient ready: %s", email)

    def _get(self, key: str):
        if key not in self._services:
            with self._lock:
                if key not in self._services:
                    api, ver = self.SERVICE_REGISTRY[key]
                    self._services[key] = build(
                        api, ver,
                        credentials=self._creds,
                        cache_discovery=True,
                    )
        return self._services[key]

    @property
    def gmail(self):    return self._get("gmail")
    @property
    def calendar(self): return self._get("calendar")
    @property
    def people(self):   return self._get("people")
    @property
    def drive(self):    return self._get("drive")
    @property
    def docs(self):     return self._get("docs")
    @property
    def sheets(self):   return self._get("sheets")
    @property
    def slides(self):   return self._get("slides")

    @api_retry
    def execute(self, request):
        return request.execute()

# --- YouTube client ---

class YouTubeClient:
    def __init__(self):
        self._service = None
        self._lock = threading.Lock()

    @property
    def service(self):
        if self._service is None:
            with self._lock:
                if self._service is None:
                    creds = _load_credentials("baileyeubanks@gmail.com")
                    self._service = build("youtube", "v3", credentials=creds, cache_discovery=True)
        return self._service

    @api_retry
    def execute(self, request):
        return request.execute()

# --- AI client (Gemini via API key) ---

class AIClient:
    """Uses Gemini API key for AI generation. Fast, no OAuth needed."""

    def __init__(self):
        # Read API key from OpenClaw config
        self._api_key = self._get_api_key()

    def _get_api_key(self) -> str:
        try:
            auth_path = "/Users/_mxappservice/.openclaw/agents/main/agent/auth.json"
            with open(auth_path) as f:
                auth = json.load(f)
            return auth.get("google", {}).get("key", "")
        except Exception:
            return os.environ.get("GEMINI_API_KEY", "")

    def generate(self, prompt: str, system: str = None, max_tokens: int = 1000) -> str:
        """Call Gemini Flash directly via REST -- no SDK needed."""
        import urllib.request

        if not self._api_key:
            raise RuntimeError("No Gemini API key found")

        messages = []
        if system:
            messages.append({"role": "user", "parts": [{"text": system}]})
            messages.append({"role": "model", "parts": [{"text": "Understood."}]})
        messages.append({"role": "user", "parts": [{"text": prompt}]})

        payload = json.dumps({
            "contents": messages,
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": 0.3,
            }
        }).encode()

        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=%s" % self._api_key
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})

        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())

        return result["candidates"][0]["content"]["parts"][0]["text"]

# --- Master orchestrator ---

class BlazeGoogleAPI:
    def __init__(self):
        self._clients: Dict[str, WorkspaceClient] = {}
        self._youtube = YouTubeClient()
        self._ai = AIClient()
        self._lock = threading.Lock()

    def workspace(self, email: str) -> WorkspaceClient:
        if email not in self._clients:
            with self._lock:
                if email not in self._clients:
                    self._clients[email] = WorkspaceClient(email)
        return self._clients[email]

    @property
    def bailey(self):  return self.workspace("bailey@contentco-op.com")
    @property
    def caio(self):    return self.workspace("caio@astrocleanings.com")
    @property
    def blaze(self):   return self.workspace("blaze@contentco-op.com")
    @property
    def personal(self): return self.workspace("baileyeubanks@gmail.com")
    @property
    def youtube(self): return self._youtube
    @property
    def ai(self):      return self._ai

# --- Singleton ---

_instance: Optional[BlazeGoogleAPI] = None

def get_api() -> BlazeGoogleAPI:
    global _instance
    if _instance is None:
        _instance = BlazeGoogleAPI()
    return _instance

# --- Ready-to-use functions (used by briefing + other scripts) ---

def get_todays_events(email: str = "bailey@contentco-op.com") -> List[Dict]:
    api = get_api()
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    end = now.replace(hour=23, minute=59, second=59, microsecond=0).isoformat()
    try:
        result = api.workspace(email).execute(
            api.workspace(email).calendar.events().list(
                calendarId="primary",
                timeMin=start, timeMax=end,
                singleEvents=True, orderBy="startTime", maxResults=10
            )
        )
        out = []
        for e in result.get("items", []):
            start_dt = e["start"].get("dateTime", e["start"].get("date", ""))
            if "T" in start_dt:
                dt = datetime.fromisoformat(start_dt.replace("Z", "+00:00"))
                time_str = dt.astimezone().strftime("%-I:%M %p")
            else:
                time_str = "All day"
            out.append({"time": time_str, "title": e.get("summary", "(no title)"), "location": e.get("location", "")})
        return out
    except Exception as e:
        logger.error("Calendar error (%s): %s", email, e)
        return []

def get_recent_emails(email: str = "bailey@contentco-op.com", max_results: int = 5, query: str = "is:unread is:inbox -category:promotions -category:updates") -> List[Dict]:
    api = get_api()
    try:
        msgs = api.workspace(email).execute(
            api.workspace(email).gmail.users().messages().list(userId="me", q=query, maxResults=max_results)
        )
        out = []
        for msg in msgs.get("messages", []):
            detail = api.workspace(email).execute(
                api.workspace(email).gmail.users().messages().get(
                    userId="me", id=msg["id"], format="metadata",
                    metadataHeaders=["From", "Subject", "Date"]
                )
            )
            headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
            sender_raw = headers.get("From", "")
            sender = sender_raw.split("<")[0].strip().strip('"') or sender_raw
            out.append({
                "id": msg["id"],
                "from": sender,
                "subject": headers.get("Subject", "(no subject)"),
                "snippet": detail.get("snippet", "")[:100],
            })
        return out
    except Exception as e:
        logger.error("Gmail error (%s): %s", email, e)
        return []

def ai_generate(prompt: str, system: str = None) -> str:
    """Drop-in replacement for ask_blaze() for AI generation."""
    try:
        return get_api().ai.generate(prompt=prompt, system=system)
    except Exception as e:
        logger.error("AI generation error: %s", e)
        return "[AI unavailable: %s]" % e

def test_all_connections() -> Dict[str, str]:
    results = {}
    api = get_api()

    for account, label in [
        ("bailey@contentco-op.com", "calendar_bailey"),
        ("caio@astrocleanings.com", "calendar_caio"),
        ("blaze@contentco-op.com", "calendar_blaze"),
    ]:
        try:
            events = get_todays_events(account)
            results[label] = "OK -- %d events" % len(events)
        except Exception as e:
            results[label] = "FAIL: %s" % e

    for account, label in [
        ("bailey@contentco-op.com", "gmail_bailey"),
        ("caio@astrocleanings.com", "gmail_caio"),
        ("baileyeubanks@gmail.com", "gmail_personal"),
    ]:
        try:
            emails = get_recent_emails(account, max_results=1)
            results[label] = "OK"
        except Exception as e:
            results[label] = "FAIL: %s" % e

    try:
        r = ai_generate("Say exactly: Blaze online", system="Return only what is asked.")
        results["gemini_ai"] = "OK -- %s" % r.strip()[:30]
    except Exception as e:
        results["gemini_ai"] = "FAIL: %s" % e

    try:
        subs = api.youtube.execute(
            api.youtube.service.subscriptions().list(part="snippet", mine=True, maxResults=1)
        )
        results["youtube"] = "OK"
    except Exception as e:
        results["youtube"] = "FAIL: %s" % e

    return results

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING)
    print("=== Blaze Google API -- Connection Test ===\n")
    results = test_all_connections()
    for service, status in results.items():
        icon = "OK" if status.startswith("OK") else "FAIL"
        print("  [%s] %s: %s" % (icon, service, status))
