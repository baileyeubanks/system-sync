#!/usr/bin/env python3
"""
telegram_watchdog.py — Checks Telegram health every 5 min.
If down, logs and attempts ONE safe restart.
Does NOT loop-restart (that's what killed Blaze before).

Runs as: com.blaze.telegram-watchdog (every 5 min LaunchAgent)
2026-02-22
"""
import subprocess, os, json
from datetime import datetime
import sys; sys.path.insert(0, os.path.dirname(__file__)); import blaze_telegram as _tg

LOG = "/Users/_mxappservice/blaze-logs/telegram-watchdog.log"
STATE = "/Users/_mxappservice/blaze-data/watchdog-state.json"
OPENCLAW = "/usr/local/bin/openclaw"
NOW = datetime.now().isoformat()
MAX_RESTARTS_PER_HOUR = 2  # Hard cap — prevents the respawn death spiral


def log(msg):
    print(msg)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write(f"[{NOW}] {msg}\n")


def get_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception:
        return {"restarts": [], "last_down": None}


def save_state(state):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w") as f:
        json.dump(state, f, indent=2)


def check_telegram():
    """Returns True if Telegram is OK."""
    try:
        result = subprocess.run(
            ["openclaw", "health"],
            capture_output=True, text=True, timeout=15
        )
        output = result.stdout + result.stderr
        return "Telegram: ok" in output
    except Exception as e:
        log(f"health check failed: {e}")
        return False


def validate_config():
    """Returns True if openclaw.json is valid."""
    try:
        result = subprocess.run(
            ["openclaw", "doctor"],
            capture_output=True, text=True, timeout=15
        )
        output = result.stdout + result.stderr
        return "Invalid config" not in output
    except Exception:
        return False


def count_recent_restarts(state):
    """Count restarts in last 60 min."""
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(hours=1)).isoformat()
    return sum(1 for r in state.get("restarts", []) if r > cutoff)


def run():
    telegram_ok = check_telegram()

    if telegram_ok:
        return  # All good, silent exit

    # Telegram is down
    log("ALERT: Telegram is down")
    state = get_state()

    # Check restart cap
    recent = count_recent_restarts(state)
    if recent >= MAX_RESTARTS_PER_HOUR:
        log(f"SKIPPING restart — already restarted {recent}x in last hour (cap: {MAX_RESTARTS_PER_HOUR})")
        log("Manual intervention required. Check: openclaw doctor, launchctl list | grep openclaw")
        return

    # Validate config before restarting
    if not validate_config():
        log("SKIPPING restart — openclaw.json has invalid config. Fix config file first.")
        return

    # Check for duplicate supervisor (the death spiral cause)
    try:
        daemons = subprocess.run(
            ["launchctl", "list"],
            capture_output=True, text=True, timeout=5
        ).stdout
        if "ai.openclaw.gatewayd" in daemons:
            log("DANGER: System LaunchDaemon found — not restarting (would cause respawn loop)")
            log("Run: sudo launchctl bootout system/ai.openclaw.gatewayd")
            return
    except Exception:
        pass

    # Safe to restart
    log(f"Attempting restart (attempt {recent + 1}/{MAX_RESTARTS_PER_HOUR} this hour)...")
    try:
        result = subprocess.run(
            ["openclaw", "gateway", "restart"],
            capture_output=True, text=True, timeout=30
        )
        log(f"Restart issued: {result.stdout.strip()[:100]}")

        import time
        time.sleep(10)

        if check_telegram():
            log("SUCCESS: Telegram restored after restart")
        else:
            log("FAILED: Telegram still down after restart — manual check required")

    except Exception as e:
        log(f"Restart failed: {e}")

    # Record restart
    state.setdefault("restarts", []).append(NOW)
    state["last_down"] = NOW
    # Keep only last 10 restart times
    state["restarts"] = state["restarts"][-10:]
    save_state(state)


if __name__ == "__main__":
    run()
