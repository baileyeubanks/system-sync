#!/usr/bin/env python3
"""
Blaze Nightly Extraction (Loop 4)
Runs at 11 PM daily via com.blaze.nightly-extraction LaunchAgent

What it does:
1. Scans today's logs for ERRORs and WARNs
2. Summarizes today's knowledge harvest
3. Checks/prunes expired holds in HOLDS.md
4. Runs research-worker with extraction prompt
5. Saves output to ~/blaze-data/extractions/YYYY-MM-DD.md
6. Updates active-state.md with today's snapshot
"""

import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, date, timezone, timedelta

HOME = os.path.expanduser("~")
LOG_DIR = os.path.join(HOME, "blaze-logs")
EXTRACT_DIR = os.path.join(HOME, "blaze-data", "extractions")
KNOWLEDGE_DB = os.path.join(HOME, "blaze-data", "knowledge.db")
HOLDS_FILE = os.path.join(HOME, ".openclaw", "workspace", "HOLDS.md")
ACTIVE_STATE = os.path.join(HOME, ".openclaw", "agents", "main", "agent", "memory", "active-state.md")
OPENCLAW = "/usr/local/bin/openclaw"
TODAY = date.today().isoformat()
NOW = datetime.now(timezone.utc)


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


# ---------------------------------------------------------------------------
# 1. Scan logs for errors
# ---------------------------------------------------------------------------
def scan_logs():
    errors = []
    for fname in os.listdir(LOG_DIR):
        if not fname.endswith(".log"):
            continue
        fpath = os.path.join(LOG_DIR, fname)
        try:
            with open(fpath, "r", errors="replace") as f:
                for line in f:
                    if TODAY in line and ("ERROR" in line or "WARN" in line or "CRITICAL" in line):
                        stripped = line.strip()
                        if stripped and stripped not in errors:
                            errors.append(stripped[:200])
        except Exception:
            pass
    return errors[:20]  # cap at 20


# ---------------------------------------------------------------------------
# 2. Knowledge harvest summary
# ---------------------------------------------------------------------------
def knowledge_summary():
    if not os.path.exists(KNOWLEDGE_DB):
        return "Knowledge DB not found."
    try:
        conn = sqlite3.connect(KNOWLEDGE_DB)
        ws = (date.today() - timedelta(days=date.today().weekday())).isoformat()
        total = conn.execute("SELECT COUNT(*) FROM discovery_queue WHERE DATE(fetched_at)=?", (TODAY,)).fetchone()[0]
        by_cat = conn.execute(
            "SELECT category, COUNT(*) as n FROM discovery_queue WHERE DATE(fetched_at)=? GROUP BY category", (TODAY,)
        ).fetchall()
        top3 = conn.execute(
            "SELECT title, source FROM discovery_queue WHERE DATE(fetched_at)=? ORDER BY relevance_score DESC LIMIT 3",
            (TODAY,)
        ).fetchall()
        conn.close()
        lines = [f"Today's harvest: {total} new items"]
        for row in by_cat:
            lines.append(f"  {row[0]}: {row[1]}")
        lines.append("Top stories:")
        for row in top3:
            lines.append(f"  - {row[0][:80]} ({row[1]})")
        return "\n".join(lines)
    except Exception as e:
        return f"Knowledge DB error: {e}"


# ---------------------------------------------------------------------------
# 3. Prune expired holds
# ---------------------------------------------------------------------------
def prune_holds():
    if not os.path.exists(HOLDS_FILE):
        return 0

    with open(HOLDS_FILE) as f:
        content = f.read()

    pruned = []
    archive_additions = []
    # Find hold blocks
    hold_blocks = re.split(r"(?=### )", content)
    active_section_started = False
    result_blocks = []
    expired_count = 0

    for block in hold_blocks:
        if not block.strip():
            continue
        if "## Active Holds" in block:
            result_blocks.append(block)
            active_section_started = True
            continue
        if "## Expired Holds" in block:
            # Keep everything from here on as-is but append newly expired
            result_blocks.append(block)
            for old_hold in archive_additions:
                result_blocks.append(old_hold)
            active_section_started = False
            continue

        if active_section_started and block.startswith("### "):
            # Check for Expires date
            expires_match = re.search(r"\*\*Expires:\*\*\s+(\d{4}-\d{2}-\d{2})", block)
            if expires_match:
                expires_date = expires_match.group(1)
                if expires_date < TODAY:
                    # Expired — move to archive
                    archive_additions.append(block.rstrip() + f"\n- **Archived:** {TODAY}\n")
                    expired_count += 1
                    log(f"  Hold expired: {block.split(chr(10))[0].strip()}")
                    continue
            result_blocks.append(block)
        else:
            result_blocks.append(block)

    if expired_count > 0:
        new_content = "".join(result_blocks)
        with open(HOLDS_FILE, "w") as f:
            f.write(new_content)

    return expired_count


# ---------------------------------------------------------------------------
# 4. FastAPI health check
# ---------------------------------------------------------------------------
def check_fastapi():
    try:
        result = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "http://127.0.0.1:8899/health"],
            capture_output=True, text=True, timeout=5
        )
        code = result.stdout.strip()
        return f"FastAPI /health: {code}"
    except Exception as e:
        return f"FastAPI unreachable: {e}"


# ---------------------------------------------------------------------------
# 5. Run research-worker extraction
# ---------------------------------------------------------------------------
def run_extraction(errors, knowledge, fastapi_status, expired_holds):
    errors_section = "\n".join(errors) if errors else "No errors today."
    error_flag = "ACTION NEEDED — errors detected." if errors else "Systems clean."

    prompt = (
        "You are running the nightly extraction for Bailey Eubanks's AI operations (Feb 25, 2026 onwards).\n\n"
        f"Date: {TODAY}\n\n"
        f"SYSTEM STATUS: {fastapi_status} | {error_flag}\n\n"
        f"TODAY'S LOG ERRORS ({len(errors)} found):\n{errors_section}\n\n"
        f"TODAY'S KNOWLEDGE HARVEST:\n{knowledge}\n\n"
        f"HOLDS PRUNED TODAY: {expired_holds} expired hold(s) archived.\n\n"
        "Tasks:\n"
        "1. If any log errors represent new failure patterns not already in regressions, write them as dated regression entries in the format: `- [YYYY-MM-DD] What went wrong → rule to prevent it`\n"
        "2. Write a 3-sentence summary of today's operational state for active-state.md\n"
        "3. Note any knowledge harvest items that seem particularly actionable\n"
        "4. Flag anything that needs Bailey's attention tomorrow\n\n"
        "Output format (use these exact headers):\n"
        "## NEW REGRESSIONS\n[entries or 'None']\n\n"
        "## TODAY'S STATE\n[3 sentences]\n\n"
        "## ACTIONABLE DISCOVERIES\n[bullet list or 'None']\n\n"
        "## FLAGS FOR TOMORROW\n[bullet list or 'None']\n"
    )

    env = os.environ.copy()
    env["PATH"] = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
    env["HOME"] = HOME

    try:
        result = subprocess.run(
            [OPENCLAW, "agent", "--agent", "research-worker", "--message", prompt, "--json"],
            capture_output=True, text=True, timeout=180, env=env
        )
        if result.returncode != 0:
            log(f"research-worker returned {result.returncode}: {result.stderr[:300]}")
            return None
        try:
            import json
            data = json.loads(result.stdout)
            # Navigate: result.payloads[0].text
            payloads = data.get("result", {}).get("payloads", [])
            if payloads:
                return payloads[0].get("text") or result.stdout
            return data.get("text") or data.get("content") or result.stdout
        except Exception:
            return result.stdout
    except subprocess.TimeoutExpired:
        log("research-worker timed out")
        return None
    except Exception as e:
        log(f"research-worker error: {e}")
        return None


# ---------------------------------------------------------------------------
# 6. Save extraction + update active-state
# ---------------------------------------------------------------------------
def save_extraction(response):
    os.makedirs(EXTRACT_DIR, exist_ok=True)
    out_path = os.path.join(EXTRACT_DIR, f"{TODAY}.md")
    header = f"# Nightly Extraction — {TODAY}\nGenerated: {NOW.isoformat()}\n\n"
    with open(out_path, "w") as f:
        f.write(header + (response or "No response from research-worker."))
    log(f"Extraction saved: {out_path}")
    return out_path


def update_active_state(response):
    if not response or "## TODAY'S STATE" not in response:
        return
    # Extract the TODAY'S STATE section
    state_match = re.search(r"## TODAY'S STATE\n(.*?)(?=\n## |$)", response, re.DOTALL)
    if not state_match:
        return
    new_state = state_match.group(1).strip()
    if not os.path.exists(ACTIVE_STATE):
        return
    with open(ACTIVE_STATE) as f:
        content = f.read()
    # Replace or prepend to the Current State section
    today_entry = f"\n### {TODAY}\n{new_state}\n"
    if "## Recent Daily Snapshots" in content:
        content = content.replace("## Recent Daily Snapshots\n", "## Recent Daily Snapshots\n" + today_entry, 1)
    else:
        content += f"\n## Recent Daily Snapshots\n{today_entry}"
    with open(ACTIVE_STATE, "w") as f:
        f.write(content)
    log("active-state.md updated with today's snapshot")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    log(f"=== NIGHTLY EXTRACTION {TODAY} ===")

    log("Scanning logs...")
    errors = scan_logs()
    log(f"Found {len(errors)} errors/warnings")

    log("Summarizing knowledge harvest...")
    knowledge = knowledge_summary()

    log("Checking FastAPI...")
    fastapi_status = check_fastapi()
    log(fastapi_status)

    log("Pruning expired holds...")
    expired = prune_holds()
    log(f"Pruned {expired} expired hold(s)")

    log("Running research-worker extraction...")
    response = run_extraction(errors, knowledge, fastapi_status, expired)

    if response:
        path = save_extraction(response)
        update_active_state(response)
        log(f"Extraction complete. Saved to {path}")
    else:
        log("No response — saved placeholder.")
        save_extraction("research-worker unavailable.")

    log("=== DONE ===")
