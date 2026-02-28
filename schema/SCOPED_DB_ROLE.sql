-- =============================================================================
-- SCOPED DATABASE ROLE FOR CO-PRODUCTS
-- =============================================================================
-- Purpose:
--   Reduces blast radius by limiting service_role exposure for CoDeliver,
--   CoScript, and Content Co-op services. This role has NO access to:
--   - Financial tables (invoices, payments, revenue)
--   - Auth/security tables (admin_auth, crew_auth, auth_tokens)
--   - CRM tables (contacts, jobs)
--   - Sensitive settings and internal tables
--
-- Usage:
--   1. Run this entire script in Supabase SQL Editor
--   2. Generate a new API key scoped to co_products_role
--   3. Update CoDeliver, CoScript, contentco-op env vars to use new key
--   4. Keep full service_role key only in acs-website for full access
--
-- Idempotency:
--   Safe to re-run. All operations use IF NOT EXISTS / DROP IF EXISTS.
-- =============================================================================

-- Drop existing role to ensure clean state (idempotent)
DROP ROLE IF EXISTS co_products_role CASCADE;

-- Create the scoped role
CREATE ROLE co_products_role NOLOGIN;

-- =============================================================================
-- CODELIVER TABLES
-- =============================================================================
-- Assets platform: video/creative review with versioning, comments, approvals

GRANT USAGE ON SCHEMA public TO co_products_role;

GRANT SELECT, INSERT, UPDATE ON public.assets TO co_products_role;
GRANT SELECT, INSERT, UPDATE ON public.versions TO co_products_role;
GRANT SELECT, INSERT, UPDATE ON public.comments TO co_products_role;
GRANT SELECT, INSERT, UPDATE ON public.approvals TO co_products_role;
GRANT SELECT, INSERT ON public.activity_log TO co_products_role;

-- Allow sequence access for any auto-incrementing IDs
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO co_products_role;

-- =============================================================================
-- COSCRIPT TABLES
-- =============================================================================
-- Script generation and management

GRANT SELECT, INSERT, UPDATE ON public.script_jobs TO co_products_role;
GRANT SELECT, INSERT, UPDATE ON public.script_variants TO co_products_role;
GRANT SELECT, INSERT, UPDATE ON public.script_fixes TO co_products_role;
GRANT SELECT, INSERT, UPDATE ON public.watchlists TO co_products_role;
GRANT SELECT, INSERT ON public.vault_items TO co_products_role;
GRANT SELECT, INSERT, UPDATE ON public.drafts TO co_products_role;
GRANT SELECT, INSERT ON public.share_links TO co_products_role;
GRANT SELECT ON public.briefs TO co_products_role;
GRANT SELECT ON public.outlier_scores TO co_products_role;

-- =============================================================================
-- CONTENT CO-OP TABLES
-- =============================================================================
-- Brief management and file storage

GRANT SELECT, INSERT, UPDATE ON public.creative_briefs TO co_products_role;
GRANT SELECT, INSERT, UPDATE ON public.brief_files TO co_products_role;
GRANT SELECT, INSERT, UPDATE ON public.brief_messages TO co_products_role;
GRANT SELECT, INSERT, UPDATE ON public.brief_status_history TO co_products_role;

-- =============================================================================
-- SHARED TABLES
-- =============================================================================
-- Cross-platform communication and AI features

-- Shared communication
GRANT SELECT, INSERT, UPDATE ON public.conversations TO co_products_role;
GRANT SELECT, INSERT, UPDATE ON public.messages TO co_products_role;

-- Shared AI profiles (content generation context)
GRANT SELECT, INSERT, UPDATE ON public.ai_profiles TO co_products_role;

-- Event queue (for async processing like Mac Mini bridge)
GRANT INSERT ON public.events TO co_products_role;

-- User profiles (required for org context)
GRANT SELECT ON public.user_profiles TO co_products_role;

-- Organization tables (for multi-tenant context)
GRANT SELECT ON public.orgs TO co_products_role;
GRANT SELECT ON public.org_members TO co_products_role;

-- =============================================================================
-- EXPLICITLY REVOKE ACCESS TO SENSITIVE TABLES
-- =============================================================================
-- These revokes ensure no accidental grants from default permissions

REVOKE ALL ON public.contacts FROM co_products_role;
REVOKE ALL ON public.jobs FROM co_products_role;
REVOKE ALL ON public.invoices FROM co_products_role;
REVOKE ALL ON public.invoice_payments FROM co_products_role;
REVOKE ALL ON public.payments FROM co_products_role;
REVOKE ALL ON public.revenue_snapshots FROM co_products_role;
REVOKE ALL ON public.crew_members FROM co_products_role;
REVOKE ALL ON public.admin_auth FROM co_products_role;
REVOKE ALL ON public.crew_auth FROM co_products_role;
REVOKE ALL ON public.auth_tokens FROM co_products_role;
REVOKE ALL ON public.settings FROM co_products_role;
REVOKE ALL ON public.cron_health FROM co_products_role;
REVOKE ALL ON public.businesses FROM co_products_role;

-- =============================================================================
-- VERIFICATION QUERIES
-- =============================================================================
-- Run these to confirm the role was created correctly

-- Check that co_products_role exists
-- SELECT * FROM pg_roles WHERE rolname = 'co_products_role';

-- List all tables accessible to co_products_role
-- SELECT
--   schemaname,
--   tablename
-- FROM pg_tables
-- WHERE schemaname = 'public'
-- ORDER BY tablename;

-- Check specific permissions (example for assets table)
-- SELECT
--   grantee,
--   privilege_type
-- FROM information_schema.role_table_grants
-- WHERE table_name = 'assets' AND grantee = 'co_products_role'
-- ORDER BY privilege_type;

-- List all permissions granted to co_products_role across all tables
-- SELECT
--   table_name,
--   STRING_AGG(privilege_type, ', ' ORDER BY privilege_type) as permissions
-- FROM information_schema.role_table_grants
-- WHERE grantee = 'co_products_role' AND table_schema = 'public'
-- GROUP BY table_name
-- ORDER BY table_name;

-- =============================================================================
-- NEXT STEPS AFTER RUNNING THIS SCRIPT
-- =============================================================================
--
-- 1. In Supabase Dashboard, go to Project Settings > API
-- 2. Create a new JWT with sub (subject) = 'co_products_role'
-- 3. Update environment variables in:
--    - codeliver (Netlify): SUPABASE_KEY
--    - coscript (Netlify): SUPABASE_KEY
--    - contentco-op-website (Vercel): SUPABASE_KEY
-- 4. Test API calls from each service to confirm access works
-- 5. Monitor error logs for permission denied errors during testing
-- 6. Keep full service_role key only in acs-website Netlify env
-- 7. Document key rotation schedule (recommended: quarterly)
--
-- =============================================================================
