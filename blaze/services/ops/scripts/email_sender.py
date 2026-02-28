#!/usr/bin/env python3
"""
email_sender.py — ACS Email Dispatch
Polls Supabase events table for type='send_email', sends via Gmail API
using DWD service account impersonating caio@astrocleanings.com.

From:     Customer Service <caio@astrocleanings.com>
Reply-To: customerservice@astrocleanings.com

LaunchAgent: com.blaze.email-sender (every 30s)
"""

import os
import sys
import json
import base64
import logging
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone

import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ── Config ──────────────────────────────────────────────────────────────────
SUPABASE_URL  = os.environ.get('SUPABASE_URL', 'https://briokwdoonawhxisbydy.supabase.co')
SUPABASE_KEY  = os.environ.get('SUPABASE_SERVICE_KEY', '')
SA_PATH       = os.path.expanduser(
    '~/.gemini/antigravity/playground/perihelion-armstrong/service_account.json'
)
IMPERSONATE   = 'caio@astrocleanings.com'
REPLY_TO      = 'customerservice@astrocleanings.com'
DISPLAY_NAME  = 'Customer Service'
GMAIL_SCOPES  = ['https://www.googleapis.com/auth/gmail.send']
BATCH_SIZE    = 20  # max emails per run

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_DIR = os.path.expanduser('~/blaze-logs')
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [email_sender] %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'email-sender.log')),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger(__name__)

# ── Gmail client ─────────────────────────────────────────────────────────────
def get_gmail_service():
    creds = service_account.Credentials.from_service_account_file(
        SA_PATH, scopes=GMAIL_SCOPES
    )
    delegated = creds.with_subject(IMPERSONATE)
    return build('gmail', 'v1', credentials=delegated, cache_discovery=False)


def build_message(to: str, subject: str, html: str) -> str:
    msg = MIMEMultipart('alternative')
    msg['To']       = to
    msg['From']     = f'{DISPLAY_NAME} <{IMPERSONATE}>'
    msg['Reply-To'] = REPLY_TO
    msg['Subject']  = subject
    msg.attach(MIMEText(html, 'html'))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return raw


def send_email(service, to: str, subject: str, html: str) -> bool:
    try:
        raw = build_message(to, subject, html)
        service.users().messages().send(
            userId='me', body={'raw': raw}
        ).execute()
        log.info(f'Sent → {to} | {subject}')
        return True
    except Exception as e:
        log.error(f'Send failed → {to} | {e}')
        return False


# ── Supabase helpers ─────────────────────────────────────────────────────────
def supabase_get(path: str, params: dict = None):
    headers = {
        'apikey': SUPABASE_KEY,
        'Authorization': f'Bearer {SUPABASE_KEY}',
    }
    r = requests.get(f'{SUPABASE_URL}/rest/v1/{path}', headers=headers, params=params, timeout=10)
    r.raise_for_status()
    return r.json()


def supabase_patch(path: str, data: dict, match: dict):
    headers = {
        'apikey': SUPABASE_KEY,
        'Authorization': f'Bearer {SUPABASE_KEY}',
        'Content-Type': 'application/json',
        'Prefer': 'return=minimal',
    }
    params = {k: f'eq.{v}' for k, v in match.items()}
    r = requests.patch(
        f'{SUPABASE_URL}/rest/v1/{path}',
        headers=headers, params=params,
        json=data, timeout=10
    )
    r.raise_for_status()


# ── Main loop ────────────────────────────────────────────────────────────────
def run():
    if not SUPABASE_KEY:
        log.error('SUPABASE_SERVICE_KEY not set — exiting')
        sys.exit(1)

    if not os.path.exists(SA_PATH):
        log.error(f'Service account not found at {SA_PATH}')
        sys.exit(1)

    log.info('email_sender starting...')

    try:
        gmail = get_gmail_service()
    except Exception as e:
        log.error(f'Failed to build Gmail service: {e}')
        sys.exit(1)

    # Fetch pending send_email events
    try:
        rows = supabase_get('events', {
            'type':         'eq.send_email',
            'processed_at': 'is.null',
            'select':       'id,payload,created_at',
            'order':        'created_at.asc',
            'limit':        str(BATCH_SIZE),
        })
    except Exception as e:
        log.error(f'Supabase fetch failed: {e}')
        sys.exit(1)

    if not rows:
        log.info('No pending emails.')
        return

    log.info(f'Processing {len(rows)} email(s)...')

    for row in rows:
        event_id = row['id']
        payload  = row.get('payload', {})

        to      = payload.get('to', '')
        subject = payload.get('subject', '(no subject)')
        html    = payload.get('html', '')

        if not to:
            log.warning(f'Event {event_id}: missing "to" — skipping')
        else:
            send_email(gmail, to, subject, html)

        # Mark processed regardless (don't retry bad events)
        try:
            supabase_patch('events', {
                'processed_at': datetime.now(timezone.utc).isoformat(),
            }, {'id': event_id})
        except Exception as e:
            log.warning(f'Failed to mark event {event_id} processed: {e}')

    log.info('Done.')


if __name__ == '__main__':
    run()
