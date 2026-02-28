#!/usr/bin/env python3
"""
disk_monitor.py — Check disk usage, alert via Telegram if threshold exceeded.
Runs nightly at 11:50 PM via com.blaze.disk-monitor LaunchAgent.
"""
from __future__ import annotations
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

WARN_PERCENT = 80
CRIT_PERCENT = 90
TELEGRAM_TARGET = "telegram:7747110667"
OPENCLAW = "/usr/local/bin/openclaw"
PATH_ENV = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"


def ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def send_telegram(msg: str) -> None:
    env = os.environ.copy()
    env["PATH"] = PATH_ENV + ":" + env.get("PATH", "")
    try:
        subprocess.run(
            [
                OPENCLAW, "message", "send",
                "--channel", "telegram",
                "--account", "main",
                "--target", TELEGRAM_TARGET,
                "--message", msg,
            ],
            env=env,
            timeout=30,
        )
    except Exception as exc:
        print("[%s] Telegram alert failed: %s" % (ts(), exc), flush=True)


def main():
    home = Path.home()
    stat = shutil.disk_usage(str(home))
    total_gb = stat.total / (1024 ** 3)
    used_gb = stat.used / (1024 ** 3)
    free_gb = stat.free / (1024 ** 3)
    pct = (stat.used / stat.total) * 100

    print(
        "[%s] Disk: %.1f%% used (%.1fGB free / %.1fGB total)"
        % (ts(), pct, free_gb, total_gb),
        flush=True,
    )

    if pct >= CRIT_PERCENT:
        msg = (
            "CRITICAL: Mac Mini disk %.1f%% full — only %.1fGB free (%.1fGB total)."
            " Action required." % (pct, free_gb, total_gb)
        )
        send_telegram(msg)
        print("[%s] CRITICAL alert sent." % ts(), flush=True)
    elif pct >= WARN_PERCENT:
        msg = (
            "WARNING: Mac Mini disk %.1f%% full — %.1fGB free (%.1fGB total)."
            " Consider cleanup." % (pct, free_gb, total_gb)
        )
        send_telegram(msg)
        print("[%s] WARNING alert sent." % ts(), flush=True)


if __name__ == "__main__":
    main()
