import aiosqlite
import sqlite3
from pathlib import Path
from typing import Optional

from app.config import DB_PATH

SCHEMA = """
-- Users (team members)
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'editor',
    avatar_url TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

-- Projects
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    owner_id TEXT NOT NULL REFERENCES users(id),
    branding TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

-- Folders
CREATE TABLE IF NOT EXISTS folders (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    parent_id TEXT REFERENCES folders(id),
    name TEXT NOT NULL,
    sort_order INTEGER DEFAULT 0,
    created_at REAL NOT NULL
);

-- Assets
CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    folder_id TEXT REFERENCES folders(id),
    name TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'uploading',
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

-- Asset versions
CREATE TABLE IF NOT EXISTS asset_versions (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    version_num INTEGER NOT NULL,
    original_path TEXT NOT NULL,
    proxy_path TEXT,
    thumbnail_path TEXT,
    sprite_path TEXT,
    file_size INTEGER,
    duration_ms INTEGER,
    width INTEGER,
    height INTEGER,
    fps REAL,
    codec TEXT,
    transcode_job_id TEXT,
    created_at REAL NOT NULL,
    UNIQUE(asset_id, version_num)
);

-- Comments
CREATE TABLE IF NOT EXISTS comments (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    version_id TEXT NOT NULL REFERENCES asset_versions(id),
    parent_id TEXT REFERENCES comments(id),
    author_id TEXT REFERENCES users(id),
    author_name TEXT NOT NULL,
    share_link_id TEXT,
    frame_start INTEGER,
    frame_end INTEGER,
    timecode_start TEXT,
    timecode_end TEXT,
    pin_x REAL,
    pin_y REAL,
    annotation_type TEXT,
    annotation_data TEXT,
    body TEXT NOT NULL,
    is_private INTEGER DEFAULT 0,
    is_resolved INTEGER DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

-- Share links (asset_id or project_id — one must be set)
CREATE TABLE IF NOT EXISTS share_links (
    id TEXT PRIMARY KEY,
    asset_id TEXT REFERENCES assets(id) ON DELETE CASCADE,
    project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
    version_id TEXT REFERENCES asset_versions(id),
    mode TEXT NOT NULL,
    token TEXT UNIQUE NOT NULL,
    password_hash TEXT,
    allow_download INTEGER DEFAULT 0,
    expires_at REAL,
    last_nudged_at REAL,
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at REAL NOT NULL
);

-- Approvals
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES assets(id),
    version_id TEXT NOT NULL REFERENCES asset_versions(id),
    share_link_id TEXT REFERENCES share_links(id),
    reviewer_name TEXT NOT NULL,
    status TEXT NOT NULL,
    note TEXT,
    created_at REAL NOT NULL
);

-- Notifications
CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    type TEXT NOT NULL,
    reference_id TEXT,
    asset_id TEXT REFERENCES assets(id),
    message TEXT NOT NULL,
    is_read INTEGER DEFAULT 0,
    email_sent INTEGER DEFAULT 0,
    created_at REAL NOT NULL
);

-- Transcode jobs
CREATE TABLE IF NOT EXISTS transcode_jobs (
    id TEXT PRIMARY KEY,
    version_id TEXT NOT NULL REFERENCES asset_versions(id),
    status TEXT NOT NULL DEFAULT 'queued',
    progress INTEGER DEFAULT 0,
    error TEXT,
    started_at REAL,
    completed_at REAL,
    created_at REAL NOT NULL
);

-- Project membership
CREATE TABLE IF NOT EXISTS project_members (
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id),
    role TEXT NOT NULL DEFAULT 'editor',
    created_at REAL NOT NULL,
    PRIMARY KEY (project_id, user_id)
);

-- Client briefs (public form submissions)
CREATE TABLE IF NOT EXISTS client_briefs (
    id TEXT PRIMARY KEY,
    contact_name TEXT NOT NULL,
    email TEXT NOT NULL,
    company TEXT,
    project_type TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at REAL NOT NULL
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_client_briefs_created ON client_briefs(created_at);
CREATE INDEX IF NOT EXISTS idx_comments_asset_version ON comments(asset_id, version_id);
CREATE INDEX IF NOT EXISTS idx_comments_frame ON comments(version_id, frame_start);
CREATE INDEX IF NOT EXISTS idx_share_links_token ON share_links(token);
CREATE INDEX IF NOT EXISTS idx_share_links_project ON share_links(project_id);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, is_read);
CREATE INDEX IF NOT EXISTS idx_asset_versions_asset ON asset_versions(asset_id, version_num);
CREATE INDEX IF NOT EXISTS idx_transcode_jobs_status ON transcode_jobs(status);
CREATE INDEX IF NOT EXISTS idx_assets_project ON assets(project_id);
CREATE INDEX IF NOT EXISTS idx_folders_project ON folders(project_id);
"""

MIGRATIONS = [
    # Migration 1: Add project_id and last_nudged_at to share_links
    "ALTER TABLE share_links ADD COLUMN project_id TEXT",
    "ALTER TABLE share_links ADD COLUMN last_nudged_at REAL",
    "CREATE INDEX IF NOT EXISTS idx_share_links_project ON share_links(project_id)",
    # Migration 2: Add branding to projects
    "ALTER TABLE projects ADD COLUMN logo_path TEXT",
    "ALTER TABLE projects ADD COLUMN accent_color TEXT DEFAULT '#3b82f6'",
]


def init_db():
    """Synchronous init for startup."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)

    # Run migrations (idempotent — silently skip if column exists)
    for migration in MIGRATIONS:
        try:
            conn.execute(migration.strip())
        except sqlite3.OperationalError:
            pass  # Column already exists

    conn.commit()
    conn.close()


async def get_db() -> aiosqlite.Connection:
    """Get an async database connection."""
    db = await aiosqlite.connect(str(DB_PATH))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys=ON")
    return db
