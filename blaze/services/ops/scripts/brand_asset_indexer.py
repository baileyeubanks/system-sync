#!/usr/bin/env python3
"""
Brand Asset Indexer
Walks the brand/ directory tree, catalogs every asset into asset_index.db.
Reads image dimensions via PIL if available.

Usage:
    python3 brand_asset_indexer.py          # full index
    python3 brand_asset_indexer.py --stats  # print summary stats
"""

import os
import sqlite3
import sys
from pathlib import Path

BRAND_BASE = Path("/Users/_mxappservice/blaze-data/brand")
DB_PATH = BRAND_BASE / "asset_index.db"

# Category mapping from directory name
DIR_TO_CATEGORY = {
    "logos": "logo",
    "photos": "photo",
    "icons": "icon",
    "templates": "template",
}

# Try to import PIL for image dimensions
try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_unit TEXT NOT NULL,
            category TEXT NOT NULL,
            filename TEXT NOT NULL,
            filepath TEXT UNIQUE NOT NULL,
            file_type TEXT,
            file_size INTEGER,
            width INTEGER,
            height INTEGER,
            tags TEXT DEFAULT '',
            description TEXT DEFAULT '',
            usage_context TEXT DEFAULT '',
            is_primary INTEGER DEFAULT 0,
            indexed_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_assets_bu ON assets(business_unit)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_assets_cat ON assets(category)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_assets_filename ON assets(filename)")
    conn.commit()


def get_image_dims(filepath):
    """Return (width, height) or (None, None)."""
    if not HAS_PIL:
        return None, None
    try:
        with Image.open(filepath) as img:
            return img.size
    except Exception:
        return None, None


def auto_tags(filename, category):
    """Generate basic tags from filename and category."""
    tags = [category]
    name_lower = filename.lower()

    # Common tag patterns
    if "dark" in name_lower or "black" in name_lower:
        tags.append("dark-bg")
    if "white" in name_lower or "light" in name_lower:
        tags.append("light-bg")
    if "blue" in name_lower:
        tags.append("blue")
    if "full" in name_lower:
        tags.append("full")
    if "mark" in name_lower:
        tags.append("mark")
    if "lockup" in name_lower:
        tags.append("lockup")
    if "spiral" in name_lower:
        tags.append("spiral")
    if "wordmark" in name_lower:
        tags.append("wordmark")
    if "team" in name_lower or "crew" in name_lower:
        tags.append("team")
    if "clean" in name_lower:
        tags.append("cleaning")
    if "app" in name_lower or "icon" in name_lower:
        tags.append("app-icon")
    if "email" in name_lower:
        tags.append("email")
    if "gapps" in name_lower:
        tags.append("google-apps")

    return ",".join(tags)


def usage_context(filename, category):
    """Infer usage context from filename."""
    name_lower = filename.lower()
    if category == "logo":
        if "lockup" in name_lower or "full" in name_lower:
            return "print-header,web-nav,document"
        if "spiral" in name_lower or "mark" in name_lower:
            return "favicon,app-icon,social-avatar"
        if "email" in name_lower:
            return "email-signature"
        if "gapps" in name_lower:
            return "google-workspace"
        if "wordmark" in name_lower:
            return "text-only"
    if category == "photo":
        return "social,web,print"
    if category == "icon":
        return "ui,web,print"
    if category == "template":
        return "document-generation"
    return ""


def is_primary_asset(filename, category, bu):
    """Flag primary assets."""
    name_lower = filename.lower()
    if bu == "acs" and category == "logo" and name_lower == "full-logo.png":
        return 1
    if bu == "cc" and category == "logo" and name_lower == "lockup-large.png":
        return 1
    return 0


def index_all():
    conn = sqlite3.connect(str(DB_PATH))
    init_db(conn)

    count = 0
    for bu in ("acs", "cc"):
        bu_dir = BRAND_BASE / bu
        if not bu_dir.exists():
            continue

        for dirname, category in DIR_TO_CATEGORY.items():
            asset_dir = bu_dir / dirname
            if not asset_dir.exists():
                continue

            for f in sorted(asset_dir.iterdir()):
                if not f.is_file() or f.name.startswith("."):
                    continue

                filepath = str(f)
                file_type = f.suffix.lower().lstrip(".")
                file_size = f.stat().st_size
                width, height = get_image_dims(filepath)
                tags = auto_tags(f.name, category)
                usage = usage_context(f.name, category)
                primary = is_primary_asset(f.name, category, bu)

                # Upsert
                conn.execute("""
                    INSERT INTO assets (business_unit, category, filename, filepath, file_type,
                        file_size, width, height, tags, usage_context, is_primary)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(filepath) DO UPDATE SET
                        file_size = excluded.file_size,
                        width = excluded.width,
                        height = excluded.height,
                        tags = excluded.tags,
                        usage_context = excluded.usage_context,
                        is_primary = excluded.is_primary,
                        indexed_at = datetime('now')
                """, (bu.upper(), category, f.name, filepath, file_type,
                      file_size, width, height, tags, usage, primary))
                count += 1

    conn.commit()
    conn.close()
    print("Indexed %d assets into %s" % (count, DB_PATH))


def print_stats():
    if not DB_PATH.exists():
        print("No database found at %s" % DB_PATH)
        return

    conn = sqlite3.connect(str(DB_PATH))
    total = conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
    print("Total assets: %d" % total)
    print()

    for row in conn.execute("SELECT business_unit, category, COUNT(*) FROM assets GROUP BY business_unit, category ORDER BY business_unit, category"):
        print("  %s / %s: %d" % (row[0], row[1], row[2]))

    print()
    primaries = conn.execute("SELECT business_unit, filename FROM assets WHERE is_primary = 1").fetchall()
    if primaries:
        print("Primary assets:")
        for r in primaries:
            print("  %s: %s" % (r[0], r[1]))

    conn.close()


if __name__ == "__main__":
    if "--stats" in sys.argv:
        print_stats()
    else:
        index_all()
        print()
        print_stats()
