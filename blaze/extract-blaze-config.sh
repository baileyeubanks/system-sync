#!/bin/bash
# ============================================================================
# extract-blaze-config.sh — Run this ON the Mac Mini to capture everything
#
# Usage: ssh blaze-master
#        cd ~/system-sync/blaze
#        chmod +x extract-blaze-config.sh
#        ./extract-blaze-config.sh
#
# This script extracts all LaunchAgent plists, Python services, OpenClaw
# config, FastAPI source, and crontabs into a git-trackable structure.
# After running, commit the output to system-sync.
# ============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

echo "=== Blaze V4 Config Extraction ==="
echo "Time: $(date -u)"
echo "Host: $(hostname)"
echo "Output: $SCRIPT_DIR"
echo ""

# ── 1. LaunchAgents ──────────────────────────────────────────────────────
echo "--- Extracting LaunchAgents ---"
mkdir -p "$SCRIPT_DIR/launchagents"

# User-level LaunchAgents (where most services live)
if [ -d "$HOME/Library/LaunchAgents" ]; then
  for plist in "$HOME/Library/LaunchAgents"/*.plist; do
    [ -f "$plist" ] && cp "$plist" "$SCRIPT_DIR/launchagents/"
  done
  echo "  Copied $(ls "$SCRIPT_DIR/launchagents/"*.plist 2>/dev/null | wc -l | tr -d ' ') plists from ~/Library/LaunchAgents"
fi

# System-level (if any were installed there)
if [ -d "/Library/LaunchAgents" ]; then
  for plist in /Library/LaunchAgents/com.blaze*.plist /Library/LaunchAgents/com.acs*.plist /Library/LaunchAgents/com.cc*.plist; do
    [ -f "$plist" ] && cp "$plist" "$SCRIPT_DIR/launchagents/"
  done
fi

# List all loaded agents for reference
launchctl list 2>/dev/null | grep -i -E "blaze|acs|astro|imsg|gmail|openclaw|cc|relay|watcher|bridge" > "$SCRIPT_DIR/launchagents/loaded-agents.txt" 2>/dev/null || true
echo "  Saved loaded agent list"

# ── 2. Python Services ──────────────────────────────────────────────────
echo ""
echo "--- Extracting Python Services ---"
mkdir -p "$SCRIPT_DIR/services"

# Common locations for the Python scripts
SEARCH_DIRS=(
  "$HOME/ACS_CC_AUTOBOT/blaze-v4"
)

for dir in "${SEARCH_DIRS[@]}"; do
  if [ -d "$dir" ]; then
    echo "  Found: $dir"
    # Copy Python files
    find "$dir" -name "*.py" -not -path "*/venv/*" -not -path "*/.venv/*" -not -path "*/node_modules/*" -not -path "*/__pycache__/*" | while read f; do
      rel_path="${f#$dir/}"
      dest_dir="$SCRIPT_DIR/services/$(dirname "$rel_path")"
      mkdir -p "$dest_dir"
      cp "$f" "$dest_dir/"
    done
    # Copy config files
    find "$dir" -maxdepth 3 \( -name "*.yml" -o -name "*.yaml" -o -name "*.toml" -o -name "*.json" -o -name "*.ini" -o -name "*.cfg" -o -name "*.env.example" -o -name "requirements*.txt" -o -name "Pipfile" -o -name "pyproject.toml" \) -not -path "*/venv/*" -not -path "*/node_modules/*" | while read f; do
      rel_path="${f#$dir/}"
      dest_dir="$SCRIPT_DIR/services/$(dirname "$rel_path")"
      mkdir -p "$dest_dir"
      cp "$f" "$dest_dir/"
    done
  fi
done

# Specifically look for the known scripts
for script in imsg_relay.py imsg_watcher.py gmail_monitor.py netlify_event_bridge.py; do
  found=$(find "$HOME/ACS_CC_AUTOBOT/blaze-v4/ops/scripts" -name "$script" 2>/dev/null | head -1)
  if [ -n "$found" ]; then
    echo "  Found $script at: $found"
  else
    echo "  WARNING: $script not found!"
  fi
done

# ── 3. OpenClaw / AI Agent Config ────────────────────────────────────────
echo ""
echo "--- Extracting OpenClaw Config ---"
mkdir -p "$SCRIPT_DIR/openclaw"

OPENCLAW_DIRS=(
  "$HOME/.openclaw"
)

for dir in "${OPENCLAW_DIRS[@]}"; do
  if [ -d "$dir" ]; then
    echo "  Found OpenClaw at: $dir"
    # Copy config (not model weights or large files)
    find "$dir" -maxdepth 3 \( -name "*.py" -o -name "*.yml" -o -name "*.yaml" -o -name "*.json" -o -name "*.toml" \) -not -path "*/venv/*" -not -path "*/node_modules/*" -not -path "*/sessions/*" -not -name "package-lock.json" -not -name "sessions.json" | while read f; do
      rel_path="${f#$dir/}"
      dest_dir="$SCRIPT_DIR/openclaw/$(dirname "$rel_path")"
      mkdir -p "$dest_dir"
      cp "$f" "$dest_dir/"
    done
  fi
done

# ── 4. FastAPI Service ───────────────────────────────────────────────────
echo ""
echo "--- Extracting FastAPI Config ---"

# Check if FastAPI is running and where
FASTAPI_PID=$(lsof -ti :8899 2>/dev/null || true)
if [ -n "$FASTAPI_PID" ]; then
  FASTAPI_CMD=$(ps -p "$FASTAPI_PID" -o command= 2>/dev/null || true)
  echo "  FastAPI running (PID $FASTAPI_PID): $FASTAPI_CMD"
  echo "$FASTAPI_CMD" > "$SCRIPT_DIR/services/fastapi-process.txt"
fi

# ── 5. Crontabs ──────────────────────────────────────────────────────────
echo ""
echo "--- Extracting Crontabs ---"
mkdir -p "$SCRIPT_DIR/cron"
crontab -l > "$SCRIPT_DIR/cron/user-crontab.txt" 2>/dev/null || echo "  No user crontab found"

# ── 6. Environment / Ports / Processes ───────────────────────────────────
echo ""
echo "--- System Snapshot ---"
mkdir -p "$SCRIPT_DIR/snapshot"

# Running services on key ports
echo "Listening ports:" > "$SCRIPT_DIR/snapshot/ports.txt"
lsof -iTCP -sTCP:LISTEN -P -n 2>/dev/null | grep -E "18789|8899|5432|3000|8000|8080" >> "$SCRIPT_DIR/snapshot/ports.txt" 2>/dev/null || true

# Python processes
echo "Python processes:" > "$SCRIPT_DIR/snapshot/python-processes.txt"
ps aux | grep -i python | grep -v grep >> "$SCRIPT_DIR/snapshot/python-processes.txt" 2>/dev/null || true

# Node processes
echo "Node processes:" > "$SCRIPT_DIR/snapshot/node-processes.txt"
ps aux | grep -i node | grep -v grep >> "$SCRIPT_DIR/snapshot/node-processes.txt" 2>/dev/null || true

# Disk usage
echo "Disk usage:" > "$SCRIPT_DIR/snapshot/disk.txt"
df -h / >> "$SCRIPT_DIR/snapshot/disk.txt"

# macOS version
echo "macOS version:" > "$SCRIPT_DIR/snapshot/system-info.txt"
sw_vers >> "$SCRIPT_DIR/snapshot/system-info.txt" 2>/dev/null || true
echo "" >> "$SCRIPT_DIR/snapshot/system-info.txt"
echo "Hardware:" >> "$SCRIPT_DIR/snapshot/system-info.txt"
system_profiler SPHardwareDataType 2>/dev/null | grep -E "Model|Chip|Memory|Serial" >> "$SCRIPT_DIR/snapshot/system-info.txt" 2>/dev/null || true

# ── 7. Summary ───────────────────────────────────────────────────────────
echo ""
echo "=== Extraction Complete ==="
echo ""
echo "Directory structure:"
find "$SCRIPT_DIR" -not -path "*/.git/*" -type f | sort | sed "s|$SCRIPT_DIR/||"
echo ""
echo "Next steps:"
echo "  cd $(dirname "$SCRIPT_DIR")"
echo "  git add blaze/"
echo "  git commit -m 'chore: extract Mac Mini (Blaze V4) config for version control'"
echo "  git push origin main"
