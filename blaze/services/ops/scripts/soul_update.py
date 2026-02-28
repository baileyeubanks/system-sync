#!/usr/bin/env python3
"""
soul_update.py — Nightly Agent Soul Pruner

Reads each agent's SOUL.md + last 7 days of session snapshots + recent decisions.
Sends to Claude Sonnet for review and pruning. Overwrites SOUL.md if meaningfully changed.

Usage:
  python3 soul_update.py                    # update all agents
  python3 soul_update.py --agent main       # single agent
  python3 soul_update.py --snapshot-only    # only capture snapshot, no Claude update
"""
import sys, os, json, logging
from datetime import datetime, timedelta
from pathlib import Path

OPENCLAW_DIR = Path("/Users/_mxappservice/.openclaw/agents")
LOG_FILE = "/Users/_mxappservice/blaze-logs/soul-updates.log"
import os as _os
from pathlib import Path as _Path

def _load_anthropic_key():
    env_file = _Path.home() / ".blaze_env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                return line.split("=", 1)[1].strip()
    return _os.environ.get("ANTHROPIC_API_KEY", "")

ANTHROPIC_KEY = _load_anthropic_key()

ANTHROPIC_KEY = _load_anthropic_key()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("soul_update")

AGENTS = ["main", "acs-worker", "cc-worker"]

PRUNE_PROMPT = """You are reviewing an AI agent's personality/soul file (SOUL.md).

Below is the current SOUL.md, followed by recent session snapshots and decisions.

Your task:
1. Preserve the agent's core identity and communication style
2. Update any facts that have changed (tools, relationships, priorities)
3. Prune outdated content or duplicates
4. Incorporate any recurring patterns from snapshots (new preferences, corrections)
5. Keep it under 400 words — dense, useful, no fluff

Return ONLY the revised SOUL.md content. No preamble, no commentary.

---
CURRENT SOUL.md:
{soul}

---
RECENT SESSION SNAPSHOTS (last 7 days):
{snapshots}

---
RECENT DECISIONS (last 10):
{decisions}
"""


def call_claude(prompt):
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )
    return response.content[0].text.strip()


def get_snapshots(agent_dir, days=7):
    snapshot_dir = agent_dir / "memory" / "session-snapshots"
    if not snapshot_dir.exists():
        return "(no snapshots yet)"
    cutoff = datetime.now() - timedelta(days=days)
    texts = []
    for f in sorted(snapshot_dir.glob("*.md"), reverse=True)[:7]:
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            if mtime < cutoff:
                continue
            content = f.read_text().strip()
            if content:
                texts.append("=== %s ===\n%s" % (f.name, content[:600]))
        except Exception:
            pass
    return "\n\n".join(texts) if texts else "(no recent snapshots)"


def get_decisions(agent_dir):
    decisions_file = agent_dir / "memory" / "decisions.jsonl"
    if not decisions_file.exists():
        return "(no decisions log)"
    lines = []
    try:
        with open(decisions_file) as f:
            all_lines = f.readlines()
        for line in all_lines[-10:]:
            entry = json.loads(line.strip())
            lines.append("- %s" % entry.get("decision", str(entry))[:120])
    except Exception:
        pass
    return "\n".join(lines) if lines else "(no decisions)"


def update_agent_soul(agent_name, snapshot_only=False):
    agent_dir = OPENCLAW_DIR / agent_name
    soul_path = agent_dir / "agent" / "SOUL.md"

    if not soul_path.exists():
        logger.warning("[%s] SOUL.md not found at %s" % (agent_name, soul_path))
        return False

    current_soul = soul_path.read_text().strip()
    if not current_soul:
        logger.warning("[%s] SOUL.md is empty, skipping" % agent_name)
        return False

    if snapshot_only:
        logger.info("[%s] snapshot-only mode, skipping Claude update" % agent_name)
        return True

    snapshots = get_snapshots(agent_dir)
    decisions = get_decisions(agent_dir)

    prompt = PRUNE_PROMPT.format(
        soul=current_soul,
        snapshots=snapshots,
        decisions=decisions,
    )

    try:
        revised = call_claude(prompt)
    except Exception as e:
        logger.error("[%s] Claude call failed: %s" % (agent_name, e))
        return False

    if not revised or len(revised) < 50:
        logger.warning("[%s] Claude returned suspiciously short output, skipping" % agent_name)
        return False

    # Check if meaningfully different (>10% change)
    old_words = set(current_soul.lower().split())
    new_words = set(revised.lower().split())
    overlap = len(old_words & new_words)
    similarity = overlap / max(len(old_words), 1)

    if similarity > 0.95:
        logger.info("[%s] SOUL.md unchanged (%.0f%% similar), skipping write" % (
            agent_name, similarity * 100))
        return True

    # Backup + overwrite
    backup = soul_path.with_suffix(".md.bak")
    backup.write_text(current_soul)
    soul_path.write_text(revised)

    # Log diff summary
    old_lines = len(current_soul.splitlines())
    new_lines = len(revised.splitlines())
    log_entry = "[%s] %s SOUL.md updated: %d→%d lines (%.0f%% similarity)\n" % (
        datetime.now().strftime("%Y-%m-%d %H:%M"),
        agent_name, old_lines, new_lines, similarity * 100
    )
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(log_entry)

    logger.info("[%s] SOUL.md updated: %d→%d lines" % (agent_name, old_lines, new_lines))
    return True


def main():
    snapshot_only = "--snapshot-only" in sys.argv
    agents_arg = None
    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--agent" and i + 1 < len(sys.argv) - 1:
            agents_arg = [sys.argv[i + 2]]
            break

    agents = agents_arg or AGENTS
    results = []
    for agent in agents:
        ok = update_agent_soul(agent, snapshot_only=snapshot_only)
        results.append("%s: %s" % (agent, "ok" if ok else "skipped"))

    summary = "soul_update done: %s" % ", ".join(results)
    logger.info(summary)
    print(summary)

    # Trigger memory reindex for each updated agent (best-effort)
    if not snapshot_only:
        try:
            import subprocess
            for agent in agents:
                subprocess.run(
                    ["/usr/local/bin/openclaw", "memory", "index", "--force", "--agent", agent],
                    timeout=30, capture_output=True,
                    env=dict(os.environ, PATH="/usr/local/bin:/opt/homebrew/bin:%s" % os.environ.get("PATH", "")),
                )
        except Exception:
            pass

    sys.path.insert(0, os.path.dirname(__file__))
    try:
        from blaze_helper import log_cron
        log_cron("soul_update", "success", summary)
    except Exception:
        pass


if __name__ == "__main__":
    main()
