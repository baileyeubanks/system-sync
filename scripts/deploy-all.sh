#!/bin/bash
# deploy-all.sh — Triggers deploys across all Netlify/Vercel sites
#
# Usage: ./scripts/deploy-all.sh [--dry-run]
# Requires: netlify-cli, gh CLI

set -e

DRY_RUN=false
[ "$1" = "--dry-run" ] && DRY_RUN=true

REPOS=(
  "acs-website"
  "codeliver"
  "coscript"
  "coedit"
  "contentco-op-website"
)

echo "=== Deploy All ==="
echo "Time: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "Dry run: $DRY_RUN"
echo ""

for repo in "${REPOS[@]}"; do
  echo "--- $repo ---"

  if [ "$DRY_RUN" = true ]; then
    echo "  [DRY RUN] Would trigger deploy for $repo"
    continue
  fi

  # Check if repo has a Netlify site linked
  if [ -f "../$repo/netlify.toml" ] || [ -f "../$repo/.netlify/state.json" ]; then
    echo "  Platform: Netlify"
    # Trigger via Netlify build hook or CLI
    # netlify deploy --dir="../$repo" --prod
    echo "  NOTE: Run 'cd ../$repo && netlify deploy --prod' to deploy"
  else
    echo "  Platform: Vercel (or manual)"
    echo "  NOTE: Push to main branch to trigger Vercel deploy"
  fi
  echo ""
done

echo "=== Done ==="
