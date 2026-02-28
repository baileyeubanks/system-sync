#!/bin/bash
# migrate.sh — Lists all pending Supabase migrations across repos
#
# Usage: ./scripts/migrate.sh [--list | --apply <migration-file>]
# Note: Actual migration execution must be done via Supabase dashboard or CLI

set -e

REPOS_DIR="${REPOS_DIR:-..}"
ACTION="${1:---list}"

echo "=== Migration Manager ==="
echo "Time: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo ""

# Only acs-website has migrations currently
MIGRATION_DIRS=(
  "acs-website/supabase/migrations"
)

case "$ACTION" in
  --list)
    echo "All migrations found:"
    echo ""
    for dir in "${MIGRATION_DIRS[@]}"; do
      full_path="$REPOS_DIR/$dir"
      if [ -d "$full_path" ]; then
        echo "--- ${dir%%/*} ---"
        ls -1 "$full_path"/*.sql 2>/dev/null | while read f; do
          basename "$f"
        done
        echo ""
      fi
    done
    ;;

  --apply)
    MIGRATION="$2"
    if [ -z "$MIGRATION" ]; then
      echo "Error: Provide migration file path"
      echo "Usage: ./scripts/migrate.sh --apply path/to/migration.sql"
      exit 1
    fi

    if [ ! -f "$MIGRATION" ]; then
      echo "Error: Migration file not found: $MIGRATION"
      exit 1
    fi

    echo "Migration: $MIGRATION"
    echo ""
    echo "To apply this migration:"
    echo "  1. Open Supabase dashboard: https://supabase.com/dashboard/project/briokwdoonawhxisbydy/sql"
    echo "  2. Paste the contents of: $MIGRATION"
    echo "  3. Execute"
    echo ""
    echo "Or use Supabase CLI:"
    echo "  supabase db push --db-url postgresql://... < $MIGRATION"
    ;;

  *)
    echo "Usage: ./scripts/migrate.sh [--list | --apply <migration-file>]"
    exit 1
    ;;
esac
