#!/usr/bin/env python3
import sys
import os

# Add the scripts directory to path
sys.path.insert(0, "/Users/_mxappservice/ACS_CC_AUTOBOT/blaze-v4/ops/scripts")

from google_api_manager import get_api

ACCOUNTS = [
    "bailey@contentco-op.com",
    "caio@astrocleanings.com",
    "blaze@contentco-op.com"
]

def clean_inbox(account_email):
    api = get_api()
    try:
        client = api.workspace(account_email)
        gmail = client.gmail
        
        # Archive Bank of America + Clutter (Social, Promotions, etc.)
        query = "in:inbox (from:bankofamerica.com OR category:social OR category:promotions OR category:updates OR category:forums)"
        results = gmail.users().messages().list(userId='me', q=query, maxResults=50).execute()
        messages = results.get('messages', [])
        
        if messages:
            msg_ids = [m['id'] for m in messages]
            gmail.users().messages().batchModify(
                userId='me',
                body={
                    'ids': msg_ids,
                    'removeLabelIds': ['INBOX']
                }
            ).execute()
            print(f"Archived {len(messages)} items for {account_email}")
    except Exception as e:
        # Silently fail for cron
        pass

def main():
    for acc in ACCOUNTS:
        clean_inbox(acc)

if __name__ == "__main__":
    main()
