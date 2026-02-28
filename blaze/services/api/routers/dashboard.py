"""
dashboard.py — System health & metrics endpoint for Blaze V4 dashboard.
GET /api/dashboard/stats — aggregated system snapshot
"""
from __future__ import annotations
import json
import os
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(prefix="/api/dashboard")

KNOWLEDGE_DB = Path.home() / "blaze-data/knowledge.db"
CONTACTS_DB  = Path.home() / "blaze-data/contacts/contacts.db"
BLAZE_DB     = Path.home() / "blaze-data/blaze.db"
LOGS_DIR     = Path.home() / "blaze-logs"
DASHBOARD_HTML = Path(__file__).parent.parent / "static" / "dashboard.html"


def _db(path: Path, timeout: int = 5):
    return sqlite3.connect(str(path), timeout=timeout)


@router.get("/stats")
def dashboard_stats():
    now = datetime.utcnow()
    stats: dict[str, Any] = {"generated_at": now.isoformat() + "Z", "ok": True}

    # ── FastAPI health ────────────────────────────────────────────────────────
    stats["api"] = {"status": "ok", "port": 8899}

    # ── LaunchAgents ──────────────────────────────────────────────────────────
    try:
        result = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5)
        blaze = [l.strip() for l in result.stdout.splitlines() if "blaze" in l.lower()]
        running = [l for l in blaze if not l.startswith("-")]
        stats["launchagents"] = {"total": len(blaze), "running": len(running)}
    except Exception as e:
        stats["launchagents"] = {"error": str(e)}

    # ── Contacts ──────────────────────────────────────────────────────────────
    try:
        conn = _db(CONTACTS_DB)
        total = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
        employees = conn.execute("SELECT COUNT(*) FROM contacts WHERE category='employee'").fetchone()[0]
        clients   = conn.execute("SELECT COUNT(*) FROM contacts WHERE category='business' AND acs_score > 0").fetchone()[0]
        recent_wa = conn.execute(
            "SELECT COUNT(*) FROM contacts WHERE source='whatsapp' AND updated_at > datetime('now','-7 days')"
        ).fetchone()[0]
        conn.close()
        stats["contacts"] = {"total": total, "employees": employees, "acs_clients": clients, "new_wa_7d": recent_wa}
    except Exception as e:
        stats["contacts"] = {"error": str(e)}

    # ── YouTube insights ──────────────────────────────────────────────────────
    try:
        conn = _db(KNOWLEDGE_DB)
        insights_total = conn.execute("SELECT COUNT(*) FROM youtube_insights").fetchone()[0]
        insights_7d    = conn.execute(
            "SELECT COUNT(*) FROM youtube_insights WHERE created_at > datetime('now','-7 days')"
        ).fetchone()[0]
        queue_pending  = conn.execute("SELECT COUNT(*) FROM youtube_queue WHERE status='pending'").fetchone()[0]
        queue_done     = conn.execute("SELECT COUNT(*) FROM youtube_queue WHERE status='done'").fetchone()[0]

        # Recent insights (last 3)
        recent = conn.execute(
            "SELECT channel_name, insight, created_at FROM youtube_insights ORDER BY created_at DESC LIMIT 3"
        ).fetchall()
        conn.close()
        stats["learning"] = {
            "insights_total": insights_total,
            "insights_7d": insights_7d,
            "queue_pending": queue_pending,
            "queue_done": queue_done,
            "recent": [{"channel": r[0], "insight": r[1][:120], "at": r[2]} for r in recent],
        }
    except Exception as e:
        stats["learning"] = {"error": str(e)}

    # ── Goals ─────────────────────────────────────────────────────────────────
    try:
        conn = _db(KNOWLEDGE_DB)
        goals = conn.execute(
            "SELECT type, goal, business, status FROM goals ORDER BY business, type"
        ).fetchall()
        conn.close()
        stats["goals"] = [{"type": g[0], "goal": g[1], "business": g[2], "status": g[3]} for g in goals]
    except Exception as e:
        stats["goals"] = {"error": str(e)}

    # ── Cron / script runs (blaze.db) ─────────────────────────────────────────
    try:
        conn = _db(BLAZE_DB)
        tables = [t[0] for t in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        if "cron_runs" in tables:
            recent_runs = conn.execute(
                "SELECT script_name, status, started_at FROM cron_runs ORDER BY started_at DESC LIMIT 10"
            ).fetchall()
            stats["cron"] = [{"script": r[0], "status": r[1], "at": r[2]} for r in recent_runs]
        else:
            stats["cron"] = []

        # API call count today
        if "api_calls" in tables:
            api_today = conn.execute(
                "SELECT COUNT(*) FROM api_calls WHERE created_at > date('now')"
            ).fetchone()[0]
            stats["api"]["calls_today"] = api_today
        conn.close()
    except Exception as e:
        stats["cron"] = {"error": str(e)}

    # ── Log files ─────────────────────────────────────────────────────────────
    try:
        if LOGS_DIR.exists():
            log_files = list(LOGS_DIR.glob("*.log")) + list(LOGS_DIR.glob("*.out.log"))
            stats["logs"] = {"count": len(log_files)}
            # Newest entry from FastAPI log
            fastapi_log = LOGS_DIR / "fastapi.out.log"
            if fastapi_log.exists():
                lines = fastapi_log.read_text().splitlines()
                stats["logs"]["fastapi_last"] = lines[-1][:120] if lines else ""
    except Exception as e:
        stats["logs"] = {"error": str(e)}

    # ── WhatsApp / iMessage channel status ───────────────────────────────────
    wa_token_set = bool(os.getenv("WHATSAPP_TOKEN", ""))
    stats["channels"] = {
        "whatsapp": {"configured": wa_token_set, "number": "+13464015841"},
        "imessage": {"configured": True, "accounts": ["caio@astrocleanings.com", "+17275985314"]},
        "telegram": {"configured": True, "bots": ["@blazenbailey_bot", "@agentastro_bot", "@agentcc_creativedirectorbot"]},
    }

    return stats
