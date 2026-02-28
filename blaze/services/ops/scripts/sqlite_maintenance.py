#!/usr/bin/env python3
"""
sqlite_maintenance.py — Nightly SQLite VACUUM + WAL checkpoint for all Blaze DBs.
Runs at 2:30 AM via com.blaze.db-maintenance LaunchAgent.
"""
from __future__ import annotations
import os
import sqlite3
from datetime import datetime
from pathlib import Path

DATA_ROOT = Path.home() / "blaze-data"
API_ROOT = Path.home() / "Blaze-V4"

DBS = [
    DATA_ROOT / "contacts/contacts.db",
    DATA_ROOT / "knowledge.db",
    DATA_ROOT / "blaze.db",
    DATA_ROOT / "event_stream.db",
    DATA_ROOT / "cron-log.db",
    DATA_ROOT / "usage.db",
    DATA_ROOT / "netlify_bridge_seen.db",
]


def ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def maintain(db_path: Path) -> None:
    if not db_path.exists():
        return
    size_before = db_path.stat().st_size
    wal = db_path.with_suffix(".db-wal")
    wal_before = wal.stat().st_size if wal.exists() else 0

    try:
        conn = sqlite3.connect(str(db_path), timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_checkpoint(RESTART)")
        conn.execute("VACUUM")
        conn.close()
        size_after = db_path.stat().st_size
        wal_after = wal.stat().st_size if wal.exists() else 0
        saved = (size_before - size_after) + (wal_before - wal_after)
        print(
            "[%s] %s OK — size %dKB→%dKB, WAL %dKB→%dKB, saved %dKB"
            % (
                ts(),
                db_path.name,
                size_before // 1024,
                size_after // 1024,
                wal_before // 1024,
                wal_after // 1024,
                saved // 1024,
            ),
            flush=True,
        )
    except Exception as exc:
        print("[%s] ERROR %s: %s" % (ts(), db_path.name, exc), flush=True)


def main():
    print("[%s] Starting SQLite maintenance..." % ts(), flush=True)
    for db in DBS:
        maintain(db)
    print("[%s] Done." % ts(), flush=True)


if __name__ == "__main__":
    main()
