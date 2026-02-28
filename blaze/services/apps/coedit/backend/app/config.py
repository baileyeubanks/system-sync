import os
from pathlib import Path

# Paths — all on local disk
LOCAL_DATA = Path("/Users/_mxappservice/blaze-data/coedit")
DB_PATH = LOCAL_DATA / "coedit.db"
DATA_DIR = LOCAL_DATA
PROJECTS_DIR = LOCAL_DATA / "projects"
TMP_DIR = LOCAL_DATA / "tmp"
LOG_DIR = LOCAL_DATA / "logs"

# Ensure directories exist
for d in [LOCAL_DATA, PROJECTS_DIR, TMP_DIR, LOG_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Auth
SECRET_KEY = os.environ.get("COEDIT_SECRET", "coedit-dev-secret-change-in-prod")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 72
SHARE_TOKEN_EXPIRE_DAYS = 30

# FFmpeg
FFMPEG = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"

# Redis
REDIS_URL = "redis://localhost:6379"

# Upload
MAX_UPLOAD_SIZE = 12 * 1024 * 1024 * 1024  # 12GB
CHUNK_SIZE = 5 * 1024 * 1024  # 5MB chunks

# Transcode settings
TRANSCODE_VIDEO_BITRATE = "8M"
TRANSCODE_MAX_BITRATE = "10M"
TRANSCODE_AUDIO_BITRATE = "192k"
TRANSCODE_FPS = 24  # Force CFR

# Server
HOST = "127.0.0.1"
PORT = 8000

# Email (SMTP)
SMTP_HOST = os.environ.get("COEDIT_SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("COEDIT_SMTP_PORT", "587"))
SMTP_USER = os.environ.get("COEDIT_SMTP_USER", "blaze@contentco-op.com")
SMTP_PASS = os.environ.get("COEDIT_SMTP_PASS", "")
SMTP_FROM = os.environ.get("COEDIT_SMTP_FROM", "Co-Edit <blaze@contentco-op.com>")
SMTP_ENABLED = bool(SMTP_PASS)  # Only send if password is configured

# App URL (for links in emails)
APP_URL = os.environ.get("COEDIT_APP_URL", "http://localhost:8000")
