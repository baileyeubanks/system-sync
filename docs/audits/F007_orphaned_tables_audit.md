# F007: Orphaned Tables Audit

## Status: VERIFIED (Feb 28, 2026)

## CoDeliver Table Name Duality

Both naming conventions exist in Supabase. Verified via direct SQL query.

### Tables Found (11 total):
| Table | Convention |
|-------|-----------|
| `assets` | Short |
| `review_assets` | Prefixed |
| `comments` | Short |
| `timecoded_comments` | Prefixed |
| `projects` | Short (shared) |
| `review_projects` | Prefixed |
| `review_sessions` | Prefixed |
| `review_links` | Prefixed |
| `review_annotations` | Prefixed |
| `review_versions` | Prefixed |
| `review_notifications` | Prefixed |

### Recommendation
- Code should standardize on `review_*` prefixed names
- Short-name tables may be legacy or duplicates — verify which are actively used by CoDeliver frontend

## Undocumented Tables (12)
Tables in Supabase not in master-schema.sql:
- calendar_sync, notification_log, conversations, activity_log
- messaging_channels, message_templates, usage_ledger
- plan_limits, org_plan_subscriptions, orgs
- ai_profiles, coedit_usage_current_period (view)

### Action
- Update master-schema.sql with these tables
