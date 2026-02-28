"""
imsg_relay.py — GUI-context iMessage relay daemon.

Watches /tmp/imsg_queue/ for .json send requests.
Runs as a LaunchAgent (gui/502) so it has access to Messages.app.

Request format:
  {"to": "+15013515927", "text": "message"}         — new conversation (default account)
  {"chat_id": "17", "text": "reply text"}            — reply in existing chat (correct account)
"""
import os, json, time, subprocess, glob, sys

QUEUE_DIR = "/tmp/imsg_queue"
IMSG      = "/opt/homebrew/bin/imsg"
LOG       = "/Users/_mxappservice/blaze-logs/imsg-relay.log"
POLL_INTERVAL   = 1   # seconds
WARMUP_INTERVAL = 20  # activate Messages.app every N seconds to keep it responsive

_last_warmup = 0


def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    sys.stdout.write(line)
    sys.stdout.flush()
    try:
        with open(LOG, "a") as f:
            f.write(line)
    except Exception:
        pass


def warmup():
    """Activate Messages.app to keep it responsive to AppleEvents."""
    global _last_warmup
    now = time.time()
    if now - _last_warmup < WARMUP_INTERVAL:
        return
    try:
        subprocess.run(
            ["osascript", "-e", 'tell application "Messages" to activate'],
            capture_output=True, timeout=5,
        )
        _last_warmup = now
    except Exception:
        pass


SSH = ["ssh",
       "-o", "BatchMode=yes",
       "-o", "StrictHostKeyChecking=no",
       "-o", "ConnectTimeout=5",
       "localhost"]


def get_chat_guid(chat_id):
    """Look up chat GUID via SSH loopback (sshd has FDA to read chat.db)."""
    try:
        result = subprocess.run(
            SSH + [f'sqlite3 ~/Library/Messages/chat.db "SELECT guid FROM chat WHERE ROWID={chat_id};"'],
            capture_output=True, text=True, timeout=10
        )
        return result.stdout.strip()
    except Exception as e:
        log(f"GUID lookup failed: {e}")
        return ""


def send_message(to, text, chat_id=None):
    warmup()
    time.sleep(0.5)  # brief pause after activate

    if chat_id:
        guid = get_chat_guid(chat_id)
        if guid:
            cmd = [IMSG, "send", "--chat-identifier", guid, "--text", text]
            label = f"chat:{chat_id}({guid})"
        else:
            # Fallback to --to if GUID lookup fails
            cmd = [IMSG, "send", "--to", to, "--text", text]
            label = to
    else:
        cmd = [IMSG, "send", "--to", to, "--text", text]
        label = to

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
    )
    try:
        out, err = proc.communicate(timeout=12)
        if proc.returncode not in (0, -9):
            log(f"FAIL {label}: {err.decode().strip()}")
            return False
        log(f"SENT {label}: {text[:80]}")
        return True
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        log(f"SENT {label} (ack timeout — expected): {text[:80]}")
        return True


def process_queue():
    files = sorted(glob.glob(os.path.join(QUEUE_DIR, "*.json")))
    for fpath in files:
        try:
            with open(fpath) as f:
                req = json.load(f)
            to      = req.get("to", "").strip()
            text    = req.get("text", "").strip()
            chat_id = str(req.get("chat_id", "")).strip() if req.get("chat_id") else ""

            if not text or (not to and not chat_id):
                log(f"SKIP malformed: {fpath}")
                os.remove(fpath)
                continue
            os.remove(fpath)
            send_message(to, text, chat_id=chat_id or None)
        except Exception as e:
            log(f"ERROR {fpath}: {e}")
            try:
                os.remove(fpath)
            except Exception:
                pass


def main():
    os.makedirs(QUEUE_DIR, exist_ok=True)
    os.chmod(QUEUE_DIR, 0o777)
    log("imsg_relay started")
    warmup()
    while True:
        try:
            warmup()
            process_queue()
        except Exception as e:
            log(f"LOOP ERROR: {e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
