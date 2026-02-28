#!/usr/bin/env python3
"""
telegram_bot_poller.py — Multi-bot Telegram group responder
Polls all three ACS bots and routes @mentions to OpenClaw agents.

Bots:
  @blazenbailey_bot              → main agent     (Blaze / Bailey)
  @agentastro_bot                → acs-worker     (Agent Astro / Caio)
  @agentcc_creativedirectorbot   → cc-worker      (Agent CC / Bailey)

Group: -1003808234745 (@ACS_CC_TEAM)
"""

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

LOG_FILE = os.path.expanduser('~/blaze-logs/telegram_bot_poller.log')
os.makedirs(os.path.expanduser('~/blaze-logs'), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s [%(name)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE),
    ],
)

OPENCLAW = '/usr/local/bin/openclaw'
OPENCLAW_ENV = {
    **os.environ,
    'PATH': '/usr/local/bin:/opt/homebrew/bin:' + os.environ.get('PATH', ''),
}

BOT_CONFIGS = [
    {
        'name': 'blazenbailey_bot',
        'username': 'blazenbailey_bot',
        'token': '8466222141:AAHGQFL8xe39JAw5sjD3nrNOUs-RBE8SoV8',
        'agent': 'main',
    },
    {
        'name': 'agentastro_bot',
        'username': 'agentastro_bot',
        'token': '8328122967:AAERigUAeIU4DDkRZoW5Hn-QFj5aUUaDC20',
        'agent': 'acs-worker',
    },
    {
        'name': 'agentcc_creativedirectorbot',
        'username': 'agentcc_creativedirectorbot',
        'token': '8087232756:AAEEbKJxFpnNwC9NmMPcLuRqL5Fg07MNrOU',
        'agent': 'cc-worker',
    },
]

ALLOWED_CHAT_IDS = {-1003808234745}   # @ACS_CC_TEAM
MAX_RESPONSE_CHARS = 4000
OPENCLAW_TIMEOUT = 120                # seconds per agent call


# ── Telegram helpers ──────────────────────────────────────────────────────────

def _tg(token, method, data=None, read_timeout=40):
    url = 'https://api.telegram.org/bot' + token + '/' + method
    if data:
        payload = json.dumps(data).encode()
        req = urllib.request.Request(
            url, data=payload, headers={'Content-Type': 'application/json'}
        )
    else:
        req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=read_timeout) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        return {'ok': False, 'error': str(exc)}


def get_updates(token, offset=None, timeout=30):
    params = {'timeout': timeout, 'allowed_updates': json.dumps(['message'])}
    if offset is not None:
        params['offset'] = offset
    url = ('https://api.telegram.org/bot' + token +
           '/getUpdates?' + urllib.parse.urlencode(params))
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout + 10) as resp:
            return json.loads(resp.read())
    except urllib.error.URLError as exc:
        return {'ok': False, 'error': str(exc)}
    except Exception as exc:
        return {'ok': False, 'error': str(exc)}


def send_message(token, chat_id, text, reply_to=None):
    data = {
        'chat_id': chat_id,
        'text': text[:MAX_RESPONSE_CHARS],
        'parse_mode': 'HTML',
        'disable_web_page_preview': True,
    }
    if reply_to:
        data['reply_to_message_id'] = reply_to
    return _tg(token, 'sendMessage', data)


def send_action(token, chat_id, action='typing'):
    _tg(token, 'sendChatAction', {'chat_id': chat_id, 'action': action})


# ── OpenClaw ──────────────────────────────────────────────────────────────────

def call_openclaw(agent_id, message_text, log):
    try:
        result = subprocess.run(
            [OPENCLAW, 'agent', '--agent', agent_id,
             '--message', message_text, '--json'],
            capture_output=True,
            text=True,
            timeout=OPENCLAW_TIMEOUT,
            env=OPENCLAW_ENV,
        )
        if result.returncode != 0:
            log.error('openclaw exit %d: %s', result.returncode, result.stderr[:500])
            return None
        raw = result.stdout.strip()
        if not raw:
            return None
        try:
            data = json.loads(raw)
            # Try common response key names
            for key in ('response', 'text', 'content', 'message', 'output'):
                if key in data and isinstance(data[key], str):
                    return data[key]
            return str(data)
        except json.JSONDecodeError:
            return raw
    except subprocess.TimeoutExpired:
        log.error('openclaw timed out after %ds', OPENCLAW_TIMEOUT)
        return None
    except Exception as exc:
        log.error('openclaw error: %s', exc)
        return None


# ── Message parsing ───────────────────────────────────────────────────────────

def extract_prompt(message, bot_username, bot_id):
    """
    Return the user's prompt if this message is directed at our bot, else None.
    Accepts @mention anywhere in the text, or a direct reply to one of our messages.
    """
    text = message.get('text') or message.get('caption') or ''

    # Check if this is a reply to our bot's message
    reply = message.get('reply_to_message') or {}
    reply_from = reply.get('from') or {}
    is_reply = str(reply_from.get('id', '')) == str(bot_id)

    mention = '@' + bot_username
    has_mention = mention.lower() in text.lower()

    # Also walk entities in case Telegram marks mentions differently
    if not has_mention:
        for ent in (message.get('entities') or []):
            if ent.get('type') == 'mention':
                start = ent['offset']
                chunk = text[start:start + ent['length']]
                if chunk.lower() == mention.lower():
                    has_mention = True
                    break

    if not has_mention and not is_reply:
        return None

    # Strip the @mention token from the text
    cleaned = re.sub(re.escape(mention), '', text, flags=re.IGNORECASE)
    cleaned = cleaned.strip().lstrip(',').strip()
    return cleaned or '(mentioned without text)'


# ── Bot poller (one thread per bot) ──────────────────────────────────────────

def poll_bot(config):
    token = config['token']
    agent_id = config['agent']
    username = config['username']
    log = logging.getLogger(config['name'])

    log.info('Starting poller for @%s → agent:%s', username, agent_id)

    # Resolve our bot's numeric ID
    me_resp = _tg(token, 'getMe')
    if not me_resp.get('ok'):
        log.error('getMe failed: %s', me_resp)
        return
    bot_id = me_resp['result']['id']
    log.info('@%s bot_id=%d', username, bot_id)

    offset = None

    while True:
        try:
            resp = get_updates(token, offset=offset, timeout=30)
        except Exception as exc:
            log.warning('get_updates exception: %s', exc)
            time.sleep(5)
            continue

        if not resp.get('ok'):
            log.warning('getUpdates not ok: %s',
                        resp.get('error') or resp.get('description'))
            time.sleep(5)
            continue

        for update in resp.get('result') or []:
            offset = update['update_id'] + 1

            message = update.get('message')
            if not message:
                continue

            chat_id = (message.get('chat') or {}).get('id')
            if chat_id not in ALLOWED_CHAT_IDS:
                continue

            message_id = message.get('message_id')
            sender = message.get('from') or {}
            sender_name = (sender.get('first_name') or
                           sender.get('username') or 'unknown')

            prompt = extract_prompt(message, username, bot_id)
            if not prompt:
                continue

            log.info('Request from %s: %.120s', sender_name, prompt)
            send_action(token, chat_id, 'typing')

            # Spin up a handler thread so polling loop doesn't stall
            def handle(tok=token, cid=chat_id, mid=message_id,
                       p=prompt, aid=agent_id, l=log):
                l.info('Calling agent:%s ...', aid)
                response = call_openclaw(aid, p, l)
                if response:
                    result = send_message(tok, cid, response, reply_to=mid)
                    if result.get('ok'):
                        l.info('Replied (%d chars)', len(response))
                    else:
                        l.error('sendMessage failed: %s', result)
                else:
                    l.warning('No response from agent:%s', aid)

            threading.Thread(target=handle, daemon=True).start()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    threads = []
    for cfg in BOT_CONFIGS:
        t = threading.Thread(
            target=poll_bot, args=(cfg,), daemon=False, name=cfg['name']
        )
        t.start()
        threads.append(t)
    for t in threads:
        t.join()


if __name__ == '__main__':
    main()
