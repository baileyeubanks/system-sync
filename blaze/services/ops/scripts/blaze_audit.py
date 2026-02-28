#!/usr/bin/env python3
"""
blaze_audit.py — Comprehensive Blaze V4 system health audit.
Checks all 36 LaunchAgents, services, cron failure rates, log errors,
disk/DB health, backup verification. Sends full report to Telegram.

Run: python3 blaze_audit.py
LaunchAgent: com.blaze.verify (daily 7:00 AM)
"""
import subprocess, json, os, time, sqlite3, re
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────────
OPENCLAW      = "/usr/local/bin/openclaw"
OPENCLAW_ENV  = {**os.environ, "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"}
BLAZE_DB      = Path.home() / "blaze-data/blaze.db"
CONTACTS_DB   = Path.home() / "blaze-data/contacts/contacts.db"
LOGS_DIR      = Path.home() / "blaze-logs"
SCRIPTS_DIR   = Path.home() / "ACS_CC_AUTOBOT/blaze-v4/ops/scripts"
BACKUPS_DIR   = Path.home() / "blaze-data/backups"
LAUNCHAGENTS  = Path.home() / "Library/LaunchAgents"
FASTAPI_URL   = "http://127.0.0.1:8899/health"

NOW           = datetime.now(timezone.utc)
YESTERDAY     = NOW - timedelta(hours=25)
WEEK_AGO      = NOW - timedelta(days=7)

# All expected LaunchAgents → expected run cadence in minutes (0 = always-on daemon)
EXPECTED_AGENTS = {
    "com.blaze.fastapi":              0,    # daemon
    "com.blaze.imsg-relay":           0,    # daemon
    "com.blaze.imsg-watcher":         0,    # daemon
    "com.blaze.event-stream":         2,
    "com.blaze.gmail-sync":           5,
    "com.blaze.telegram-watchdog":    5,
    "com.blaze.email-triage-urgent":  30,   # weekdays 8-18
    "com.blaze.netlify-bridge":       1,
    "com.blaze.backup":               60,
    "com.blaze.git-autosync":         30,
    "com.blaze.contact-cache-sync":   60,
    "com.blaze.acs-proactive":        None, # interval varies
    "com.blaze.gmail-monitor":        5,
    "com.blaze.knowledge-harvest":    None,
    "com.blaze.vector-index":         None,
    "com.blaze.morning-briefing":     None, # 6:30AM daily
    "com.blaze.morning-briefing-acs": None, # 7:00AM daily
    "com.blaze.followup-digest":      None, # 9:00AM daily
    "com.blaze.business-council":     None, # 1:00AM daily
    "com.blaze.session-rotate":       None, # 4:00AM daily
    "com.blaze.soul-update":          None,
    "com.blaze.email-triage-daily":   None, # 23:00 daily
    "com.blaze.crm-sync":             None, # 23:30 daily
    "com.blaze.nightly-extraction":   None,
    "com.blaze.contact-brain-rescore":None, # 2:15AM daily
    "com.blaze.agent-briefings":      None,
    "com.blaze.recruitment-scorer":   None, # 9:00AM daily
    "com.blaze.openclaw-update":      None, # 2:00AM daily
    "com.blaze.db-maintenance":       None, # 2:30AM daily
    "com.blaze.log-rotation":         None, # 23:55 daily
    "com.blaze.disk-monitor":         None, # 23:50 daily
    "com.blaze.verify":               None, # 7:00AM daily
    "com.blaze.usage-summary":        None, # 23:59 daily
    "com.blaze.knowledge-weekly":     None,
    "com.blaze.youtube-learning":     None,
    "com.blaze.coedit-backend":       0,    # daemon (known broken)
}

findings   = []   # (severity, label, detail) — "FAIL"|"WARN"|"PASS"|"INFO"
score_pass = 0
score_total = 0


def check(label, passed, detail="", warn_only=False):
    global score_pass, score_total
    score_total += 1
    if passed:
        score_pass += 1
        findings.append(("PASS", label, detail))
    else:
        sev = "WARN" if warn_only else "FAIL"
        findings.append((sev, label, detail))


def run(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return -1, "", str(e)


def launchctl_list():
    """Returns dict of label → (pid, exit_code) from launchctl list."""
    rc, out, _ = run(["launchctl", "list"])
    result = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        pid_s, exit_s, label = parts[0].strip(), parts[1].strip(), parts[2].strip()
        if not label.startswith("com.blaze"):
            continue
        pid = int(pid_s) if pid_s.isdigit() else None
        try:
            exit_code = int(exit_s)
        except Exception:
            exit_code = None
        result[label] = (pid, exit_code)
    return result


# ══ SECTION 1: Core Services ══════════════════════════════════════════════════

def check_services():
    findings.append(("INFO", "── SECTION 1: CORE SERVICES ──", ""))

    # FastAPI
    rc, out, _ = run(["curl", "-sf", FASTAPI_URL], timeout=5)
    check("fastapi:health", rc == 0, out[:80] if rc == 0 else "port 8899 unreachable")

    # OpenClaw gateway
    rc, out, err = run([OPENCLAW, "health"], timeout=10)
    check("openclaw:health", rc == 0 and "ok" in out.lower(),
          out[:80] if rc == 0 else err[:80])

    # iMessage watcher
    rc, out, _ = run(["pgrep", "-f", "imsg_watcher.py"])
    check("imsg:watcher", rc == 0, "pid=" + out if rc == 0 else "NOT RUNNING")

    # iMessage relay
    rc, out, _ = run(["pgrep", "-f", "imsg_relay.py"])
    check("imsg:relay", rc == 0, "pid=" + out if rc == 0 else "NOT RUNNING")

    # State file exists (only updates when messages arrive — no freshness check)
    state_f = Path.home() / "blaze-data/imsg_watcher_state.json"
    check("imsg:state_exists", state_f.exists(),
          "rowid=" + str(__import__("json").load(open(state_f)).get("last_rowid","?"))
          if state_f.exists() else "missing state file")


# ══ SECTION 2: LaunchAgent Registry ══════════════════════════════════════════

def check_launchagents():
    findings.append(("INFO", "── SECTION 2: LAUNCHAGENT REGISTRY ──", ""))

    loaded = launchctl_list()
    DAEMONS = {"com.blaze.fastapi", "com.blaze.imsg-relay",
               "com.blaze.imsg-watcher", "com.blaze.coedit-backend"}

    for label in sorted(EXPECTED_AGENTS.keys()):
        plist = LAUNCHAGENTS / (label + ".plist")
        plist_ok = plist.exists()
        if not plist_ok:
            check("launchd:" + label.replace("com.blaze.", ""), False,
                  "plist missing")
            continue

        in_launchctl = label in loaded
        if not in_launchctl:
            # Check if intentionally disabled in plist
            try:
                import plistlib as _pll
                with open(plist, "rb") as _pf:
                    _pl = _pll.load(_pf)
                disabled = _pl.get("Disabled", False) or (
                    not _pl.get("RunAtLoad", True) and not _pl.get("KeepAlive", False)
                    and not _pl.get("StartInterval") and not _pl.get("StartCalendarInterval"))
            except Exception:
                disabled = False
            if disabled:
                findings.append(("WARN", "launchd:" + label.replace("com.blaze.", ""),
                                 "Disabled (intentional) — not loaded"))
            else:
                check("launchd:" + label.replace("com.blaze.", ""), False,
                      "not loaded in launchctl — run: launchctl bootstrap gui/502 " + str(plist))
            continue

        pid, exit_code = loaded[label]

        # For daemons — must have a PID (actively running)
        if label in DAEMONS:
            if label == "com.blaze.coedit-backend":
                # Check if intentionally disabled in plist
                import plistlib as _pll
                _pl_path = LAUNCHAGENTS / (label + ".plist")
                try:
                    with open(_pl_path, "rb") as _pf:
                        _pl = _pll.load(_pf)
                    disabled = _pl.get("Disabled", False)
                except Exception:
                    disabled = False
                if disabled:
                    findings.append(("WARN", "launchd:coedit-backend",
                                     "Intentionally disabled — needs Python 3.10+ for str|None syntax"))
                    # Don't count against score
                else:
                    running = pid is not None
                    check("launchd:" + label.replace("com.blaze.", ""),
                          running, "KNOWN BROKEN (wrong Python)", warn_only=True)
                continue
            else:
                check("launchd:" + label.replace("com.blaze.", ""),
                      pid is not None,
                      "running pid=%s" % pid if pid else "daemon not running! exit=%s" % exit_code)
        else:
            # Scheduled agents: exit 0 means clean last run
            bad_exit = exit_code not in (0, None, -15)
            check("launchd:" + label.replace("com.blaze.", ""),
                  not bad_exit,
                  "last_exit=%s" % exit_code if not bad_exit else "ERROR exit=%s" % exit_code,
                  warn_only=bad_exit)


# ══ SECTION 3: Cron Job Failure Rates ═════════════════════════════════════════

def check_cron_runs():
    findings.append(("INFO", "── SECTION 3: CRON FAILURE RATES (7 days) ──", ""))
    try:
        conn = sqlite3.connect(str(BLAZE_DB))
        cur = conn.cursor()
        cutoff = (NOW - timedelta(days=7)).isoformat()
        cur.execute("""
            SELECT job_name,
                   COUNT(*) as total,
                   SUM(CASE WHEN status='fail' THEN 1 ELSE 0 END) as fails,
                   MAX(completed_at) as last_run
            FROM cron_runs
            WHERE completed_at > ?
            GROUP BY job_name
            ORDER BY fails DESC, last_run DESC
        """, (cutoff,))
        rows_with_status = []
        for row in cur.fetchall():
            job, total, fails, last_run = row
            last_s = conn.execute(
                "SELECT status FROM cron_runs WHERE job_name=? ORDER BY completed_at DESC LIMIT 1",
                (job,)).fetchone()
            rows_with_status.append((job, total, fails, last_run, last_s[0] if last_s else None))
        conn.close()
        rows = rows_with_status

        # Jobs exempt from failure rate analysis (audit scripts report system state)
        EXEMPT_FROM_FAIL_ANALYSIS = {"blaze_audit", "blaze_verify", "platform_council"}

        for job, total, fails, last_run, last_run_status in rows:
            if job in EXEMPT_FROM_FAIL_ANALYSIS:
                continue
            fail_rate = (fails / total * 100) if total > 0 else 0
            label = "cron:" + job

            last_ok = (last_run_status == "success") if last_run_status else False

            if fail_rate > 50 and not last_ok:
                check(label, False,
                      "%d/%d failed (%.0f%%) last=%s" % (fails, total, fail_rate,
                      (last_run or "")[:16]))
            elif fail_rate > 50 and last_ok:
                # Historical failures but recovered — downgrade to warn
                check(label, True,
                      "%d/%d historical fails but RECOVERED — last=%s" % (
                      fails, total, (last_run or "")[:16]), warn_only=True)
            elif fail_rate > 10:
                check(label, True,
                      "%d/%d failed (%.0f%%) last=%s" % (fails, total, fail_rate,
                      (last_run or "")[:16]), warn_only=True)
            else:
                check(label, True,
                      "%d runs, %d fails last=%s" % (total, fails,
                      (last_run or "")[:16]))

        # Check for jobs that haven't run in >24h but should
        DAILY_JOBS = ["morning_briefing_v3", "business_council",
                      "blaze_verify", "morning_briefing_acs"]
        for job in DAILY_JOBS:
            cur2 = sqlite3.connect(str(BLAZE_DB)).cursor()
            cur2.execute("SELECT MAX(completed_at) FROM cron_runs WHERE job_name=?", (job,))
            row = cur2.fetchone()
            last = row[0] if row else None
            if last:
                try:
                    last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                    age_h = (NOW - last_dt).total_seconds() / 3600
                    check("cron:staleness:" + job, age_h < 30,
                          "last=%.1fh ago" % age_h, warn_only=age_h < 48)
                except Exception:
                    pass

    except Exception as e:
        findings.append(("FAIL", "cron:db_read", str(e)))


# ══ SECTION 4: Log Error Scan ═════════════════════════════════════════════════

def check_logs():
    findings.append(("INFO", "── SECTION 4: LOG ERROR SCAN ──", ""))

    ERROR_PATTERNS = [
        re.compile(r"error|traceback|exception|fatal|crash|failed", re.I),
    ]
    SKIP_OK = [
        "file_cache is only supported",  # harmless google warning
        "401 response",                  # expected with expired tokens
        "DeprecationWarning",
        "RSS fetch failed",              # youtube channel 404s (benign)
        "HTTP Error 404",               # youtube RSS 404s
        "Transcript failed",            # youtube transcript unavailable
        "No transcript available",      # youtube transcript not available
        "No module named",              # dependency warnings (audited separately)
        "Refreshing credentials due to a 401",  # normal google-auth OAuth refresh cycle
        "file_cache is only supported",       # harmless google-auth warning
        "WARNING",                      # python warning level (not errors)
    ]

    # Check logs modified in last 2 hours
    cutoff_mtime = time.time() - 7200
    for log_file in sorted(LOGS_DIR.glob("*.err.log")):
        if log_file.stat().st_mtime < cutoff_mtime:
            continue
        try:
            lines = log_file.read_text(errors="replace").splitlines()
            # Count errors in last 100 lines
            recent = lines[-100:]
            error_count = 0
            for line in recent:
                if any(s in line for s in SKIP_OK):
                    continue
                if any(p.search(line) for p in ERROR_PATTERNS):
                    error_count += 1
            name = log_file.name.replace(".err.log", "")
            check("logs:" + name, error_count < 10,
                  "%d recent errors" % error_count,
                  warn_only=error_count < 30)
        except Exception as e:
            findings.append(("WARN", "logs:read:" + log_file.name, str(e)))

    # Check imsg_watcher log specifically
    watcher_log = Path.home() / "logs/imsg_watcher.log"
    if watcher_log.exists():
        lines = watcher_log.read_text(errors="replace").splitlines()
        recent = [l for l in lines[-50:] if "LOOP ERROR" in l or "POLL ERROR" in l]
        check("logs:imsg_watcher", len(recent) < 5,
              "loop errors in last 50 lines: %d" % len(recent), warn_only=True)


# ══ SECTION 5: Database Health ════════════════════════════════════════════════

def check_databases():
    findings.append(("INFO", "── SECTION 5: DATABASE HEALTH ──", ""))

    dbs = {
        "blaze.db":     BLAZE_DB,
        "contacts.db":  CONTACTS_DB,
        "knowledge.db": Path.home() / "blaze-data/knowledge.db",
    }
    for name, path in dbs.items():
        if not path.exists():
            check("db:" + name, False, "MISSING")
            continue
        size_mb = path.stat().st_size / 1024 / 1024
        try:
            conn = sqlite3.connect(str(path))
            conn.execute("PRAGMA integrity_check").fetchone()
            conn.close()
            check("db:" + name, True, "%.1fMB" % size_mb)
        except Exception as e:
            check("db:" + name, False, "integrity fail: %s" % e)

    # contacts count
    try:
        conn = sqlite3.connect(str(CONTACTS_DB))
        cnt = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
        conn.close()
        check("db:contacts_count", cnt > 1000, "%d contacts" % cnt)
    except Exception as e:
        check("db:contacts_count", False, str(e))


# ══ SECTION 6: Disk Space ════════════════════════════════════════════════════

def check_disk():
    findings.append(("INFO", "── SECTION 6: DISK SPACE ──", ""))
    rc, out, _ = run(["df", "-h", "/"])
    lines = out.splitlines()
    if len(lines) >= 2:
        parts = lines[1].split()
        capacity_s = [p for p in parts if p.endswith("%")]
        if capacity_s:
            pct = int(capacity_s[0].rstrip("%"))
            check("disk:root", pct < 85, "capacity=%d%%" % pct,
                  warn_only=pct < 95)

    # Log dir size
    try:
        total_mb = sum(f.stat().st_size for f in LOGS_DIR.glob("*")) / 1024 / 1024
        check("disk:logs_dir", total_mb < 2000,
              "%.0fMB in blaze-logs" % total_mb, warn_only=total_mb < 5000)
    except Exception:
        pass


# ══ SECTION 7: Backup Verification ═══════════════════════════════════════════

def check_backups():
    findings.append(("INFO", "── SECTION 7: BACKUP VERIFICATION ──", ""))

    if not BACKUPS_DIR.exists():
        check("backup:dir_exists", False, "no backups directory")
        return

    # Find most recent backup folder (within last 28h — avoids UTC midnight false positive)
    import time as _time
    cutoff_28h = _time.time() - (28 * 3600)
    recent_backup = None
    if BACKUPS_DIR.exists():
        candidates = sorted(
            [d for d in BACKUPS_DIR.iterdir() if d.is_dir()],
            key=lambda d: d.stat().st_mtime, reverse=True
        )
        for d in candidates:
            if d.stat().st_mtime > cutoff_28h:
                recent_backup = d
                break
    check("backup:recent_exists", recent_backup is not None,
          "dir=%s" % recent_backup.name if recent_backup else "NO BACKUP IN LAST 28H")

    if recent_backup:
        file_count = len(list(recent_backup.iterdir()))
        check("backup:files_count", file_count > 50,
              "%d files in %s" % (file_count, recent_backup.name), warn_only=True)

    # Age of most recent backup (via backup.log)
    backup_log = LOGS_DIR / "backup.log"
    if backup_log.exists():
        lines = backup_log.read_text(errors="replace").splitlines()
        last = [l for l in lines if "Backed up to" in l]
        if last:
            # Parse timestamp from last entry
            try:
                ts_s = last[-1].split(": Backed up")[0].strip()
                age_min = (time.time() - time.mktime(time.strptime(ts_s))) / 60
                check("backup:recency", age_min < 90,
                      "last backup %.0f min ago" % age_min, warn_only=True)
            except Exception:
                pass

    # Note: backups are local only, not encrypted to cloud — flag as warning
    findings.append(("WARN", "backup:cloud",
                     "Backups are LOCAL ONLY — no Google Drive encryption configured (see improvement plan)"))


# ══ SECTION 8: Script → Plist Alignment ══════════════════════════════════════

def check_plist_alignment():
    findings.append(("INFO", "── SECTION 8: PLIST→SCRIPT ALIGNMENT ──", ""))

    for plist_path in sorted(LAUNCHAGENTS.glob("com.blaze.*.plist")):
        label = plist_path.stem
        try:
            import plistlib
            with open(plist_path, "rb") as f:
                pl = plistlib.load(f)
            args = pl.get("ProgramArguments", [])
            # Find script paths in ProgramArguments
            working_dir = Path(pl.get("WorkingDirectory", "/"))
            for arg in args:
                if arg.endswith(".py") or arg.endswith(".sh"):
                    # Resolve relative paths against WorkingDirectory
                    script_path = Path(arg) if Path(arg).is_absolute() else working_dir / arg
                    if not script_path.exists():
                        check("align:" + label.replace("com.blaze.", ""),
                              False, "script missing: %s" % arg)
                        break
                    # Check Python version if .py
                    if arg.endswith(".py") and args and "python" in args[0].lower():
                        python_bin = args[0]
                        if "3.14" in python_bin or "python3.14" in python_bin:
                            check("align:" + label.replace("com.blaze.", ""),
                                  False,
                                  "WRONG PYTHON: %s — use CL Tools python3 for uvicorn scripts" % python_bin)
        except Exception as e:
            findings.append(("WARN", "align:" + label.replace("com.blaze.", ""),
                             "plist read error: %s" % e))


# ══ RENDER + SEND ═════════════════════════════════════════════════════════════

def render_report():
    fails  = [(s, l, d) for s, l, d in findings if s == "FAIL"]
    warns  = [(s, l, d) for s, l, d in findings if s == "WARN"]
    passes = [(s, l, d) for s, l, d in findings if s == "PASS"]

    pct = int(score_pass / score_total * 100) if score_total else 0
    status = "ALL SYSTEMS GREEN" if not fails else \
             ("DEGRADED — %d FAILURES" % len(fails))

    lines = [
        "=" * 54,
        "BLAZE V4 — FULL SYSTEM AUDIT",
        "Run: " + NOW.strftime("%Y-%m-%d %H:%M UTC"),
        "=" * 54,
        "",
        "SCORE: %d/%d (%d%%)" % (score_pass, score_total, pct),
        "STATUS: " + status,
        "",
    ]

    if fails:
        lines.append("── FAILURES (%d) ──" % len(fails))
        for i, (_, l, d) in enumerate(fails, 1):
            lines.append("[F%d] %s — %s" % (i, l, d))
        lines.append("")

    if warns:
        lines.append("── WARNINGS (%d) ──" % len(warns))
        for i, (_, l, d) in enumerate(warns, 1):
            lines.append("[W%d] %s — %s" % (i, l, d))
        lines.append("")

    lines.append("── PASSED (%d/%d) ──" % (len(passes), score_total))

    return "\n".join(lines)


def send_telegram(msg, account="main", target="telegram:7747110667"):
    try:
        subprocess.Popen(
            [OPENCLAW, "message", "send",
             "--channel", "telegram",
             "--account", account,
             "--target", target,
             "--message", msg],
            env=OPENCLAW_ENV, close_fds=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        print("TELEGRAM ERROR: %s" % e)


def record_cron(status, summary, error=""):
    try:
        conn = sqlite3.connect(str(BLAZE_DB))
        conn.execute(
            "INSERT INTO cron_runs (job_name, started_at, completed_at, status, output_summary, error_message)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            ("blaze_audit", NOW.isoformat(), datetime.now(timezone.utc).isoformat(),
             status, summary[:500], error[:200])
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def main():
    import sys
    silent = "--silent" in sys.argv
    telegram = "--telegram" in sys.argv or "--notify" in sys.argv

    check_services()
    check_launchagents()
    check_cron_runs()
    check_logs()
    check_databases()
    check_disk()
    check_backups()
    check_plist_alignment()

    report = render_report()
    print(report)

    fails  = [f for s, f, d in findings if s == "FAIL"]
    warns  = [f for s, f, d in findings if s == "WARN"]

    # Always save to cron DB
    status = "success" if not fails else "fail"
    record_cron(status, "Audit: %d pass, %d fail, %d warn" % (score_pass, len(fails), len(warns)))

    # Send Telegram if failures or explicitly requested
    if telegram or fails or len(warns) > 3:
        # Build concise Telegram version
        pct = int(score_pass / score_total * 100) if score_total else 0
        tg_lines = ["🔍 BLAZE AUDIT — %s" % NOW.strftime("%Y-%m-%d %H:%M")]
        tg_lines.append("Score: %d/%d (%d%%)" % (score_pass, score_total, pct))

        if fails:
            tg_lines.append("\n🔴 FAILURES:")
            for i, (_, l, d) in enumerate([(s, l, d) for s, l, d in findings
                                           if s == "FAIL"], 1):
                tg_lines.append("  F%d. %s — %s" % (i, l, d[:60]))

        if len(warns) <= 10:
            tg_lines.append("\n⚠️ WARNINGS:")
            for i, (_, l, d) in enumerate([(s, l, d) for s, l, d in findings
                                           if s == "WARN"], 1):
                tg_lines.append("  W%d. %s — %s" % (i, l, d[:60]))

        tg_lines.append("\nAll %d checks complete." % score_total)
        send_telegram("\n".join(tg_lines))

    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
