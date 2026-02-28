#!/usr/bin/env python3
"""
generate_launchd.py — Generate launchd plists for all Blaze V4 cron jobs.

Advantages over crontab:
- KeepAlive: auto-restart crashed processes
- StartCalendarInterval: catches up after sleep/reboot
- StandardOutPath/ErrorPath: built-in log routing
- launchctl list: easy status checking

Generates plists to ~/Library/LaunchAgents/
"""
import os, plistlib

SCRIPTS = "/Users/_mxappservice/ACS_CC_AUTOBOT/blaze-v4/ops/scripts"
LOGS = "/Users/_mxappservice/blaze-logs"
AGENTS_DIR = os.path.expanduser("~/Library/LaunchAgents")
PYTHON = "/Library/Developer/CommandLineTools/usr/bin/python3"

# Ensure log dir exists
os.makedirs(LOGS, exist_ok=True)

JOBS = [
    {
        "label": "com.blaze.gmail-sync",
        "program": [PYTHON, "%s/gmail_contact_sync.py" % SCRIPTS],
        "interval": 300,  # every 5 min
        "workdir": SCRIPTS,
    },
    {
        "label": "com.blaze.event-stream",
        "program": [PYTHON, "%s/event_stream.py" % SCRIPTS],
        "interval": 120,  # every 2 min
        "workdir": SCRIPTS,
    },
    {
        "label": "com.blaze.morning-briefing",
        "program": [PYTHON, "%s/morning_briefing_v3.py" % SCRIPTS],
        "calendar": {"Hour": 6, "Minute": 30},
        "workdir": SCRIPTS,
    },
    {
        "label": "com.blaze.backup",
        "program": ["/bin/bash", "%s/backup_databases.sh" % SCRIPTS],
        "calendar": {"Minute": 15},  # every hour at :15
        "workdir": SCRIPTS,
    },
    {
        "label": "com.blaze.git-autosync",
        "program": ["/bin/bash", "%s/git_autosync.sh" % SCRIPTS],
        "calendar": {"Minute": 30},  # every hour at :30
        "workdir": SCRIPTS,
    },
    {
        "label": "com.blaze.verify",
        "program": [PYTHON, "%s/blaze_verify.py" % SCRIPTS],
        "calendar": {"Hour": 7, "Minute": 0},
        "workdir": SCRIPTS,
    },
    {
        "label": "com.blaze.usage-summary",
        "program": [PYTHON, "-c",
                    "import sys; sys.path.insert(0, '%s'); from blaze_helper import update_daily_summary; update_daily_summary()" % SCRIPTS],
        "calendar": {"Hour": 23, "Minute": 59},
        "workdir": SCRIPTS,
    },
]


def make_plist(job):
    label = job["label"]
    plist = {
        "Label": label,
        "ProgramArguments": job["program"],
        "WorkingDirectory": job["workdir"],
        "StandardOutPath": "%s/%s.log" % (LOGS, label.split(".")[-1]),
        "StandardErrorPath": "%s/%s.err.log" % (LOGS, label.split(".")[-1]),
        "EnvironmentVariables": {
            "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin",
            "HOME": os.path.expanduser("~"),
        },
    }

    if "interval" in job:
        plist["StartInterval"] = job["interval"]
    elif "calendar" in job:
        plist["StartCalendarInterval"] = job["calendar"]

    return plist


def main():
    print("Generating launchd plists...")
    paths = []
    for job in JOBS:
        plist = make_plist(job)
        path = "%s/%s.plist" % (AGENTS_DIR, job["label"])
        with open(path, "wb") as f:
            plistlib.dump(plist, f)
        os.chmod(path, 0o644)
        paths.append((job["label"], path))
        print("  Created: %s" % path)

    print("\nTo install:")
    print("  # Unload old plists if they exist")
    for label, path in paths:
        print("  launchctl unload %s 2>/dev/null" % path)
    print()
    print("  # Load new plists")
    for label, path in paths:
        print("  launchctl load %s" % path)
    print()
    print("  # Remove crontab")
    print("  crontab -r")
    print()
    print("  # Verify")
    print("  launchctl list | grep com.blaze")


if __name__ == "__main__":
    main()
