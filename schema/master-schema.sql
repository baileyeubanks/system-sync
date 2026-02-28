-- =============================================================================
-- MASTER SCHEMA REGISTRY — All Supabase tables across the ACS + CC ecosystem
-- Generated: 2026-02-27
-- Source: audit of acs-website, contentco-op-website, codeliver, coscript, coedit
-- =============================================================================
-- Legend:
--   Owner = repo that CREATE TABLE or primarily writes
--   Readers = repos that SELECT from it
--   * = table defined in migration SQL
--   ~ = table referenced in code but no migration found (pre-existing or manual)
-- =============================================================================

-- ─── ACS-WEBSITE OWNED TABLES ────────────────────────────────────────────────

-- * admin_auth (acs-website owns, acs-website reads)
-- Columns: id UUID PK, email TEXT UNIQUE, password_hash TEXT, role TEXT, created_at TIMESTAMPTZ
-- Indexes: idx_admin_auth_email (email)

-- ~ businesses (acs-website reads)
-- Referenced by: crew_members.business_id, adminSettings.js
-- Pre-existing table

-- * calendar_sync (acs-website owns)
-- Columns: id UUID PK, google_event_id TEXT, job_id UUID FK, owner TEXT, synced_at TIMESTAMPTZ
-- Indexes: idx_calendar_sync_google_event_id

-- ~ client_profiles (acs-website reads)
-- Referenced by: getPortalData.js, adminClients.js

-- ~ client_requests (acs-website reads)
-- Referenced by: adminRequests.js, createRequest.js

-- ~ contacts (acs-website owns, acs-website reads)
-- Core CRM table. Referenced by: 20+ functions
-- Key columns: id UUID, name, email, phone, telegram_chat_id, whatsapp_phone,
--   preferred_channel, priority_score, tags JSONB, source, last_contacted
-- Indexes: phone, email, telegram_chat_id, whatsapp_phone

-- * conversations (acs-website owns)
-- Columns: id UUID PK, contact_id UUID FK, channel TEXT CHECK(telegram|whatsapp|imessage|email),
--   messages_json JSONB, intent_history JSONB, started_at, last_message_at, resolved BOOLEAN
-- Indexes: contact_id, channel, resolved (partial), last_message_at DESC

-- * crew_auth (acs-website owns)
-- Columns: id UUID PK, crew_member_id UUID FK, pin_hash TEXT, created_at
-- Crew PIN-based authentication

-- * crew_members (acs-website owns)
-- Columns: id UUID PK, name, phone, email, status, business_id UUID FK,
--   telegram_chat_id TEXT, created_at
-- Indexes: status, telegram_chat_id

-- ~ events (acs-website owns, contentco-op-website writes)
-- Async event queue for Mac Mini bridge (iMessage, email triggers)
-- Columns: id, type TEXT, contact_id UUID, payload JSONB, processed BOOLEAN, created_at
-- Shared write: contentco-op-website inserts brief_message events

-- * faq_responses (acs-website owns)
-- Columns: id SERIAL PK, question_pattern TEXT, answer TEXT, category TEXT, created_at
-- 20 seeded FAQ entries for conversational AI bot

-- * feedback (acs-website owns)
-- Columns: id UUID PK, job_id UUID FK UNIQUE, contact_id UUID FK, rating INT CHECK(1-5),
--   comment TEXT, created_at
-- Indexes: job_id (unique), contact_id

-- ~ interactions (acs-website reads)
-- Referenced by: adminClients.js

-- * invoice_payments (acs-website owns)
-- Columns: id UUID PK, invoice_id UUID FK, amount NUMERIC, method TEXT, paid_at, created_at

-- * invoices (acs-website owns)
-- Columns: id UUID PK, job_id UUID FK, contact_id UUID FK, amount, status, due_date, paid_at, created_at
-- Indexes: contact_id, status, due_date

-- * job_applicants (acs-website owns)
-- Columns: id UUID PK, applicant_id UUID FK, job_id UUID FK, status, applied_at

-- * job_crew_assignments (acs-website owns)
-- Columns: id UUID PK, job_id UUID FK, crew_member_id UUID FK, role TEXT, assigned_at

-- * job_eta_overrides (acs-website owns)
-- Columns: id UUID PK, job_id UUID FK, eta_minutes INT, updated_by, created_at

-- * job_locations (acs-website owns)
-- Columns: id UUID PK, job_id UUID FK, lat DOUBLE, lng DOUBLE, address TEXT, geocoded_at

-- * job_tracking_tokens (acs-website owns)
-- Columns: id UUID PK, job_id UUID FK, token TEXT UNIQUE, expires_at, created_at
-- Client-facing tracking link tokens

-- ~ jobs (acs-website owns)
-- Core scheduling table. Referenced by: 15+ functions
-- Key columns: id UUID, contact_id FK, scheduled_start, scheduled_end, status, notes, address
-- Statuses: scheduled, confirmed, in_progress, completed, cancelled

-- * lead_nurture_log (acs-website owns)
-- Columns: id UUID PK, contact_id UUID FK, action TEXT, channel TEXT, sent_at, created_at

-- * notification_log (acs-website owns)
-- Columns: id UUID PK, contact_id UUID, channel TEXT, message_type TEXT, message_preview TEXT,
--   status TEXT, error TEXT, created_at
-- Indexes: contact_id, channel, created_at DESC

-- ~ payments (acs-website reads)
-- Referenced by: adminFinance.js, stripeWebhook.js

-- ~ quotes (acs-website owns)
-- Referenced by: submitQuote.js, adminQuotes.js, _conversationAgent.js
-- Key columns: id UUID, contact_id FK, service_type, estimated_price, status, created_at

-- * referral_codes (acs-website owns)
-- Columns: id UUID PK, code TEXT UNIQUE, contact_id UUID FK, discount_pct, max_uses, created_at

-- * referral_uses (acs-website owns)
-- Columns: id UUID PK, referral_code_id UUID FK, referred_contact_id UUID FK, job_id UUID FK, created_at

-- * revenue_snapshots (acs-website owns)
-- Columns: id UUID PK, period TEXT, revenue NUMERIC, expenses NUMERIC, jobs_count INT, snapshot_date, created_at

-- * review_requests (acs-website owns)
-- Columns: id UUID PK, job_id UUID FK, contact_id UUID FK, sent_at, completed_at, channel TEXT

-- * reviews (acs-website owns)
-- Columns: id UUID PK, contact_id UUID FK, job_id UUID FK, rating INT, text TEXT, source TEXT, created_at

-- * route_history (acs-website owns)
-- Columns: id UUID PK, crew_member_id UUID FK, job_id UUID FK, lat, lng, recorded_at
-- GPS breadcrumb trail for crew tracking

-- ~ tasks (acs-website owns)
-- Columns: id UUID, title, description, assigned_to, status, priority, due_date, created_at

-- ~ admin_daily_snapshot (acs-website reads — likely a VIEW)
-- Referenced by: adminDashboard.js

-- ~ tasks_dashboard_today (acs-website reads — likely a VIEW)
-- Referenced by: defined in 20260224_create_tasks_dashboard_view.sql

-- * tax_deduction_categories (acs-website owns)
-- * tax_deductions (acs-website owns)
-- * tax_estimates (acs-website owns)
-- * tax_profiles (acs-website owns)
-- * tax_rules (acs-website owns)
-- Tax module tables for business expense tracking

-- ~ applicants (acs-website reads)
-- Referenced by: adminApplicants.js, submitApplication.js

-- ─── CONTENTCO-OP-WEBSITE OWNED TABLES ───────────────────────────────────────

-- ~ creative_briefs (contentco-op-website owns)
-- Columns: id UUID, title, description, status, client_id, created_by, created_at, updated_at
-- Referenced by: briefs API routes, portal page

-- ~ brief_files (contentco-op-website owns)
-- Columns: id UUID, brief_id UUID FK, file_url TEXT, filename TEXT, uploaded_at
-- Storage bucket: brief-files

-- ~ brief_messages (contentco-op-website owns)
-- Columns: id UUID, brief_id UUID FK, sender_id, message TEXT, created_at

-- ~ brief_status_history (contentco-op-website owns)
-- Columns: id UUID, brief_id UUID FK, old_status, new_status, changed_by, changed_at

-- ─── CODELIVER OWNED TABLES ──────────────────────────────────────────────────

-- ~ review_assets (codeliver owns)
-- Columns: id UUID, title, description, file_url, project_id, status, created_at
-- Video/creative asset review platform

-- ~ asset_versions (codeliver owns)
-- Columns: id UUID, asset_id UUID FK, version_number INT, file_url, notes, created_at

-- ~ approval_gates (codeliver owns)
-- Columns: id UUID, asset_id UUID FK, gate_name TEXT, status, required_approvers INT

-- ~ approval_decisions (codeliver owns)
-- Columns: id UUID, gate_id UUID FK, user_id, decision TEXT, comment, decided_at

-- ~ review_events (codeliver owns)
-- Audit log for all review actions
-- Columns: id UUID, asset_id UUID FK, event_type TEXT, user_id, metadata JSONB, created_at

-- ~ timecoded_comments (codeliver owns)
-- Columns: id UUID, asset_id UUID FK, user_id, timecode FLOAT, comment TEXT, created_at

-- ─── COSCRIPT OWNED TABLES ───────────────────────────────────────────────────

-- ~ briefs (coscript owns — different from creative_briefs)
-- Referenced by: coscript briefs API

-- ~ drafts (coscript owns)
-- Columns: id UUID, user_id, title, content JSONB, created_at, updated_at

-- ~ outlier_scores (coscript owns)
-- Video performance outlier detection

-- ~ script_fixes (coscript owns)
-- Columns: id UUID, script_id UUID FK, fix_type, content, created_at

-- ~ script_jobs (coscript owns, acs-website reads)
-- Columns: id UUID, status, prompt, model, created_at
-- Also referenced by acs-website coedit functions

-- ~ script_variants (coscript owns, acs-website reads)
-- Columns: id UUID, script_job_id UUID FK, content, hook, structure, created_at

-- ~ share_links (coscript owns)
-- Columns: id UUID, draft_id UUID FK, token TEXT UNIQUE, created_at

-- ~ vault_items (coscript owns, acs-website reads)
-- Columns: id UUID, user_id, title, content, tags, created_at

-- ~ watchlists (coscript owns, acs-website reads)
-- Columns: id UUID, user_id, name, youtube_channel_ids JSONB, created_at

-- ─── COEDIT OWNED TABLES ────────────────────────────────────────────────────

-- ~ projects (coedit owns, acs-website reads)
-- Columns: id UUID, name, description, user_id, settings JSONB, created_at, updated_at
-- Also: co_edit_documents, co_edit_document_versions (referenced by acs-website)

-- ─── SHARED / CROSS-REPO TABLES ─────────────────────────────────────────────

-- ~ user_profiles (codeliver + coscript read)
-- Auth-linked user profiles. Used for org membership.

-- ~ orgs (acs-website reads)
-- Multi-tenant organization table

-- ~ org_members (acs-website reads)
-- Columns: id, org_id FK, user_id FK, role

-- ~ org_invites (acs-website reads)

-- ~ org_plan_subscriptions (acs-website reads)
-- Billing/plan tiers for orgs

-- ~ plan_limits (acs-website reads)
-- Feature limits per plan tier

-- ~ usage_ledger (acs-website reads)
-- Usage tracking for metered billing

-- ~ coedit_usage_current_period (acs-website reads)
-- Current billing period usage for CoEdit

-- ~ coedit_dead_letters (acs-website reads)
-- Failed/unprocessable CoEdit events

-- ~ contact_business_map (acs-website reads)
-- Maps contacts to business entities

-- ~ project_notes (acs-website reads)
-- ~ project_videos (acs-website reads)
-- ~ video_features (acs-website reads)
-- ~ video_metrics_daily (acs-website reads)
-- ~ video_outlier_scores (acs-website reads)
-- ~ videos (acs-website reads)
-- ~ watchlist_sources (acs-website reads)
-- ~ watchlist_sync_errors (acs-website reads)
-- ~ watchlist_sync_runs (acs-website reads)
-- YouTube/video analytics tables used by CoScript features in acs-website

-- ~ script_feedback (acs-website reads)
-- User feedback on generated scripts
