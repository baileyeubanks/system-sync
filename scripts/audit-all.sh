#!/bin/bash
# audit-all.sh — Runs all audit checks and generates combined report
#
# Usage: ./scripts/audit-all.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
REPORTS_DIR="$REPO_DIR/reports"

mkdir -p "$REPORTS_DIR"

echo "=== Full System Audit ==="
echo "Time: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo ""

PASS=0
FAIL=0
WARN=0

# 1. Health check
echo "--- Running health check ---"
if node "$SCRIPT_DIR/health-check.js" 2>&1; then
  PASS=$((PASS + 1))
else
  FAIL=$((FAIL + 1))
fi
echo ""

# 2. Sync check
echo "--- Running sync check ---"
if node "$SCRIPT_DIR/sync-check.js" 2>&1; then
  PASS=$((PASS + 1))
else
  WARN=$((WARN + 1))
fi
echo ""

# 3. Migration inventory
echo "--- Migration inventory ---"
bash "$SCRIPT_DIR/migrate.sh" --list 2>&1
echo ""

# 4. Git status across repos
echo "--- Git Status (local repos) ---"
for repo in acs-website contentco-op-website codeliver coscript coedit portfolio; do
  REPO_PATH="$HOME/$repo"
  if [ -d "$REPO_PATH/.git" ]; then
    BRANCH=$(cd "$REPO_PATH" && git branch --show-current 2>/dev/null || echo "unknown")
    DIRTY=$(cd "$REPO_PATH" && git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
    echo "  $repo: branch=$BRANCH dirty_files=$DIRTY"
  else
    echo "  $repo: not cloned locally"
  fi
done
echo ""

# Summary
TOTAL=$((PASS + FAIL + WARN))
echo "=== Audit Summary ==="
echo "Checks: $TOTAL (pass=$PASS, fail=$FAIL, warn=$WARN)"
echo "Time: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Write combined report
cat > "$REPORTS_DIR/audit-latest.json" <<EOF
{
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "checks": {
    "health": $([ -f "$REPORTS_DIR/health-latest.json" ] && cat "$REPORTS_DIR/health-latest.json" || echo '{"status":"skipped"}'),
    "sync": $([ -f "$REPORTS_DIR/sync-latest.json" ] && cat "$REPORTS_DIR/sync-latest.json" || echo '{"status":"skipped"}')
  },
  "summary": {
    "total": $TOTAL,
    "pass": $PASS,
    "fail": $FAIL,
    "warn": $WARN
  }
}
EOF

echo ""
echo "Full report: reports/audit-latest.json"
