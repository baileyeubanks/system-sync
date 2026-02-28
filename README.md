# system-sync

Coordination layer for the ACS + Content Co-op ecosystem. Single source of truth for schema, functions, environment variables, and cross-repo dependencies across 6 repositories.

## What's Inside

```
system-sync/
├── schema/
│   └── master-schema.sql       # Every Supabase table with ownership + columns
├── registry/
│   ├── functions.json           # All serverless functions across all repos
│   ├── env-vars.json            # Environment variables per repo + shared secrets
│   └── dependencies.json        # Cross-repo dependencies + data flows
├── scripts/
│   ├── health-check.js          # HTTP health check for all services
│   ├── sync-check.js            # Cross-repo consistency validation
│   ├── deploy-all.sh            # Trigger deploys across all sites
│   ├── migrate.sh               # List/apply Supabase migrations
│   └── audit-all.sh             # Full system audit (health + sync + git status)
├── .github/workflows/
│   ├── nightly-sync.yml         # Daily sync + health checks
│   └── schema-validate.yml      # Validate registry JSON on push
├── reports/                     # Generated audit reports (gitignored)
├── SYSTEM_CONTEXT.md            # Complete architectural reference
└── README.md
```

## Repos Tracked

| Repo | Domain | Platform |
|------|--------|----------|
| [acs-website](https://github.com/baileyeubanks/acs-website) | astrocleanings.com | Netlify |
| [contentco-op-website](https://github.com/baileyeubanks/contentco-op-website) | contentco-op.com | Vercel |
| [codeliver](https://github.com/baileyeubanks/codeliver) | codeliver.cc | Netlify |
| [coscript](https://github.com/baileyeubanks/coscript) | coscript.cc | Netlify |
| [coedit](https://github.com/baileyeubanks/coedit) | coedit.cc | Netlify |
| [portfolio](https://github.com/baileyeubanks/portfolio) | baileyeubanks.com | Static |

## Quick Start

```bash
# Run full audit
chmod +x scripts/*.sh
./scripts/audit-all.sh

# Health check only
node scripts/health-check.js

# Sync validation only
node scripts/sync-check.js

# List all migrations
./scripts/migrate.sh --list
```

## Keeping It Updated

When you add a new table, function, or env var to any repo:

1. Update `schema/master-schema.sql` with the table definition
2. Update `registry/functions.json` with the new function
3. Update `registry/env-vars.json` if new env vars are needed
4. Update `registry/dependencies.json` if cross-repo relationships change
5. Push to main — GitHub Actions validates the changes

## Nightly Automation

GitHub Actions runs at midnight CST:
- **health-check**: Pings all service URLs
- **sync-check**: Validates registry consistency
- Reports uploaded as artifacts (30-day retention)
