# F004: Supabase Key Scoping

## Phase 1: COMPLETE
- PostgreSQL role `co_products_role` created
- GRANT SELECT/INSERT/UPDATE/DELETE on co-product tables
- GRANT USAGE on public schema

## Phase 2: PENDING (Requires Dashboard Action)

### Bailey Must Do:
1. Go to Supabase Dashboard → SQL Editor
2. Generate a JWT signed with the project's JWT secret
3. The JWT payload should include `role: 'co_products_role'`
4. Use this scoped JWT in CoEdit/CoScript/CoDeliver env vars instead of the service_role key

### Why This Matters
- Currently co-products use the `service_role` key (full DB access)
- A scoped key limits blast radius — co-products can only touch their own tables
- If a co-product key leaks, attacker can't access ACS customer data

### Tables Accessible to co_products_role
- scripts, script_versions, script_templates
- watchlists, watchlist_channels
- review_assets, review_sessions, review_projects
- review_links, review_annotations, review_versions
- review_comments, review_notifications
- ai_profiles (read-only)
