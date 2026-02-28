import json, urllib.request, urllib.parse
from google.oauth2 import service_account
import google.auth.transport.requests

SA_FILE = "/Users/_mxappservice/.gemini/antigravity/playground/perihelion-armstrong/service_account.json"
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

ACCOUNTS = [
    "bailey@contentco-op.com",
    "caio@astrocleanings.com",
    "blaze@contentco-op.com"
]

def get_token(email):
    creds = service_account.Credentials.from_service_account_file(SA_FILE, scopes=SCOPES)
    delegated = creds.with_subject(email)
    request = google.auth.transport.requests.Request()
    delegated.refresh(request)
    return delegated.token

def gmail_get(token, path):
    url = f"https://gmail.googleapis.com/gmail/v1/users/me/{path}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}

results = {}

for email in ACCOUNTS:
    token = get_token(email)
    messages = gmail_get(token, "messages?q=is:unread&maxResults=10")
    
    account_msgs = []
    if "messages" in messages:
        for m in messages["messages"]:
            details = gmail_get(token, f"messages/{m['id']}?format=metadata&metadataHeaders=From&metadataHeaders=Subject")
            if "error" not in details:
                snippet = details.get("snippet", "")
                headers = details.get("payload", {}).get("headers", [])
                subj = ""
                sender = ""
                for h in headers:
                    if h["name"].lower() == "subject": subj = h["value"]
                    if h["name"].lower() == "from": sender = h["value"]
                account_msgs.append({
                    "id": m["id"],
                    "from": sender,
                    "subject": subj,
                    "snippet": snippet
                })
    results[email] = account_msgs

print(json.dumps(results, indent=2))
