#!/usr/bin/env python3
"""
platform_council.py — Nightly 4-perspective platform health council.
Inspired by Matthew Berman's Security Council + Platform Council pattern.

Perspectives:
  1. Operational Realism  — Are jobs actually running? Any silent failures?
  2. Code Integrity       — Wrong Python, hardcoded secrets, dead plist→script refs
  3. Log Health           — Error rates, log size bloat, missing expected logs
  4. Alignment Audit      — Scripts without LaunchAgents, LaunchAgents without scripts,
                            MEMORY.md staleness, cron_runs coverage

Runs nightly at 3:30 AM via com.blaze.platform-council LaunchAgent.
Sends numbered findings to Bailey (Telegram). Critical = immediate ping.
"""
import subprocess, json, os, re, sqlite3, plistlib, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

OPENCLAW     = "/usr/local/bin/openclaw"
OPENCLAW_ENV = {**os.environ, "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"}
BLAZE_DB     = Path.home() / "blaze-data/blaze.db"
LOGS_DIR     = Path.home() / "blaze-logs"
SCRIPTS_DIR  = Path.home() / "ACS_CC_AUTOBOT/blaze-v4/ops/scripts"
LAUNCHAGENTS = Path.home() / "Library/LaunchAgents"
NOW          = datetime.now(timezone.utc)

findings = []   # (severity, perspective, label, detail, fix)
# severity: CRITICAL | HIGH | MEDIUM | LOW | INFO


def flag(severity, perspective, label, detail, fix=""):
    findings.append((severity, perspective, label, detail, fix))


def run(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return -1, "", str(e)


# ══ PERSPECTIVE 1: OPERATIONAL REALISM ════════════════════════════════════════
# Are all jobs actually running? Silent failures? Unexpected gaps?

def perspective_operational():
    # Check 7-day cron run patterns
    try:
        conn = sqlite3.connect(str(BLAZE_DB))
        cur = conn.cursor()
        cutoff = (NOW - timedelta(days=7)).isoformat()

        cur.execute("""
            SELECT job_name,
                   COUNT(*) as total,
                   SUM(CASE WHEN status='fail' THEN 1 ELSE 0 END) as fails,
                   MAX(completed_at) as last_run,
                   MIN(completed_at) as first_run
            FROM cron_runs WHERE completed_at > ? GROUP BY job_name
        """, (cutoff,))
        rows = {r[0]: r for r in cur.fetchall()}
        conn.close()

        # Jobs exempt from failure rate analysis (audit/council scripts report system state,
        # their "failures" are findings, not script errors)
        EXEMPT_FROM_FAIL_ANALYSIS = {"blaze_audit", "blaze_verify", "platform_council",
                                     "security_audit", "blaze_test"}

        # High failure rates — with recovery detection
        for job, (_, total, fails, last_run, first_run) in rows.items():
            if total == 0 or job in EXEMPT_FROM_FAIL_ANALYSIS:
                continue
            rate = fails / total

            # Recovery: if most recent run was success, downgrade severity
            try:
                _conn2 = sqlite3.connect(str(BLAZE_DB))
                last_row = _conn2.execute(
                    "SELECT status FROM cron_runs WHERE job_name=? ORDER BY completed_at DESC LIMIT 1",
                    (job,)).fetchone()
                _conn2.close()
                last_ok = last_row and last_row[0] == "success"
            except Exception:
                last_ok = False

            if rate > 0.5 and not last_ok:
                flag("HIGH", "Operational", "cron_fail_rate:" + job,
                     "%.0f%% fail rate (%d/%d runs)" % (rate*100, fails, total),
                     "Check log: %s.log" % job.replace("_", "-"))
            elif rate > 0.5 and last_ok:
                flag("MEDIUM", "Operational", "cron_fail_rate:" + job,
                     "%.0f%% historical fails but RECOVERED — last run OK (%d/%d)" % (rate*100, fails, total))
            elif rate > 0.2:
                flag("MEDIUM", "Operational", "cron_fail_rate:" + job,
                     "%.0f%% fail rate (%d/%d runs)" % (rate*100, fails, total))

        # Check for jobs expected daily but last run > 30h ago
        DAILY = {
            "morning_briefing_v3": "6:30AM morning brief",
            "business_council":    "1:00AM business council",
            "blaze_audit":         "7:00AM system audit",
        }
        for job, desc in DAILY.items():
            r = rows.get(job)
            if not r:
                flag("HIGH", "Operational", "job_never_ran:" + job,
                     "%s — no runs in 7 days" % desc)
            else:
                last_s = r[3]
                try:
                    last_dt = datetime.fromisoformat(last_s.replace("Z", "+00:00"))
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                    age_h = (NOW - last_dt).total_seconds() / 3600
                    if age_h > 30:
                        flag("MEDIUM", "Operational", "job_stale:" + job,
                             "%s — last run %.1fh ago" % (desc, age_h))
                except Exception:
                    pass

    except Exception as e:
        flag("HIGH", "Operational", "db_read_error", str(e))

    # Check daemons are alive
    DAEMONS = {
        "imsg_watcher.py": "iMessage watcher",
        "imsg_relay.py":   "iMessage relay",
    }
    for pattern, name in DAEMONS.items():
        rc, _, _ = run(["pgrep", "-f", pattern])
        if rc != 0:
            flag("CRITICAL", "Operational", "daemon_down:" + pattern.replace(".py", ""),
                 "%s is NOT running" % name,
                 "launchctl bootstrap gui/502 ~/Library/LaunchAgents/com.blaze.%s.plist" % \
                 pattern.replace("_watcher.py", "-watcher").replace("_relay.py", "-relay"))

    # FastAPI check
    rc, out, _ = run(["curl", "-sf", "http://127.0.0.1:8899/health"], timeout=5)
    if rc != 0:
        flag("CRITICAL", "Operational", "fastapi_down",
             "FastAPI on port 8899 unreachable",
             "launchctl kickstart -k gui/502/com.blaze.fastapi")


# ══ PERSPECTIVE 2: CODE INTEGRITY ════════════════════════════════════════════
# Wrong Python versions, dead references, hardcoded secrets, bad patterns

PYTHON_PATTERNS = re.compile(r"python3\.14|python3\.12|/opt/homebrew/bin/python3", re.I)
SECRET_PATTERNS = re.compile(
    r"(api_key|token|password|secret|key)\s*=\s*['\"][a-zA-Z0-9_\-]{12,}['\"]",
    re.I
)
# Known-safe values that are not real secrets
SECRET_WHITELIST = ["sk-proj-***REDACTED***", "your_key_here", "REPLACE_ME",
                    "test", "example", "placeholder"]

def perspective_code():
    # 1. Plist → script alignment
    for plist_path in sorted(LAUNCHAGENTS.glob("com.blaze.*.plist")):
        try:
            with open(plist_path, "rb") as f:
                pl = plistlib.load(f)
            args = pl.get("ProgramArguments", [])
            python_bin = args[0] if args else ""

            working_dir = Path(pl.get("WorkingDirectory", str(SCRIPTS_DIR)))
            for arg in args:
                if arg.endswith(".py") or arg.endswith(".sh"):
                    p = Path(arg)
                    # Resolve relative paths against WorkingDirectory or SCRIPTS_DIR
                    exists = p.is_absolute() and p.exists() or                              (working_dir / p.name).exists() or                              (SCRIPTS_DIR / p.name).exists()
                    if not exists:
                        flag("HIGH", "Code Integrity",
                             "dead_script:" + plist_path.stem.replace("com.blaze.", ""),
                             "Script missing: %s" % arg,
                             "Either create the script or remove the plist")

            # Wrong Python for uvicorn apps
            if "uvicorn" in args and ("3.14" in python_bin or "opt/homebrew" in python_bin):
                flag("CRITICAL", "Code Integrity",
                     "wrong_python:" + plist_path.stem.replace("com.blaze.", ""),
                     "plist uses %s but uvicorn only in CL Tools Python" % python_bin,
                     "Fix: /Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3")

        except Exception as e:
            flag("LOW", "Code Integrity", "plist_parse:" + plist_path.stem, str(e))

    # 2. Scan scripts for hardcoded secrets
    secret_hits = []
    for script in SCRIPTS_DIR.glob("*.py"):
        try:
            content = script.read_text(errors="replace")
            for match in SECRET_PATTERNS.finditer(content):
                val = match.group(0)
                if not any(w in val.lower() for w in SECRET_WHITELIST):
                    secret_hits.append("%s: %s" % (script.name, val[:60]))
        except Exception:
            pass

    if secret_hits:
        flag("HIGH", "Code Integrity", "hardcoded_secrets",
             "%d potential secrets in scripts" % len(secret_hits),
             "Move to env vars or ~/.openclaw/credentials. Hits: " + "; ".join(secret_hits[:3]))

    # 3. Check for Python 3.9 anti-patterns in new scripts
    BAD_PATTERNS = [
        (re.compile(r"f\".*\\{.*\\}.*\""),  "backslash in f-string (Python 3.9 illegal)"),
        (re.compile(r"\w\s*:=\s"),              "walrus operator in comprehension (check 3.9 compat)"),
    ]
    SELF_EXCLUDE = {"blaze_audit.py", "platform_council.py", "security_audit.py"}
    for script in SCRIPTS_DIR.glob("*.py"):
        try:
            if script.name in SELF_EXCLUDE:
                continue
            content = script.read_text(errors="replace")
            mtime = script.stat().st_mtime
            if time.time() - mtime > 7 * 86400:
                continue  # only check recently modified scripts
            for pat, desc in BAD_PATTERNS:
                if pat.search(content):
                    flag("MEDIUM", "Code Integrity",
                         "syntax_compat:" + script.name, desc)
        except Exception:
            pass


# ══ PERSPECTIVE 3: LOG HEALTH ════════════════════════════════════════════════
# Error rates, bloat, missing expected logs, OAuth degradation

def perspective_logs():
    # 1. Error rate per log
    ERROR_PAT = re.compile(r"\b(error|traceback|exception|fatal|crash)\b", re.I)
    SKIP_OK   = ["file_cache is only supported", "DeprecationWarning",
                 "oauth2client", "Refreshing credentials",
                 "HTTP Error 404",           # youtube RSS 404 (benign)
                 "RSS fetch failed",         # youtube channel moved/renamed
                 "Transcript failed",        # youtube transcript unavailable
                 "No transcript available",  # youtube transcript not available
                 "No module named",          # missing optional dependency
                 "[WARNING]",               # warning-level log lines (not errors)
                 ]

    # Only check logs updated in last 24h
    cutoff = time.time() - 86400
    for log_f in sorted(LOGS_DIR.glob("*.err.log")):
        if log_f.stat().st_mtime < cutoff:
            continue
        try:
            lines = log_f.read_text(errors="replace").splitlines()
            recent = lines[-200:]
            errors = [l for l in recent
                      if ERROR_PAT.search(l)
                      and not any(s in l for s in SKIP_OK)]
            if len(errors) > 50:
                flag("HIGH", "Log Health", "error_flood:" + log_f.stem,
                     "%d errors in last 200 lines" % len(errors),
                     "tail -50 %s" % log_f)
            elif len(errors) > 15:
                flag("MEDIUM", "Log Health", "error_rate:" + log_f.stem,
                     "%d errors in last 200 lines" % len(errors))
        except Exception:
            pass

    # 2. Gmail OAuth: 401 in logs is NORMAL — google-auth library auto-refreshes
    # access tokens (they expire every ~1hr). The retry succeeds. Verified working.
    # Do NOT flag this as an error.

    # 3. Log size bloat
    total_mb = sum(f.stat().st_size for f in LOGS_DIR.glob("*")) / 1024 / 1024
    if total_mb > 1000:
        flag("MEDIUM", "Log Health", "log_bloat",
             "%.0fMB total in blaze-logs" % total_mb,
             "Run log-rotation manually or reduce retention")

    # 4. Expected logs that haven't been updated today
    EXPECTED_DAILY = ["gmail-sync.log", "event-stream.log", "netlify-bridge.log"]
    today_cutoff = time.time() - 3600 * 2  # updated in last 2h
    for log_name in EXPECTED_DAILY:
        log_f = LOGS_DIR / log_name
        if not log_f.exists():
            flag("HIGH", "Log Health", "log_missing:" + log_name,
                 "Log file does not exist")
        elif log_f.stat().st_mtime < today_cutoff:
            age_min = (time.time() - log_f.stat().st_mtime) / 60
            flag("MEDIUM", "Log Health", "log_stale:" + log_name,
                 "Not updated in %.0f min" % age_min)

    # 5. watcher log check
    watcher_log = Path.home() / "logs/imsg_watcher.log"
    if watcher_log.exists():
        lines = watcher_log.read_text(errors="replace").splitlines()
        loop_errors = [l for l in lines[-100:] if "LOOP ERROR" in l or "POLL ERROR" in l]
        if len(loop_errors) > 10:
            flag("HIGH", "Log Health", "watcher_loop_errors",
                 "%d LOOP ERRORs in recent log" % len(loop_errors))


# ══ PERSPECTIVE 4: ALIGNMENT AUDIT ═══════════════════════════════════════════
# Scripts without LaunchAgents, LaunchAgents without scripts, coverage gaps

def perspective_alignment():
    # 1. Scripts that exist but aren't in any LaunchAgent
    all_plist_scripts = set()
    for plist_path in LAUNCHAGENTS.glob("com.blaze.*.plist"):
        try:
            with open(plist_path, "rb") as f:
                pl = plistlib.load(f)
            for arg in pl.get("ProgramArguments", []):
                if arg.endswith(".py") or arg.endswith(".sh"):
                    all_plist_scripts.add(Path(arg).name)
        except Exception:
            pass

    # Scripts that are clearly system scripts (should have LaunchAgents)
    EXPECTED_COVERED = [
        # blaze_verify.py superseded by blaze_audit.py — no longer needs LaunchAgent
        # morning_briefing.py superseded by morning_briefing_v3.py
        "business_council.py", "netlify_event_bridge.py",
        "youtube_learning_engine.py",
    ]
    for name in EXPECTED_COVERED:
        if name not in all_plist_scripts:
            flag("LOW", "Alignment", "uncovered_script:" + name,
                 "Script not referenced in any LaunchAgent plist",
                 "Add to appropriate plist or verify it's called indirectly")

    # 2. coedit-backend: KNOWN broken (wrong Python / no uvicorn) — acceptable if Disabled=True
    coedit_plist = LAUNCHAGENTS / "com.blaze.coedit-backend.plist"
    if coedit_plist.exists():
        with open(coedit_plist, "rb") as f:
            pl = plistlib.load(f)
        disabled = pl.get("Disabled", False)
        if disabled:
            pass  # Intentionally disabled — not a concern
        else:
            args = pl.get("ProgramArguments", [])
            if args and ("3.14" in args[0] or "opt/homebrew/bin/python3" in args[0]):
                flag("CRITICAL", "Alignment", "coedit_wrong_python",
                     "coedit-backend uses %s — no uvicorn installed there" % args[0],
                     "Disable plist: set Disabled=True or run launchctl bootout gui/502/com.blaze.coedit-backend")

    # 3. Check blaze_audit.py coverage (replaced blaze_verify.py)
    try:
        audit_src = (SCRIPTS_DIR / "blaze_audit.py").read_text(errors="replace")
        # Count entries in EXPECTED_AGENTS dict (com.blaze.* labels), not literal "launchd:"
        import re as _re
        agent_entries = len(_re.findall(r'"com\.blaze\.[a-z0-9-]+"', audit_src))
        total_agents = len(list(LAUNCHAGENTS.glob("com.blaze.*.plist")))
        if agent_entries < total_agents * 0.6:
            flag("MEDIUM", "Alignment", "audit_incomplete",
                 "blaze_audit.py tracks ~%d/%d agents" % (agent_entries, total_agents),
                 "Add missing agents to EXPECTED_AGENTS dict in blaze_audit.py")
        # blaze_verify.py is now superseded — no longer flagged
    except Exception:
        pass

    # 4. MEMORY.md freshness
    memory_md = Path("/Users/baileyeubanks/.claude/projects/-Users-baileyeubanks/memory/MEMORY.md")
    alt_memory = Path.home() / ".openclaw/MEMORY.md"
    for mem_path in [alt_memory]:
        if mem_path.exists():
            age_days = (time.time() - mem_path.stat().st_mtime) / 86400
            if age_days > 7:
                flag("LOW", "Alignment", "memory_stale",
                     "MEMORY.md last updated %.1f days ago" % age_days,
                     "Review and update MEMORY.md with current system state")

    # 5. Platform gaps vs alignment philosophy
    HAS_SECURITY_COUNCIL = (SCRIPTS_DIR / "security_audit.py").exists()
    HAS_PLATFORM_COUNCIL = (SCRIPTS_DIR / "platform_council.py").exists()
    HAS_AUDIT            = (SCRIPTS_DIR / "blaze_audit.py").exists()

    if not HAS_SECURITY_COUNCIL:
        flag("MEDIUM", "Alignment", "missing_security_council",
             "No nightly security audit script (security_audit.py)",
             "Build: 4-perspective security review → Telegram findings")
    if not HAS_PLATFORM_COUNCIL:
        flag("LOW", "Alignment", "platform_council_not_deployed",
             "platform_council.py not yet deployed to scripts dir")
    if not HAS_AUDIT:
        flag("LOW", "Alignment", "blaze_audit_not_deployed",
             "blaze_audit.py not yet deployed to scripts dir")


# ══ RENDER + DISPATCH ════════════════════════════════════════════════════════

def render():
    SEV_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
    sorted_f = sorted(findings, key=lambda x: SEV_ORDER.get(x[0], 9))

    criticals = [f for f in sorted_f if f[0] == "CRITICAL"]
    highs     = [f for f in sorted_f if f[0] == "HIGH"]
    mediums   = [f for f in sorted_f if f[0] == "MEDIUM"]
    lows      = [f for f in sorted_f if f[0] == "LOW"]

    lines = [
        "=" * 56,
        "BLAZE V4 — PLATFORM COUNCIL",
        "Run: " + NOW.strftime("%Y-%m-%d %H:%M UTC"),
        "=" * 56,
        "Findings: %d critical, %d high, %d medium, %d low" %
        (len(criticals), len(highs), len(mediums), len(lows)),
        "",
    ]

    n = 1
    for group, label in [(criticals, "CRITICAL"), (highs, "HIGH"),
                         (mediums, "MEDIUM"), (lows, "LOW")]:
        if not group:
            continue
        lines.append("── %s ──" % label)
        for (sev, persp, lbl, detail, fix) in group:
            lines.append("[%d] [%s] %s" % (n, persp[:3].upper(), lbl))
            lines.append("    Detail: %s" % detail)
            if fix:
                lines.append("    Fix:    %s" % fix[:100])
            n += 1
        lines.append("")

    if not (criticals or highs or mediums or lows):
        lines.append("No issues found. All systems aligned.")

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


def record_cron(status, summary):
    try:
        conn = sqlite3.connect(str(BLAZE_DB))
        conn.execute(
            "INSERT INTO cron_runs (job_name, started_at, completed_at, status, output_summary)"
            " VALUES (?, ?, ?, ?, ?)",
            ("platform_council", NOW.isoformat(),
             datetime.now(timezone.utc).isoformat(), status, summary[:500])
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def main():
    import sys
    telegram = "--telegram" in sys.argv or "--notify" in sys.argv

    # Run all 4 perspectives
    perspective_operational()
    perspective_code()
    perspective_logs()
    perspective_alignment()

    report = render()
    print(report)

    criticals = [f for f in findings if f[0] == "CRITICAL"]
    highs     = [f for f in findings if f[0] == "HIGH"]
    mediums   = [f for f in findings if f[0] == "MEDIUM"]

    status = "fail" if criticals else ("partial" if highs else "success")
    record_cron(status, "Platform council: %dC %dH %dM %dL" %
                (len(criticals), len(highs), len(mediums),
                 len([f for f in findings if f[0] == "LOW"])))

    # Send if issues found or explicitly requested
    if telegram or criticals or highs or mediums:
        tg = ["🛡️ PLATFORM COUNCIL — %s" % NOW.strftime("%Y-%m-%d")]
        c, h, m, l_ = len(criticals), len(highs), len(mediums), \
                      len([f for f in findings if f[0] == "LOW"])
        tg.append("Findings: %dC 🔴 %dH 🟠 %dM 🟡 %dL 🔵" % (c, h, m, l_))

        if criticals or highs:
            tg.append("")
            n = 1
            for (sev, persp, lbl, detail, fix) in criticals + highs:
                tg.append("[%d] %s — %s" % (n, lbl, detail[:80]))
                if fix:
                    tg.append("    Fix: %s" % fix[:80])
                n += 1

        if mediums:
            tg.append("\nMedium issues (%d) — reply for details" % m)

        send_telegram("\n".join(tg))

        # Immediate alert if critical
        if criticals:
            for (_, persp, lbl, detail, fix) in criticals:
                alert = "🚨 CRITICAL [%s]\n%s\n%s\nFix: %s" % (persp, lbl, detail, fix)
                send_telegram(alert)

    return 0 if not criticals else 1


if __name__ == "__main__":
    raise SystemExit(main())
