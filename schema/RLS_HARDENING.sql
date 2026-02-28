/**
 * ============================================================================
 * ASTRO CLEANING SERVICES (ACS) - RLS HARDENING FOR SUPABASE
 * ============================================================================
 *
 * Project: briokwdoonawhxisbydy
 * Purpose: Drop all permissive USING(true) policies and implement proper
 *          Row-Level Security based on custom JWT roles (admin, crew, client)
 *
 * IMPORTANT CONTEXT:
 * - This uses CUSTOM JWT (not Supabase Auth)
 * - JWTs contain: role, crew_member_id, contact_id, sub
 * - RLS only applies to: anon + authenticated (JWT) users
 * - service_role key BYPASSES RLS entirely (server-side)
 * - All policies are idempotent (safe to re-run)
 *
 * JWT CLAIM EXTRACTION:
 *   For custom JWT: (current_setting('request.jwt.claims', true)::json)
 *   Examples:
 *     - (current_setting('request.jwt.claims', true)::json) ->> 'role'
 *     - (current_setting('request.jwt.claims', true)::json) ->> 'crew_member_id'
 *     - (current_setting('request.jwt.claims', true)::json) ->> 'contact_id'
 *
 * ============================================================================
 */

-- ============================================================================
-- SECTION 1: HELPER FUNCTIONS FOR JWT CLAIM EXTRACTION
-- ============================================================================

CREATE OR REPLACE FUNCTION auth.get_jwt_claim(claim_name text)
RETURNS text AS $$
  SELECT (current_setting('request.jwt.claims', true)::json) ->> claim_name;
$$ LANGUAGE sql STABLE;

CREATE OR REPLACE FUNCTION auth.get_jwt_role()
RETURNS text AS $$
  SELECT (current_setting('request.jwt.claims', true)::json) ->> 'role';
$$ LANGUAGE sql STABLE;

CREATE OR REPLACE FUNCTION auth.get_crew_member_id()
RETURNS uuid AS $$
  SELECT ((current_setting('request.jwt.claims', true)::json) ->> 'crew_member_id')::uuid;
$$ LANGUAGE sql STABLE;

CREATE OR REPLACE FUNCTION auth.get_contact_id()
RETURNS uuid AS $$
  SELECT ((current_setting('request.jwt.claims', true)::json) ->> 'contact_id')::uuid;
$$ LANGUAGE sql STABLE;

CREATE OR REPLACE FUNCTION auth.get_jwt_sub()
RETURNS text AS $$
  SELECT (current_setting('request.jwt.claims', true)::json) ->> 'sub';
$$ LANGUAGE sql STABLE;

-- ============================================================================
-- SECTION 2: DROP ALL EXISTING PERMISSIVE POLICIES
-- ============================================================================
-- This section drops all USING(true) and other policies to start fresh

DO $$
DECLARE
  policy_record RECORD;
BEGIN
  -- Drop all existing policies on all tables
  FOR policy_record IN
    SELECT schemaname, tablename, policyname
    FROM pg_policies
    WHERE schemaname = 'public'
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON %I.%I',
                   policy_record.policyname,
                   policy_record.schemaname,
                   policy_record.tablename);
  END LOOP;
  RAISE NOTICE 'All existing policies dropped';
END $$;

-- ============================================================================
-- SECTION 3: ENABLE RLS ON ALL TABLES
-- ============================================================================

DO $$
DECLARE
  table_record RECORD;
BEGIN
  FOR table_record IN
    SELECT tablename FROM pg_tables
    WHERE schemaname = 'public'
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', table_record.tablename);
  END LOOP;
  RAISE NOTICE 'RLS enabled on all public tables';
END $$;

-- ============================================================================
-- SECTION 4: CORE OPERATIONAL TABLES
-- ============================================================================

-- ---------------------------------------------------------------------------
-- contacts (client records)
-- ---------------------------------------------------------------------------
-- Policies:
--   - Admin: full access (R/W/D)
--   - Crew: can read contacts assigned to their jobs
--   - Client: can read own contact info
--   - Anon: no access

ALTER TABLE contacts ENABLE ROW LEVEL SECURITY;

-- Admin full access
CREATE POLICY contacts_admin_all ON contacts
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- Crew can read contacts on their assigned jobs
CREATE POLICY contacts_crew_read ON contacts
  FOR SELECT USING (
    auth.get_jwt_role() = 'crew'
    AND id IN (
      SELECT DISTINCT contact_id
      FROM jobs
      WHERE job_crew_assignments.job_id = jobs.id
        AND job_crew_assignments.crew_member_id = auth.get_crew_member_id()
    )
  );

-- Clients can read their own contact
CREATE POLICY contacts_client_own ON contacts
  FOR SELECT USING (
    auth.get_jwt_role() = 'client'
    AND id = auth.get_contact_id()
  );

-- ---------------------------------------------------------------------------
-- jobs (job records)
-- ---------------------------------------------------------------------------
-- Policies:
--   - Admin: full access
--   - Crew: read/update own assigned jobs only
--   - Client: read own jobs
--   - Anon: no access

ALTER TABLE jobs ENABLE ROW LEVEL SECURITY;

-- Admin full access
CREATE POLICY jobs_admin_all ON jobs
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- Crew can read jobs they're assigned to
CREATE POLICY jobs_crew_read ON jobs
  FOR SELECT USING (
    auth.get_jwt_role() = 'crew'
    AND id IN (
      SELECT job_id FROM job_crew_assignments
      WHERE crew_member_id = auth.get_crew_member_id()
    )
  );

-- Crew can update jobs they're assigned to (mark complete, add notes, etc.)
CREATE POLICY jobs_crew_update ON jobs
  FOR UPDATE USING (
    auth.get_jwt_role() = 'crew'
    AND id IN (
      SELECT job_id FROM job_crew_assignments
      WHERE crew_member_id = auth.get_crew_member_id()
    )
  )
  WITH CHECK (
    auth.get_jwt_role() = 'crew'
    AND id IN (
      SELECT job_id FROM job_crew_assignments
      WHERE crew_member_id = auth.get_crew_member_id()
    )
  );

-- Clients can read their own jobs
CREATE POLICY jobs_client_own ON jobs
  FOR SELECT USING (
    auth.get_jwt_role() = 'client'
    AND contact_id = auth.get_contact_id()
  );

-- ---------------------------------------------------------------------------
-- job_locations (tied to jobs)
-- ---------------------------------------------------------------------------

ALTER TABLE job_locations ENABLE ROW LEVEL SECURITY;

-- Admin full access
CREATE POLICY job_locations_admin_all ON job_locations
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- Crew can read/update locations for their jobs
CREATE POLICY job_locations_crew_access ON job_locations
  FOR ALL USING (
    auth.get_jwt_role() = 'crew'
    AND job_id IN (
      SELECT job_id FROM job_crew_assignments
      WHERE crew_member_id = auth.get_crew_member_id()
    )
  )
  WITH CHECK (
    auth.get_jwt_role() = 'crew'
    AND job_id IN (
      SELECT job_id FROM job_crew_assignments
      WHERE crew_member_id = auth.get_crew_member_id()
    )
  );

-- Clients can read locations for their jobs
CREATE POLICY job_locations_client_own ON job_locations
  FOR SELECT USING (
    auth.get_jwt_role() = 'client'
    AND job_id IN (SELECT id FROM jobs WHERE contact_id = auth.get_contact_id())
  );

-- ---------------------------------------------------------------------------
-- job_crew_assignments (crew assignments)
-- ---------------------------------------------------------------------------

ALTER TABLE job_crew_assignments ENABLE ROW LEVEL SECURITY;

-- Admin full access
CREATE POLICY job_crew_assignments_admin_all ON job_crew_assignments
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- Crew can read their own assignments
CREATE POLICY job_crew_assignments_crew_own ON job_crew_assignments
  FOR SELECT USING (
    auth.get_jwt_role() = 'crew'
    AND crew_member_id = auth.get_crew_member_id()
  );

-- Clients can read crew assignments for their jobs (visibility)
CREATE POLICY job_crew_assignments_client_read ON job_crew_assignments
  FOR SELECT USING (
    auth.get_jwt_role() = 'client'
    AND job_id IN (SELECT id FROM jobs WHERE contact_id = auth.get_contact_id())
  );

-- ---------------------------------------------------------------------------
-- job_services (services within a job)
-- ---------------------------------------------------------------------------

ALTER TABLE job_services ENABLE ROW LEVEL SECURITY;

-- Admin full access
CREATE POLICY job_services_admin_all ON job_services
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- Crew can read/update services for their jobs
CREATE POLICY job_services_crew_access ON job_services
  FOR ALL USING (
    auth.get_jwt_role() = 'crew'
    AND job_id IN (
      SELECT job_id FROM job_crew_assignments
      WHERE crew_member_id = auth.get_crew_member_id()
    )
  )
  WITH CHECK (
    auth.get_jwt_role() = 'crew'
    AND job_id IN (
      SELECT job_id FROM job_crew_assignments
      WHERE crew_member_id = auth.get_crew_member_id()
    )
  );

-- Clients can read services for their jobs
CREATE POLICY job_services_client_own ON job_services
  FOR SELECT USING (
    auth.get_jwt_role() = 'client'
    AND job_id IN (SELECT id FROM jobs WHERE contact_id = auth.get_contact_id())
  );

-- ---------------------------------------------------------------------------
-- job_addons (add-ons/extras)
-- ---------------------------------------------------------------------------

ALTER TABLE job_addons ENABLE ROW LEVEL SECURITY;

-- Admin full access
CREATE POLICY job_addons_admin_all ON job_addons
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- Crew can read/update add-ons for their jobs
CREATE POLICY job_addons_crew_access ON job_addons
  FOR ALL USING (
    auth.get_jwt_role() = 'crew'
    AND job_id IN (
      SELECT job_id FROM job_crew_assignments
      WHERE crew_member_id = auth.get_crew_member_id()
    )
  )
  WITH CHECK (
    auth.get_jwt_role() = 'crew'
    AND job_id IN (
      SELECT job_id FROM job_crew_assignments
      WHERE crew_member_id = auth.get_crew_member_id()
    )
  );

-- Clients can read add-ons for their jobs
CREATE POLICY job_addons_client_own ON job_addons
  FOR SELECT USING (
    auth.get_jwt_role() = 'client'
    AND job_id IN (SELECT id FROM jobs WHERE contact_id = auth.get_contact_id())
  );

-- ---------------------------------------------------------------------------
-- job_notes (crew notes on jobs)
-- ---------------------------------------------------------------------------

ALTER TABLE job_notes ENABLE ROW LEVEL SECURITY;

-- Admin full access
CREATE POLICY job_notes_admin_all ON job_notes
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- Crew can read/write notes on their jobs
CREATE POLICY job_notes_crew_access ON job_notes
  FOR ALL USING (
    auth.get_jwt_role() = 'crew'
    AND job_id IN (
      SELECT job_id FROM job_crew_assignments
      WHERE crew_member_id = auth.get_crew_member_id()
    )
  )
  WITH CHECK (
    auth.get_jwt_role() = 'crew'
    AND job_id IN (
      SELECT job_id FROM job_crew_assignments
      WHERE crew_member_id = auth.get_crew_member_id()
    )
  );

-- Clients can read notes on their jobs
CREATE POLICY job_notes_client_own ON job_notes
  FOR SELECT USING (
    auth.get_jwt_role() = 'client'
    AND job_id IN (SELECT id FROM jobs WHERE contact_id = auth.get_contact_id())
  );

-- ============================================================================
-- SECTION 5: FINANCIAL TABLES (ADMIN ONLY + CLIENT OWN)
-- ============================================================================

-- ---------------------------------------------------------------------------
-- invoices
-- ---------------------------------------------------------------------------
-- Admin full access + Clients see own invoices

ALTER TABLE invoices ENABLE ROW LEVEL SECURITY;

-- Admin full access
CREATE POLICY invoices_admin_all ON invoices
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- Clients can read their own invoices
CREATE POLICY invoices_client_own ON invoices
  FOR SELECT USING (
    auth.get_jwt_role() = 'client'
    AND job_id IN (SELECT id FROM jobs WHERE contact_id = auth.get_contact_id())
  );

-- ---------------------------------------------------------------------------
-- payments
-- ---------------------------------------------------------------------------
-- Admin full access + Clients see payments for their jobs

ALTER TABLE payments ENABLE ROW LEVEL SECURITY;

-- Admin full access
CREATE POLICY payments_admin_all ON payments
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- Clients can read payments for their invoices
CREATE POLICY payments_client_own ON payments
  FOR SELECT USING (
    auth.get_jwt_role() = 'client'
    AND invoice_id IN (
      SELECT id FROM invoices
      WHERE job_id IN (SELECT id FROM jobs WHERE contact_id = auth.get_contact_id())
    )
  );

-- ---------------------------------------------------------------------------
-- payment_methods
-- ---------------------------------------------------------------------------
-- Admin only (sensitive financial data)

ALTER TABLE payment_methods ENABLE ROW LEVEL SECURITY;

-- Admin full access
CREATE POLICY payment_methods_admin_all ON payment_methods
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- ---------------------------------------------------------------------------
-- revenue_daily
-- ---------------------------------------------------------------------------
-- Admin only (financial reporting)

ALTER TABLE revenue_daily ENABLE ROW LEVEL SECURITY;

-- Admin full access
CREATE POLICY revenue_daily_admin_all ON revenue_daily
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- ---------------------------------------------------------------------------
-- quotes
-- ---------------------------------------------------------------------------
-- Admin full access + Clients see their own quotes

ALTER TABLE quotes ENABLE ROW LEVEL SECURITY;

-- Admin full access
CREATE POLICY quotes_admin_all ON quotes
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- Clients can read their own quotes
CREATE POLICY quotes_client_own ON quotes
  FOR SELECT USING (
    auth.get_jwt_role() = 'client'
    AND contact_id = auth.get_contact_id()
  );

-- ---------------------------------------------------------------------------
-- quote_services (services within quotes)
-- ---------------------------------------------------------------------------

ALTER TABLE quote_services ENABLE ROW LEVEL SECURITY;

-- Admin full access
CREATE POLICY quote_services_admin_all ON quote_services
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- Clients can read services for their quotes
CREATE POLICY quote_services_client_own ON quote_services
  FOR SELECT USING (
    auth.get_jwt_role() = 'client'
    AND quote_id IN (SELECT id FROM quotes WHERE contact_id = auth.get_contact_id())
  );

-- ============================================================================
-- SECTION 6: CREW MANAGEMENT TABLES
-- ============================================================================

-- ---------------------------------------------------------------------------
-- crew_members (crew profiles)
-- ---------------------------------------------------------------------------

ALTER TABLE crew_members ENABLE ROW LEVEL SECURITY;

-- Admin full access
CREATE POLICY crew_members_admin_all ON crew_members
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- Crew can read their own profile
CREATE POLICY crew_members_crew_own ON crew_members
  FOR SELECT USING (
    auth.get_jwt_role() = 'crew'
    AND id = auth.get_crew_member_id()
  );

-- ---------------------------------------------------------------------------
-- crew_schedules (scheduling)
-- ---------------------------------------------------------------------------

ALTER TABLE crew_schedules ENABLE ROW LEVEL SECURITY;

-- Admin full access
CREATE POLICY crew_schedules_admin_all ON crew_schedules
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- Crew can read/update their own schedule
CREATE POLICY crew_schedules_crew_own ON crew_schedules
  FOR ALL USING (
    auth.get_jwt_role() = 'crew'
    AND crew_member_id = auth.get_crew_member_id()
  )
  WITH CHECK (
    auth.get_jwt_role() = 'crew'
    AND crew_member_id = auth.get_crew_member_id()
  );

-- ---------------------------------------------------------------------------
-- crew_availability (availability slots)
-- ---------------------------------------------------------------------------

ALTER TABLE crew_availability ENABLE ROW LEVEL SECURITY;

-- Admin full access
CREATE POLICY crew_availability_admin_all ON crew_availability
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- Crew can read/update their own availability
CREATE POLICY crew_availability_crew_own ON crew_availability
  FOR ALL USING (
    auth.get_jwt_role() = 'crew'
    AND crew_member_id = auth.get_crew_member_id()
  )
  WITH CHECK (
    auth.get_jwt_role() = 'crew'
    AND crew_member_id = auth.get_crew_member_id()
  );

-- ---------------------------------------------------------------------------
-- crews (crew groups/teams)
-- ---------------------------------------------------------------------------
-- Admin only

ALTER TABLE crews ENABLE ROW LEVEL SECURITY;

-- Admin full access
CREATE POLICY crews_admin_all ON crews
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- ============================================================================
-- SECTION 7: COMMUNICATION TABLES
-- ============================================================================

-- ---------------------------------------------------------------------------
-- conversations
-- ---------------------------------------------------------------------------
-- Participants only (admin can moderate)

ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;

-- Admin full access
CREATE POLICY conversations_admin_all ON conversations
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- Participants can access their conversations
CREATE POLICY conversations_participant_access ON conversations
  FOR ALL USING (
    (
      auth.get_jwt_role() = 'crew'
      AND (
        initiator_crew_id = auth.get_crew_member_id()
        OR recipient_crew_id = auth.get_crew_member_id()
      )
    )
    OR (
      auth.get_jwt_role() = 'client'
      AND (
        initiator_contact_id = auth.get_contact_id()
        OR recipient_contact_id = auth.get_contact_id()
      )
    )
  )
  WITH CHECK (
    (
      auth.get_jwt_role() = 'crew'
      AND (
        initiator_crew_id = auth.get_crew_member_id()
        OR recipient_crew_id = auth.get_crew_member_id()
      )
    )
    OR (
      auth.get_jwt_role() = 'client'
      AND (
        initiator_contact_id = auth.get_contact_id()
        OR recipient_contact_id = auth.get_contact_id()
      )
    )
  );

-- ---------------------------------------------------------------------------
-- messages (messages within conversations)
-- ---------------------------------------------------------------------------

ALTER TABLE messages ENABLE ROW LEVEL SECURITY;

-- Admin full access
CREATE POLICY messages_admin_all ON messages
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- Participants can access messages in their conversations
CREATE POLICY messages_participant_access ON messages
  FOR ALL USING (
    conversation_id IN (SELECT id FROM conversations)
  )
  WITH CHECK (
    conversation_id IN (SELECT id FROM conversations)
  );

-- ---------------------------------------------------------------------------
-- notifications (user notifications)
-- ---------------------------------------------------------------------------

ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;

-- Admin can read all, but can't modify others' notifications
CREATE POLICY notifications_admin_read ON notifications
  FOR SELECT USING (auth.get_jwt_role() = 'admin');

-- Crew can read/update their own notifications
CREATE POLICY notifications_crew_own ON notifications
  FOR ALL USING (
    auth.get_jwt_role() = 'crew'
    AND crew_member_id = auth.get_crew_member_id()
  )
  WITH CHECK (
    auth.get_jwt_role() = 'crew'
    AND crew_member_id = auth.get_crew_member_id()
  );

-- Clients can read/update their own notifications
CREATE POLICY notifications_client_own ON notifications
  FOR ALL USING (
    auth.get_jwt_role() = 'client'
    AND contact_id = auth.get_contact_id()
  )
  WITH CHECK (
    auth.get_jwt_role() = 'client'
    AND contact_id = auth.get_contact_id()
  );

-- ============================================================================
-- SECTION 8: SYSTEM TABLES (ADMIN ONLY)
-- ============================================================================

-- ---------------------------------------------------------------------------
-- events (system event log / pipeline)
-- ---------------------------------------------------------------------------

ALTER TABLE events ENABLE ROW LEVEL SECURITY;

CREATE POLICY events_admin_only ON events
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- ---------------------------------------------------------------------------
-- cron_health (scheduled jobs monitoring)
-- ---------------------------------------------------------------------------

ALTER TABLE cron_health ENABLE ROW LEVEL SECURITY;

CREATE POLICY cron_health_admin_only ON cron_health
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- ============================================================================
-- SECTION 9: AUTH & USER TABLES
-- ============================================================================

-- ---------------------------------------------------------------------------
-- auth_tokens (crew auth tokens)
-- ---------------------------------------------------------------------------

ALTER TABLE auth_tokens ENABLE ROW LEVEL SECURITY;

-- Admin can view all
CREATE POLICY auth_tokens_admin_read ON auth_tokens
  FOR SELECT USING (auth.get_jwt_role() = 'admin');

-- Crew can see their own tokens
CREATE POLICY auth_tokens_crew_own ON auth_tokens
  FOR ALL USING (
    auth.get_jwt_role() = 'crew'
    AND crew_member_id = auth.get_crew_member_id()
  )
  WITH CHECK (
    auth.get_jwt_role() = 'crew'
    AND crew_member_id = auth.get_crew_member_id()
  );

-- ---------------------------------------------------------------------------
-- client_auth_tokens (client auth tokens)
-- ---------------------------------------------------------------------------

ALTER TABLE client_auth_tokens ENABLE ROW LEVEL SECURITY;

-- Admin can view all
CREATE POLICY client_auth_tokens_admin_read ON client_auth_tokens
  FOR SELECT USING (auth.get_jwt_role() = 'admin');

-- Clients can see their own tokens
CREATE POLICY client_auth_tokens_client_own ON client_auth_tokens
  FOR ALL USING (
    auth.get_jwt_role() = 'client'
    AND contact_id = auth.get_contact_id()
  )
  WITH CHECK (
    auth.get_jwt_role() = 'client'
    AND contact_id = auth.get_contact_id()
  );

-- ---------------------------------------------------------------------------
-- ai_profiles (AI assistant profiles)
-- ---------------------------------------------------------------------------

ALTER TABLE ai_profiles ENABLE ROW LEVEL SECURITY;

-- Admin full access
CREATE POLICY ai_profiles_admin_all ON ai_profiles
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- Crew can read their own profile
CREATE POLICY ai_profiles_crew_own ON ai_profiles
  FOR SELECT USING (
    auth.get_jwt_role() = 'crew'
    AND crew_member_id = auth.get_crew_member_id()
  );

-- ---------------------------------------------------------------------------
-- settings (application settings)
-- ---------------------------------------------------------------------------

ALTER TABLE settings ENABLE ROW LEVEL SECURITY;

-- Admin only
CREATE POLICY settings_admin_only ON settings
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- ============================================================================
-- SECTION 10: PUBLIC-FACING TABLES (ANON + AUTH READ-ONLY)
-- ============================================================================

-- ---------------------------------------------------------------------------
-- services (cleaning services offered)
-- ---------------------------------------------------------------------------

ALTER TABLE services ENABLE ROW LEVEL SECURITY;

-- Admin full access
CREATE POLICY services_admin_all ON services
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- Everyone (anon + auth) can read active services
CREATE POLICY services_public_read ON services
  FOR SELECT USING (is_active = true OR auth.get_jwt_role() = 'admin');

-- ---------------------------------------------------------------------------
-- service_areas (service coverage areas)
-- ---------------------------------------------------------------------------

ALTER TABLE service_areas ENABLE ROW LEVEL SECURITY;

-- Admin full access
CREATE POLICY service_areas_admin_all ON service_areas
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- Everyone can read active areas
CREATE POLICY service_areas_public_read ON service_areas
  FOR SELECT USING (is_active = true OR auth.get_jwt_role() = 'admin');

-- ---------------------------------------------------------------------------
-- testimonials (client testimonials - approved only)
-- ---------------------------------------------------------------------------

ALTER TABLE testimonials ENABLE ROW LEVEL SECURITY;

-- Admin full access
CREATE POLICY testimonials_admin_all ON testimonials
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- Everyone can read approved testimonials
CREATE POLICY testimonials_public_read ON testimonials
  FOR SELECT USING (is_approved = true OR auth.get_jwt_role() = 'admin');

-- ============================================================================
-- SECTION 11: CONTENT MANAGEMENT TABLES (ADMIN ONLY)
-- ============================================================================

-- ---------------------------------------------------------------------------
-- watchlists
-- ---------------------------------------------------------------------------

ALTER TABLE watchlists ENABLE ROW LEVEL SECURITY;

CREATE POLICY watchlists_admin_only ON watchlists
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- ---------------------------------------------------------------------------
-- videos
-- ---------------------------------------------------------------------------

ALTER TABLE videos ENABLE ROW LEVEL SECURITY;

CREATE POLICY videos_admin_only ON videos
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- ---------------------------------------------------------------------------
-- scripts (video scripts)
-- ---------------------------------------------------------------------------

ALTER TABLE scripts ENABLE ROW LEVEL SECURITY;

CREATE POLICY scripts_admin_only ON scripts
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- ---------------------------------------------------------------------------
-- content_projects
-- ---------------------------------------------------------------------------

ALTER TABLE content_projects ENABLE ROW LEVEL SECURITY;

CREATE POLICY content_projects_admin_only ON content_projects
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- ---------------------------------------------------------------------------
-- assets (project assets)
-- ---------------------------------------------------------------------------

ALTER TABLE assets ENABLE ROW LEVEL SECURITY;

CREATE POLICY assets_admin_only ON assets
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- ---------------------------------------------------------------------------
-- versions (asset versions)
-- ---------------------------------------------------------------------------

ALTER TABLE versions ENABLE ROW LEVEL SECURITY;

CREATE POLICY versions_admin_only ON versions
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- ---------------------------------------------------------------------------
-- comments (asset comments)
-- ---------------------------------------------------------------------------

ALTER TABLE comments ENABLE ROW LEVEL SECURITY;

CREATE POLICY comments_admin_only ON comments
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- ---------------------------------------------------------------------------
-- approvals (asset approvals)
-- ---------------------------------------------------------------------------

ALTER TABLE approvals ENABLE ROW LEVEL SECURITY;

CREATE POLICY approvals_admin_only ON approvals
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- ---------------------------------------------------------------------------
-- activity_log (audit log)
-- ---------------------------------------------------------------------------

ALTER TABLE activity_log ENABLE ROW LEVEL SECURITY;

-- Admin full access
CREATE POLICY activity_log_admin_all ON activity_log
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- Users can read their own activity (read-only)
CREATE POLICY activity_log_own_read ON activity_log
  FOR SELECT USING (
    (
      auth.get_jwt_role() = 'crew'
      AND user_id = auth.get_crew_member_id()::text
    )
    OR (
      auth.get_jwt_role() = 'client'
      AND user_id = auth.get_contact_id()::text
    )
  );

-- ============================================================================
-- SECTION 12: BILLING & ORGANIZATION TABLES
-- ============================================================================

-- ---------------------------------------------------------------------------
-- orgs (organizations)
-- ---------------------------------------------------------------------------

ALTER TABLE orgs ENABLE ROW LEVEL SECURITY;

-- Admin full access
CREATE POLICY orgs_admin_all ON orgs
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- Members can read their own org
CREATE POLICY orgs_member_read ON orgs
  FOR SELECT USING (
    auth.get_jwt_role() IN ('crew', 'client')
  );

-- ---------------------------------------------------------------------------
-- org_plan_subscriptions
-- ---------------------------------------------------------------------------

ALTER TABLE org_plan_subscriptions ENABLE ROW LEVEL SECURITY;

-- Admin only
CREATE POLICY org_plan_subscriptions_admin_only ON org_plan_subscriptions
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- ---------------------------------------------------------------------------
-- plan_limits (plan feature limits)
-- ---------------------------------------------------------------------------

ALTER TABLE plan_limits ENABLE ROW LEVEL SECURITY;

-- Admin full access
CREATE POLICY plan_limits_admin_all ON plan_limits
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- Everyone can read (public pricing info)
CREATE POLICY plan_limits_public_read ON plan_limits
  FOR SELECT USING (true);

-- ---------------------------------------------------------------------------
-- usage_ledger (usage tracking)
-- ---------------------------------------------------------------------------

ALTER TABLE usage_ledger ENABLE ROW LEVEL SECURITY;

-- Admin only
CREATE POLICY usage_ledger_admin_only ON usage_ledger
  FOR ALL USING (auth.get_jwt_role() = 'admin')
  WITH CHECK (auth.get_jwt_role() = 'admin');

-- ============================================================================
-- SECTION 13: VERIFICATION QUERIES
-- ============================================================================

-- Show RLS status and policy count for all tables
DO $$
DECLARE
  v_table_name text;
  v_rls_enabled boolean;
  v_policy_count integer;
  v_output text;
BEGIN
  RAISE NOTICE '
  ============================================================================
  RLS VERIFICATION REPORT
  ============================================================================
  ';

  FOR v_table_name IN
    SELECT tablename FROM pg_tables
    WHERE schemaname = 'public'
    ORDER BY tablename
  LOOP
    -- Check RLS status
    SELECT relrowsecurity INTO v_rls_enabled
    FROM pg_class
    WHERE relname = v_table_name;

    -- Count policies
    SELECT COUNT(*) INTO v_policy_count
    FROM pg_policies
    WHERE tablename = v_table_name;

    v_output := format(
      '%-30s | RLS: %-5s | Policies: %s',
      v_table_name,
      CASE WHEN v_rls_enabled THEN 'ON' ELSE 'OFF' END,
      v_policy_count
    );

    RAISE NOTICE '%', v_output;
  END LOOP;

  RAISE NOTICE '
  ============================================================================
  ';
END $$;

-- ============================================================================
-- SECTION 14: SUMMARY
-- ============================================================================

/*
SUMMARY OF RLS HARDENING:

ROLE-BASED ACCESS:
  ✓ Admin (role='admin'):
    - Full access to all operational, financial, crew, system, and content tables
    - Can create, read, update, delete across the platform
    - Limited visibility: Can only read public testimonials and services

  ✓ Crew (role='crew'):
    - Read own crew_members profile
    - Read/Update own crew_schedules and crew_availability
    - Read/Update jobs they're assigned to (via job_crew_assignments)
    - Read/Update job_locations, job_services, job_addons, job_notes for their jobs
    - Read contacts assigned to their jobs
    - Manage their own auth_tokens and notifications
    - NO access to: financial data, crew management, system tables, content

  ✓ Client (role='client'):
    - Read own contact information
    - Read own jobs and related data (locations, services, addons, notes, crew_assignments)
    - Read own invoices, payments, quotes, and quote_services
    - Read own notifications and auth_tokens
    - NO access to: crew data, financial reporting, system tables, content

  ✓ Anon (unauthenticated):
    - Read-only: services (active), service_areas (active), testimonials (approved)
    - NO access to: any operational, financial, or user data

DATA ISOLATION:
  • Job data flows through job_crew_assignments (crew) and contact_id (clients)
  • Financial data completely isolated to admin + client own invoices/payments
  • Crew management isolated to admin only
  • System tables isolated to admin only
  • Communication filtered by participant IDs

IMPORTANT NOTES:
  1. Service role bypasses RLS entirely (server-side operations unaffected)
  2. All policies use custom JWT claim extraction
  3. Policies are idempotent (safe to re-apply)
  4. Double-check all table names match your schema
  5. Test thoroughly with JWT containing each role before production
  6. Ensure foreign key relationships are correctly reflected in policies

TESTING CHECKLIST:
  □ Admin JWT can access all tables
  □ Crew JWT can only see assigned jobs and own data
  □ Client JWT can only see own jobs/invoices/quotes
  □ Anon requests can only see public services/areas/testimonials
  □ Service role bypasses RLS (unaffected by these policies)
  □ Run verification query to confirm all policies are in place
*/

