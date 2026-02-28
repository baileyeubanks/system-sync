#!/usr/bin/env python3
"""
blaze_telegram.py — Shared Telegram send helper for all Blaze scripts.
Reads bot token and chat_id from canonical locations.
2026-02-22
"""
import os, json, urllib.request, urllib.parse

# Canonical token/chat_id paths
TOKEN_PATHS = [
    "/Users/_mxappservice/blaze-data/telegram.bot_token",
    os.path.expanduser("~/.openclaw/credentials/telegram.bot_token"),
]
ENV_PATHS = [
    "/Users/_mxappservice/blaze-data/.env",
    os.path.expanduser("~/blaze-data/.env"),
]


def _load_env():
    for path in ENV_PATHS:
        if os.path.exists(path):
            env = {}
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        env[k.strip()] = v.strip().strip('"')
            return env
    return {}


def _get_bot_token():
    for path in TOKEN_PATHS:
        if os.path.exists(path):
            with open(path) as f:
                token = f.read().strip()
                if token:
                    return token
    # fallback: read from openclaw.json accounts.main.botToken
    try:
        with open(os.path.expanduser("~/.openclaw/openclaw.json")) as f:
            c = json.load(f)
        tg = c.get("channels", {}).get("telegram", {})
        token = tg.get("accounts", {}).get("main", {}).get("botToken")
        if not token:
            token = tg.get("botToken")  # legacy fallback
        return token
    except Exception:
        return None


def _get_chat_id():
    env = _load_env()
    if "TELEGRAM_CHAT_ID" in env:
        return env["TELEGRAM_CHAT_ID"]
    # Read from openclaw.json: channels.telegram.accounts.main.allowFrom[0]
    try:
        with open(os.path.expanduser("~/.openclaw/openclaw.json")) as f:
            c = json.load(f)
        tg = c.get("channels", {}).get("telegram", {})
        # Try accounts.main.allowFrom first (most specific)
        allow_from = tg.get("accounts", {}).get("main", {}).get("allowFrom", [])
        for entry in allow_from:
            if entry and entry != "*":
                return entry
        # Fallback: top-level allowFrom
        allow_from = tg.get("allowFrom", [])
        for entry in allow_from:
            if entry and entry != "*":
                return entry
    except Exception:
        pass
    return None


def send(message, parse_mode="Markdown"):
    """
    Send a message to Bailey's Telegram via the bot.
    Returns True on success, False on failure.
    """
    bot_token = _get_bot_token()
    chat_id = _get_chat_id()

    if not bot_token:
        print("blaze_telegram: no bot token found")
        return False
    if not chat_id:
        print("blaze_telegram: no chat_id found")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": parse_mode,
    }).encode()

    try:
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            result = json.loads(r.read())
            return result.get("ok", False)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"blaze_telegram HTTP {e.code}: {body[:200]}")
        return False
    except Exception as e:
        print(f"blaze_telegram send error: {e}")
        return False


if __name__ == "__main__":
    # Quick test
    ok = send("🔧 blaze_telegram test — if you see this it works")
    print("Sent:", ok)
