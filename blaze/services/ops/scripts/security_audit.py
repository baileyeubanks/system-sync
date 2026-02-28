#!/usr/bin/env python3
"""
security_audit.py — Nightly 4-perspective Security Council.
Inspired by Matthew Berman's OpenClaw security philosophy.

Perspectives:
  1. Offensive Posture   — Exposed ports, world-readable secrets, SSH keys
  2. Defensive Posture   — Firewall state, failed logins, sudo usage
  3. Data Privacy        — PII in logs, DB permissions, secrets in git/temp
  4. Operational Realism — API key health, token expiry, service exposure

Runs nightly at 2:00 AM via com.blaze.security-audit LaunchAgent.
Sends findings to Bailey Telegram. CRITICAL = immediate alert.
"""
import subprocess, os, re, time, sqlite3, plistlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

OPENCLAW     = "/usr/local/bin/openclaw"
OPENCLAW_ENV = {**os.environ, "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"}
BLAZE_DB     = Path.home() / "blaze-data/blaze.db"
LOGS_DIR     = Path.home() / "blaze-logs"
SCRIPTS_DIR  = Path.home() / "ACS_CC_AUTOBOT/blaze-v4/ops/scripts"
DATA_DIR     = Path.home() / "blaze-data"
NOW          = datetime.now(timezone.utc)

findings = []  # (severity, perspective, label, detail, fix)
# severity: CRITICAL | HIGH | MEDIUM | LOW | INFO


def flag(severity, perspective, label, detail, fix=""):
    findings.append((severity, perspective, label, detail, fix))


def run(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           env=OPENCLAW_ENV)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return -1, "", str(e)


# ══ PERSPECTIVE 1: OFFENSIVE POSTURE ══════════════════════════════════════════
# What attack surface exists? Ports, exposed files, SSH hygiene.

def perspective_offensive():
    # 1a. Check listening ports — flag anything unexpected
    rc, out, _ = run(["netstat", "-an"])
    if rc == 0:
        listening = [l for l in out.splitlines() if "LISTEN" in l]
        EXPECTED_PORTS = {"8899", "18789", "5432", "22"}  # FastAPI, OpenClaw, Postgres (if any), SSH
        for line in listening:
            parts = line.split()
            if parts:
                addr = parts[3] if len(parts) > 3 else ""
                port = addr.rsplit(".", 1)[-1] if "." in addr else addr.rsplit(":", 1)[-1]
                # Only flag non-loopback listeners on unexpected ports
                is_loopback = "127.0.0.1" in addr or "::1" in addr
                if not is_loopback and port not in EXPECTED_PORTS and port.isdigit():
                    p = int(port)
                    if p < 1024 or p in (3000, 4000, 5000, 8080, 8000, 9000):
                        flag("MEDIUM", "Offensive", "open_port:" + port,
                             "Non-loopback listener on port %s" % port,
                             "Verify this is intentional: lsof -i :%s" % port)

    # 1b. World-readable .env / key files
    SENSITIVE_FILES = [
        Path.home() / ".blaze_env",
        Path.home() / ".openclaw/openclaw.json",
        Path.home() / ".gemini/antigravity/playground/perihelion-armstrong/service_account.json",
    ]
    for f in SENSITIVE_FILES:
        if f.exists():
            mode = oct(f.stat().st_mode)[-3:]
            if mode[2] != "0":  # world can read
                flag("HIGH", "Offensive", "world_readable:" + f.name,
                     "%s permissions %s (world-readable)" % (f.name, mode),
                     "chmod 600 %s" % f)

    # 1c. SSH authorized_keys — flag any unknown keys
    auth_keys = Path.home() / ".ssh/authorized_keys"
    if auth_keys.exists():
        keys = [l for l in auth_keys.read_text().splitlines() if l.strip() and not l.startswith("#")]
        if len(keys) > 5:
            flag("LOW", "Offensive", "ssh_keys_count",
                 "%d authorized SSH keys — review for stale entries" % len(keys),
                 "cat ~/.ssh/authorized_keys | grep -v '#'")

    # 1d. Hardcoded secrets scan — check scripts written/modified in last 7 days
    SECRET_PATTERNS = [
        (re.compile(r'sk-ant-***REDACTED***[A-Za-z0-9_-]{50,}'), "Anthropic API key"),
        (re.compile(r'sk-proj-[A-Za-z0-9_-]{40,}'), "OpenAI API key"),
        (re.compile(r'nfp_[A-Za-z0-9]{30,}'), "Netlify PAT"),
        (re.compile(r'(?:password|passwd|secret)\s*=\s*["\'][^"\']{8,}["\']', re.I), "hardcoded password"),
        (re.compile(r'AIza[A-Za-z0-9_-]{35}'), "Google API key"),
    ]
    SELF_EXCLUDE = {"security_audit.py", "blaze_audit.py", "platform_council.py"}
    cutoff_7d = time.time() - (7 * 86400)
    for script in SCRIPTS_DIR.glob("*.py"):
        if script.name in SELF_EXCLUDE:
            continue
        if script.stat().st_mtime < cutoff_7d:
            continue
        try:
            content = script.read_text(errors="replace")
            for pat, desc in SECRET_PATTERNS:
                if pat.search(content):
                    flag("CRITICAL", "Offensive", "hardcoded_secret:" + script.name,
                         "%s found in %s" % (desc, script.name),
                         "Move to ~/.blaze_env and use _load_env_key() pattern")
        except Exception:
            pass


# ══ PERSPECTIVE 2: DEFENSIVE POSTURE ══════════════════════════════════════════
# Firewall, failed auth, process integrity.

def perspective_defensive():
    # 2a. macOS firewall state
    rc, out, _ = run(["/usr/libexec/ApplicationFirewall/socketfilterfw", "--getglobalstate"])
    if rc == 0:
        if "disabled" in out.lower():
            flag("HIGH", "Defensive", "firewall_disabled",
                 "macOS application firewall is DISABLED",
                 "System Settings → Privacy & Security → Firewall → Turn On")
        # "enabled" in output = OK
    else:
        # Fallback: check via defaults
        rc2, out2, _ = run(["defaults", "read", "/Library/Preferences/com.apple.alf", "globalstate"])
        if rc2 == 0 and out2.strip() == "0":
            flag("HIGH", "Defensive", "firewall_disabled",
                 "macOS application firewall is DISABLED (alf globalstate=0)",
                 "System Settings → Privacy & Security → Firewall")

    # 2b. Recent failed SSH logins
    rc, out, _ = run(["log", "show", "--predicate",
                      "eventMessage CONTAINS 'Invalid user' OR eventMessage CONTAINS 'authentication failure'",
                      "--last", "24h", "--style", "compact"])
    if rc == 0 and out:
        fail_count = len([l for l in out.splitlines() if l.strip()])
        if fail_count > 20:
            flag("HIGH", "Defensive", "ssh_brute_force",
                 "%d failed SSH auth attempts in last 24h" % fail_count,
                 "Review /var/log/auth.log or consider fail2ban / geofencing")
        elif fail_count > 5:
            flag("MEDIUM", "Defensive", "ssh_failed_logins",
                 "%d failed SSH auth attempts in last 24h" % fail_count)

    # 2c. Check blaze-logs for injection-like patterns in incoming data
    INJECTION_PATTERNS = [
        re.compile(r'<script[^>]*>', re.I),
        re.compile(r'(union\s+select|drop\s+table|insert\s+into)', re.I),
        re.compile(r'\.\./\.\./'),  # path traversal
    ]
    cutoff_mtime = time.time() - 86400  # last 24h logs
    for log_file in sorted(LOGS_DIR.glob("*.log")):
        if log_file.stat().st_mtime < cutoff_mtime:
            continue
        try:
            content = log_file.read_text(errors="replace")[-10000:]  # last 10KB
            for pat in INJECTION_PATTERNS:
                if pat.search(content):
                    flag("HIGH", "Defensive", "injection_pattern:" + log_file.name,
                         "Possible injection pattern detected in %s" % log_file.name,
                         "Review log: tail -100 %s" % log_file)
                    break
        except Exception:
            pass

    # 2d. OpenClaw gateway process integrity
    rc, out, _ = run(["pgrep", "-c", "node"])
    if rc == 0:
        node_procs = int(out.strip()) if out.strip().isdigit() else 0
        if node_procs > 3:
            flag("MEDIUM", "Defensive", "node_process_count",
                 "%d node processes running (expected ≤3)" % node_procs,
                 "Possible gateway leak: openclaw gateway stop && openclaw gateway start")


# ══ PERSPECTIVE 3: DATA PRIVACY ════════════════════════════════════════════════
# PII in logs, DB access controls, secrets in temp/git.

def perspective_data_privacy():
    # 3a. PII patterns in recent logs (phone numbers, email addresses in plaintext)
    PII_PATTERNS = [
        (re.compile(r'\b\+?1?\s*\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b'), "phone number"),
        (re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'), "email address"),
        (re.compile(r'\b\d{3}-\d{2}-\d{4}\b'), "SSN pattern"),
    ]
    # Only check logs that would NOT be expected to have contact info
    PRIVACY_SENSITIVE_LOGS = ["backup.log", "git-autosync.log", "db-maintenance.log"]
    for log_name in PRIVACY_SENSITIVE_LOGS:
        log_file = LOGS_DIR / log_name
        if not log_file.exists():
            continue
        try:
            content = log_file.read_text(errors="replace")[-5000:]
            for pat, desc in PII_PATTERNS:
                matches = pat.findall(content)
                if len(matches) > 3:
                    flag("MEDIUM", "Privacy", "pii_in_log:" + log_name,
                         "%d %s(s) found in %s" % (len(matches), desc, log_name),
                         "Review log for accidental PII exposure — consider scrubbing")
                    break
        except Exception:
            pass

    # 3b. /tmp directory — check for sensitive files left behind
    sensitive_tmp = []
    # Ignore blaze patch/fix scripts (our own deploy pattern) and common benign names
    BENIGN_TMP_PATTERNS = {"fix_", "patch_", "disable_", "revert_", "migrate_", "seed_",
                           "audit_", "test_", "check_", "verify_"}
    try:
        for f in Path("/tmp").iterdir():
            fname = f.name.lower()
            # Skip our own maintenance scripts
            if any(fname.startswith(p) for p in BENIGN_TMP_PATTERNS):
                continue
            # Flag only files with credential-like names that aren't scripts
            if (any(kw in fname for kw in ["credential", "secret", "password", ".pem",
                                            ".key", ".env", "token.json", "auth.json"])
                    and not fname.endswith(".py") and not fname.endswith(".sh")):
                sensitive_tmp.append(f.name)
    except Exception:
        pass
    if sensitive_tmp:
        flag("MEDIUM", "Privacy", "sensitive_tmp_files",
             "Possible sensitive files in /tmp: %s" % ", ".join(sensitive_tmp[:5]),
             "Review and delete: rm /tmp/<file>")

    # 3c. DB file permissions
    for db_name in ["blaze.db", "contacts/contacts.db", "knowledge.db"]:
        db_path = DATA_DIR / db_name
        if db_path.exists():
            mode = oct(db_path.stat().st_mode)[-3:]
            if mode[2] != "0":  # world can read DB
                flag("HIGH", "Privacy", "world_readable_db:" + db_name,
                     "%s is world-readable (permissions %s)" % (db_name, mode),
                     "chmod 600 %s" % db_path)

    # 3d. Git history — check for secrets committed
    try:
        rc, out, _ = run(["git", "-C", str(SCRIPTS_DIR.parent.parent),
                          "log", "--oneline", "--all", "-20"])
        if rc == 0 and out:
            # Look for commits mentioning keys
            for line in out.splitlines():
                if any(kw in line.lower() for kw in ["key", "token", "secret", "password"]):
                    # Skip commits that are REMOVING secrets (normal security work)
                    if any(kw in line.lower() for kw in ["remove", "remov", "delete",
                                                          "delet", "cleanup", "clean up"]):
                        continue
                    flag("LOW", "Privacy", "git_commit_mentions_secrets",
                         "Git commit may reference secrets: %s" % line[:80],
                         "Verify no actual secrets were committed: git show <hash>")
    except Exception:
        pass

    # 3e. ~/.blaze_env chmod check
    blaze_env = Path.home() / ".blaze_env"
    if blaze_env.exists():
        mode = oct(blaze_env.stat().st_mode)[-3:]
        if mode != "600":
            flag("HIGH", "Privacy", "blaze_env_permissions",
                 "~/.blaze_env has permissions %s (should be 600)" % mode,
                 "chmod 600 ~/.blaze_env")


# ══ PERSPECTIVE 4: OPERATIONAL REALISM ════════════════════════════════════════
# API key health, token expiry, what would break at 3 AM.

def perspective_operational_security():
    # 4a. Check ~/.blaze_env has all expected keys
    expected_keys = ["ANTHROPIC_API_KEY", "NETLIFY_AUTH_TOKEN"]
    blaze_env = Path.home() / ".blaze_env"
    if blaze_env.exists():
        env_content = blaze_env.read_text()
        env_keys = {l.split("=")[0] for l in env_content.splitlines() if "=" in l}
        for key in expected_keys:
            if key not in env_keys:
                flag("HIGH", "OpSec", "missing_env_key:" + key,
                     "%s not found in ~/.blaze_env" % key,
                     "Add: echo '%s=...' >> ~/.blaze_env && chmod 600 ~/.blaze_env" % key)
    else:
        flag("CRITICAL", "OpSec", "blaze_env_missing",
             "~/.blaze_env not found — all API keys may be hardcoded",
             "Create ~/.blaze_env with ANTHROPIC_API_KEY=... NETLIFY_AUTH_TOKEN=...")

    # 4b. Verify OpenClaw API key actually works (quick health)
    rc, out, err = run(
        [OPENCLAW, "health"],
        timeout=10
    )
    if rc != 0:
        flag("HIGH", "OpSec", "openclaw_health_fail",
             "openclaw health returned non-zero: %s" % (err or out)[:100],
             "Check gateway: openclaw gateway status")

    # 4c. FastAPI health
    rc, out, _ = run(["curl", "-sf", "http://127.0.0.1:8899/health"])
    if rc != 0:
        flag("HIGH", "OpSec", "fastapi_down",
             "FastAPI not responding on port 8899",
             "Check: launchctl list com.blaze.fastapi")

    # 4d. Service account key age
    sa_key = (Path.home() /
              ".gemini/antigravity/playground/perihelion-armstrong/service_account.json")
    if sa_key.exists():
        age_days = (time.time() - sa_key.stat().st_mtime) / 86400
        if age_days > 90:
            flag("MEDIUM", "OpSec", "service_account_key_age",
                 "Google service account key is %.0f days old (rotate every 90d)" % age_days,
                 "Rotate at console.cloud.google.com → IAM → Service Accounts")

    # 4e. Log disk usage — large logs can fill disk silently
    total_log_size = sum(f.stat().st_size for f in LOGS_DIR.glob("*") if f.is_file())
    total_log_mb = total_log_size / (1024 * 1024)
    if total_log_mb > 500:
        flag("HIGH", "OpSec", "log_disk_usage",
             "Log directory is %.0f MB — disk fill risk" % total_log_mb,
             "Run log rotation: launchctl start com.blaze.log-rotation")
    elif total_log_mb > 200:
        flag("MEDIUM", "OpSec", "log_disk_usage",
             "Log directory is %.0f MB" % total_log_mb)

    # 4f. Check cron_runs for any job that hasn't run in > 36h (silent death)
    CRITICAL_JOBS = {
        "event_stream":        "event stream (2min)",
        "morning_briefing_v3": "morning briefing (6:30AM)",
        "gmail_contact_sync":  "gmail sync (5min)",
        "blaze_audit":         "system audit (7AM)",
    }
    try:
        conn = sqlite3.connect(str(BLAZE_DB))
        cutoff_36h = (NOW - timedelta(hours=36)).isoformat()
        for job, desc in CRITICAL_JOBS.items():
            row = conn.execute(
                "SELECT MAX(completed_at) FROM cron_runs WHERE job_name=?",
                (job,)).fetchone()
            last_run = row[0] if row else None
            if not last_run or last_run < cutoff_36h:
                flag("HIGH", "OpSec", "critical_job_silent:" + job,
                     "%s has not run in >36h (last: %s)" % (desc, (last_run or "NEVER")[:16]),
                     "Check LaunchAgent: launchctl list com.blaze.%s" % job.replace("_", "-"))
        conn.close()
    except Exception as e:
        flag("LOW", "OpSec", "cron_db_check_failed",
             "Could not verify cron job freshness: %s" % str(e))


# ══ RENDER + DISPATCH ════════════════════════════════════════════════════════

def render():
    SEV_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
    sorted_f = sorted(findings, key=lambda x: SEV_ORDER.get(x[0], 9))

    counts = {}
    for s, _, _, _, _ in sorted_f:
        counts[s] = counts.get(s, 0) + 1

    lines = ["🔐 SECURITY AUDIT — %s" % NOW.strftime("%Y-%m-%d %H:%M UTC")]
    lines.append("=" * 50)

    # Summary
    summary_parts = []
    for sev in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]:
        if sev in counts:
            emoji = {"CRITICAL": "🚨", "HIGH": "❌", "MEDIUM": "⚠️", "LOW": "💡"}[sev]
            summary_parts.append("%s %s×%d" % (emoji, sev, counts[sev]))
    lines.append("  ".join(summary_parts) if summary_parts else "✅ ALL CLEAR")
    lines.append("")

    # Findings
    for sev, persp, label, detail, fix in sorted_f:
        if sev == "INFO":
            continue
        emoji = {"CRITICAL": "🚨", "HIGH": "❌", "MEDIUM": "⚠️", "LOW": "💡"}.get(sev, "•")
        lines.append("%s [%s] %s" % (emoji, persp[:8], label))
        if detail:
            lines.append("   %s" % detail)
        if fix:
            lines.append("   Fix: %s" % fix)

    if not any(s in ("CRITICAL", "HIGH", "MEDIUM") for s, *_ in sorted_f):
        lines.append("✅ No critical/high/medium security issues found.")

    return "\n".join(lines)


def dispatch(report):
    """Send report to Bailey Telegram."""
    import shlex
    cmd = [
        OPENCLAW, "message", "send",
        "--channel", "telegram",
        "--account", "main",
        "--target", "telegram:7747110667",
        "--message", report,
    ]
    rc, out, err = run(cmd, timeout=30)
    if rc != 0:
        print("Telegram send failed: %s" % (err or out))


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--telegram", action="store_true", help="Send report to Telegram")
    parser.add_argument("--stdout", action="store_true", help="Print report to stdout (default)")
    args = parser.parse_args()

    print("Running security audit perspectives...")
    perspective_offensive()
    perspective_defensive()
    perspective_data_privacy()
    perspective_operational_security()

    report = render()
    print(report)

    if args.telegram:
        dispatch(report)
        print("Report dispatched to Telegram.")

    # Exit code: 1 if any CRITICAL/HIGH
    has_critical = any(s in ("CRITICAL", "HIGH") for s, *_ in findings)
    return 1 if has_critical else 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
