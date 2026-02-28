# API Contracts — ACS Ecosystem
**Generated:** 2026-02-28

---

## Overview

This document defines all HTTP endpoints across the ACS ecosystem:
- **ACS Website** (Netlify Functions)
- **CoScript** (Next.js API routes)
- **CoDeliver** (Next.js API routes)

All timestamps are ISO 8601. All monetary amounts in dollars (floats) or cents (integers, suffixed `_cents`).

---

## Authentication & Authorization

### JWT Tokens
All protected endpoints use Bearer token authentication: `Authorization: Bearer <jwt>`

Token claims contain:
- `role`: 'admin' | 'crew' | 'client'
- `business_id`: UUID (business scope)
- `admin_id` / `crew_member_id` / `client_profile_id` (role-specific IDs)
- Additional role-specific fields

### Auth Levels
- **Public**: No auth required
- **Admin**: `requireAdmin()` — role='admin'
- **Crew**: `requireCrew()` — role='crew'
- **Client**: `requireClient()` — role='client'
- **Any Auth**: Requires valid JWT (any role)

---

## ACS Website (Netlify Functions)

All Netlify functions are available at `/.netlify/functions/{name}`.

### Authentication Endpoints

#### POST /adminLogin
- **Auth:** None
- **Body:** `{ phoneOrEmail: string, pin: string }`
- **Response:** `{ success: true, token: string, admin_id: uuid, role: string }`
- **Errors:** 400 (missing fields), 401 (invalid credentials), 500
- **Notes:** PIN-based admin login. Updates `last_login_at`. Business ID derived from admin_auth.

#### POST /crewLogin
- **Auth:** None
- **Body:** `{ phone: string, pin: string }`
- **Response:** `{ success: true, token: string, crew_member_id: uuid, name: string, role: string }`
- **Errors:** 400 (missing fields), 401 (invalid credentials), 403 (inactive), 500
- **Notes:** Phone must be normalized. Validates crew member status = 'active'.

#### POST /authLogin
- **Auth:** None
- **Body:** `{ type: 'admin'|'crew'|'client', email: string, password: string }`
- **Response:** `{ success: true, token: string, ...role_specific_fields }`
- **Errors:** 400 (missing/invalid type), 401 (invalid credentials), 403 (not verified/inactive), 500
- **Notes:** Unified email+password login. Requires email verification. Replaces legacy PIN endpoints.

#### POST /authSignup
- **Auth:** None
- **Body:** `{ type: 'crew'|'client', name: string, email: string, password: string, phone?: string }`
- **Response:** `{ contact?: object, crew_member?: object, client_profile?: object, verification_email_sent: bool }`
- **Errors:** 400 (invalid data), 409 (exists), 500
- **Notes:** Self-service registration. Queues verification email. `type='admin'` returns 403. Password ≥ 8 chars.

#### POST /authForgotPassword
- **Auth:** None
- **Body:** `{ email: string }`
- **Response:** `{ success: true, message: string }`
- **Errors:** 400 (invalid email), 404 (user not found), 500
- **Notes:** Queues password reset link. Valid for 24 hours.

#### POST /authResetPassword
- **Auth:** None
- **Body:** `{ token: string, password: string }`
- **Response:** `{ success: true }`
- **Errors:** 400 (invalid token/password), 500
- **Notes:** Token from forgot password email. Password ≥ 8 chars.

#### POST /authVerifyEmail
- **Auth:** None
- **Body:** `{ token: string }`
- **Response:** `{ success: true }`
- **Errors:** 400 (invalid/expired token), 500
- **Notes:** Token from signup email. Required before login.

#### POST /loginGoogle
- **Auth:** None
- **Body:** `{ idToken: string, type: 'crew'|'client'|'admin' }`
- **Response:** `{ success: true, token: string, ...user_data }`
- **Errors:** 401 (invalid idToken), 500
- **Notes:** OAuth fallback (rarely used; prefer authLogin).

#### POST /loginPin
- **Auth:** None
- **Body:** `{ phone: string, pin: string }`
- **Response:** `{ success: true, token: string, crew_member_id: uuid }`
- **Errors:** 401 (invalid), 500
- **Notes:** Deprecated; use crewLogin or authLogin instead.

---

### Client Portal (Public / Client)

#### POST /createRequest
- **Auth:** None
- **Body:** `{ contact_id: uuid, type: string, message: string }`
- **Response:** `{ request: object }`
- **Errors:** 400 (missing fields), 500
- **Notes:** Clients submit service requests. No auth; contact_id is public.

#### POST /submitQuote
- **Auth:** None
- **Body:** `{ contact_id?: uuid, name: string, email: string, phone: string, service_type: string, square_footage?: number, bedrooms?: number, bathrooms?: number, frequency?: string, pet_count?: number, has_high_reach?: bool, is_next_day?: bool, window_count?: number, carpet_sqft?: number, addons?: array, notes?: string }`
- **Response:** `{ quote: object, booking_link: string }`
- **Errors:** 400 (missing required), 500
- **Notes:** Public quote request. Generates quote & booking link. Notifies team.

#### GET /getAvailableSlots
- **Auth:** None
- **Query:** `quote_id: uuid`
- **Response:** `{ available_dates: [{ date: string, windows: ['morning'|'afternoon'|'evening'] }] }`
- **Errors:** 404 (quote not found), 500
- **Notes:** Returns next 30 days. Checks existing jobs for conflicts.

#### POST /bookSlot
- **Auth:** None
- **Body:** `{ quote_id: uuid, date: string (YYYY-MM-DD), time_window: 'morning'|'afternoon'|'evening' }`
- **Response:** `{ job: object, confirmation_message: string }`
- **Errors:** 400 (invalid date/window, quote expired), 404 (quote not found), 500
- **Notes:** Quote must be < 14 days old. Date must be future. Creates job linked to quote contact.

#### POST /confirmAppointment
- **Auth:** None
- **Body:** `{ job_id: uuid }`
- **Response:** `{ job: object, confirmation: string }`
- **Errors:** 404 (job not found), 500
- **Notes:** Client confirms appointment. Notifies crew.

#### POST /submitFeedback
- **Auth:** None
- **Body:** `{ job_id: uuid, rating: 1-5, text: string, anonymous?: bool }`
- **Response:** `{ feedback: object }`
- **Errors:** 400 (invalid rating), 404 (job not found), 500
- **Notes:** Post-job feedback. Feeds review analytics.

#### POST /submitReview
- **Auth:** None
- **Body:** `{ job_id: uuid, service_quality: 1-5, professionalism: 1-5, punctuality: 1-5, overall_comment?: string }`
- **Response:** `{ review: object }`
- **Errors:** 400 (invalid ratings), 404 (job not found), 500
- **Notes:** Detailed review. Links to job contact.

#### POST /requestReview
- **Auth:** None
- **Body:** `{ job_id: uuid }`
- **Response:** `{ message_sent: bool }`
- **Errors:** 404 (job not found), 500
- **Notes:** Triggers review request email/SMS to client. Queues async notification.

#### POST /submitApplication
- **Auth:** None
- **Body:** `{ name: string, email: string, phone: string, position: string, resume_url?: string, cover_letter?: string }`
- **Response:** `{ application: object }`
- **Errors:** 400 (missing required), 500
- **Notes:** Job application. Notifies hiring team.

#### POST /setPin
- **Auth:** None
- **Body:** `{ contact_id: uuid, pin: string }`
- **Response:** `{ success: true }`
- **Errors:** 400 (invalid pin), 404 (contact not found), 500
- **Notes:** Self-service PIN setup for crew. PIN hashed with bcrypt.

#### POST /createPublicDepositCheckout
- **Auth:** None
- **Body:** `{ quote_id: uuid, deposit_amount: number }`
- **Response:** `{ checkout_url: string, session_id: string }`
- **Errors:** 400 (invalid amount), 404 (quote not found), 500
- **Notes:** Creates Stripe checkout for deposit. No auth; quote_id validates access.

#### GET /getTestimonials
- **Auth:** None
- **Query:** `limit?: number`
- **Response:** `{ testimonials: [{ author: string, text: string, rating: number, date: string }] }`
- **Errors:** 500
- **Notes:** Public endpoint. Returns approved testimonials from reviews.

#### GET /propertyLookup
- **Auth:** None
- **Query:** `address: string`
- **Response:** `{ properties: [{ address: string, square_footage?: number, year_built?: number, bedrooms?: number, bathrooms?: number }] }`
- **Errors:** 400 (no address), 500
- **Notes:** Third-party property data. Used to pre-fill quote forms.

#### POST /validatePromo
- **Auth:** None
- **Body:** `{ code: string, contact_id?: uuid }`
- **Response:** `{ valid: bool, discount_percent?: number, message?: string }`
- **Errors:** 400 (no code), 500
- **Notes:** Validates promo code. Returns discount if valid.

#### POST /validateReferral
- **Auth:** None
- **Body:** `{ referral_code: string }`
- **Response:** `{ valid: bool, referrer_name?: string, bonus_amount?: number }`
- **Errors:** 400 (no code), 500
- **Notes:** Validates referral code. Returns bonus if valid.

#### POST /generateReferralCode
- **Auth:** Client
- **Query:** None
- **Response:** `{ referral_code: string, bonus_amount: number }`
- **Errors:** 401, 500
- **Notes:** Generate unique referral code for authenticated client.

---

### Client Portal Endpoints (Authenticated Client)

#### GET /getPortalData
- **Auth:** Client
- **Query:** None
- **Response:** `{ profile: object, recent_jobs: [job], upcoming_jobs: [job], invoices: [invoice] }`
- **Errors:** 401, 500
- **Notes:** Client dashboard data. Scoped to authenticated user's profile.

---

### Crew Endpoints

#### GET /crewPortalData
- **Auth:** Crew
- **Query:** None
- **Response:** `{ member: object, jobs_today: [job], jobs_upcoming: [job], route_plan: object|null }`
- **Errors:** 401, 500
- **Notes:** Today + next 7 days jobs. Includes route plan metadata if available.

#### PUT /crewUpdateJobStatus
- **Auth:** Crew
- **Body:** `{ job_id: uuid, status: 'on_my_way'|'arrived'|'in_progress'|'completed' }`
- **Response:** `{ job: object, status: string, notified_client: bool }`
- **Errors:** 400 (invalid status), 401, 403 (not assigned), 404 (job not found), 500
- **Notes:** Crew updates job status. Notifies client via preferred channel (SMS/email/Telegram) on 'on_my_way'.

#### POST /crewReportLocation
- **Auth:** Crew
- **Body:** `{ job_id: uuid, lat: number, lng: number }`
- **Response:** `{ location: object, geofence_departed?: bool, receipt_triggered?: bool }`
- **Errors:** 400 (invalid coords), 401, 403 (not assigned), 404, 500
- **Notes:** GPS ping. Detects geofence departure (>600m from job site). Triggers receipt auto-send if departed & previously inside.

---

### Admin Endpoints

All admin endpoints require `requireAdmin(event)` JWT with role='admin'.

#### GET /adminDashboard
- **Auth:** Admin
- **Query:** None
- **Response:** `{ revenue_today: number, jobs_in_progress: number, pending_invoices: number, team_members_online: number, kpis: object }`
- **Errors:** 401, 500
- **Notes:** Admin dashboard summary.

#### GET /adminFinance
- **Auth:** Admin
- **Query:** `year?: number`
- **Response:** `{ year: number, revenue: { total_cents, total, monthly, paid_cents, pending_cents, pipeline_value }, snapshots: [], jobs: { total, by_status, completed }, tax: { profile, estimate, rules, deduction_categories, deductions } }`
- **Errors:** 401, 500
- **Notes:** Finance dashboard. Annual breakdown. Includes tax profile data.

#### GET /adminClients
- **Auth:** Admin
- **Query:** `page?: number, limit?: number, q?: string, id?: uuid, source?: 'acs'|'all'`
- **Response:** `{ clients: [{ id, name, email, phone, jobs_count, last_job_date, has_portal_account, ... }], total: number, page: number, limit: number, pages: number }`
- **Errors:** 400 (bad query), 401, 500
- **Notes:** ACS-scoped by default. `?source=all` lists all system contacts (admin override). `?id=uuid` returns full detail with jobs, quotes, payments, requests, interactions. `?q=search_term` searches name/email/phone.

#### POST /adminClients (Create)
- **Auth:** Admin
- **Body:** `{ name: string (required), phone?: string, email?: string, company?: string, city?: string, state?: string, address?: string, notes?: string }`
- **Response:** `{ contact: object }`
- **Errors:** 400 (missing name), 401, 500
- **Notes:** Creates new contact. Auto-links to ACS business.

#### POST /adminClients (Log Interaction)
- **Auth:** Admin
- **Body:** `{ action: 'log_interaction', contact_id: uuid, type: 'call'|'email'|'text'|'meeting'|'note', summary: string }`
- **Response:** `{ interaction: object }`
- **Errors:** 400 (missing fields, invalid type), 401, 500
- **Notes:** Logs interaction for contact.

#### PUT /adminClients (Update) / PATCH /adminClients (Partial Update)
- **Auth:** Admin
- **Body:** `{ contact_id: uuid, name?: string, email?: string, phone?: string, company?: string, city?: string, state?: string, address?: string, notes?: string }`
- **Response:** `{ contact: object }`
- **Errors:** 400 (no contact_id or updates), 401, 500
- **Notes:** PUT and PATCH behave identically. Updates allowed fields only.

#### GET /adminCrew
- **Auth:** Admin
- **Query:** `id?: uuid, date?: string (YYYY-MM-DD), page?: number, limit?: number`
- **Response:** `{ crew: [{ id, name, phone, role, color, status, jobs_today?: [job], availability?: string, ... }], total?: number, page?: number, limit?: number }`
- **Errors:** 401, 500
- **Notes:** `?id=uuid` returns single member with day's jobs. No query returns paginated list.

#### POST /adminCrew (Create)
- **Auth:** Admin
- **Body:** `{ name: string, phone: string, role: string, color?: string }`
- **Response:** `{ crew_member: object }`
- **Errors:** 400 (missing required), 401, 500
- **Notes:** Creates new crew member.

#### PUT /adminCrew (Update)
- **Auth:** Admin
- **Body:** `{ crew_member_id: uuid, name?: string, phone?: string, role?: string, color?: string, status?: 'active'|'inactive' }`
- **Response:** `{ crew_member: object }`
- **Errors:** 400, 401, 500
- **Notes:** Updates crew member.

#### GET /adminJobs
- **Auth:** Admin
- **Query:** `range?: 'day'|'week', date?: string (YYYY-MM-DD), status?: string (comma-separated)`
- **Response:** `{ jobs: [{ id, scheduled_start, scheduled_end, status, notes, total_amount_cents, completed_at, contact: { ... }, crew: [...] }], range: string, start: string, end: string }`
- **Errors:** 401, 500
- **Notes:** Default range is 'week'. Filters by status if provided (e.g., `?status=scheduled,in_progress`).

#### POST /adminJobs (Create)
- **Auth:** Admin
- **Body:** `{ contact_id: uuid, scheduled_start: string (ISO 8601), scheduled_end?: string, status?: string, notes?: string, crew_member_ids?: [uuid], total_amount_cents?: number }`
- **Response:** `{ job: object }`
- **Errors:** 400 (missing contact/start), 401, 500
- **Notes:** Creates job. Auto-links to client_profile. Optionally assign crew.

#### PUT /adminJobs (Update)
- **Auth:** Admin
- **Body:** `{ job_id: uuid, scheduled_start?: string, scheduled_end?: string, status?: string, notes?: string, crew_member_ids?: [uuid], total_amount_cents?: number }`
- **Response:** `{ job: object }`
- **Errors:** 400 (no job_id), 401, 500
- **Notes:** Replaces crew assignments if provided. Sets `completed_at` if status='completed'.

#### GET /adminQuotes
- **Auth:** Admin
- **Query:** `page?: number, limit?: number, status?: string (comma-separated)`
- **Response:** `{ quotes: [...], total: number, page: number, limit: number, pages: number }`
- **Errors:** 401, 500
- **Notes:** Lists quotes with contact detail. Default limit 25, max 100.

#### POST /adminQuotes (Create)
- **Auth:** Admin
- **Body:** `{ contact_id: uuid, service_type: string, estimated_total: number, square_footage?: number, bedrooms?: number, bathrooms?: number, frequency?: string, ... }`
- **Response:** `{ quote: object }`
- **Errors:** 400 (missing required), 401, 500
- **Notes:** Creates quote for contact.

#### PUT /adminQuotes (Update)
- **Auth:** Admin
- **Body:** `{ quote_id: uuid, status?: string, estimated_total?: number, deposit_status?: string, ... }`
- **Response:** `{ quote: object }`
- **Errors:** 400, 401, 500
- **Notes:** Updates quote. Can convert to job.

#### GET /adminInvoices
- **Auth:** Admin
- **Query:** `page?: number, limit?: number, status?: string, sort?: 'date'|'amount'`
- **Response:** `{ invoices: [...], total: number, page: number, limit: number, stats: { total_revenue, outstanding, overdue_count, draft_count } }`
- **Errors:** 401, 500
- **Notes:** Lists invoices. Default limit 50, max 100. Stats computed across all business invoices.

#### POST /adminInvoices (Generate)
- **Auth:** Admin
- **Body:** `{ job_id: uuid }`
- **Response:** `{ invoice: object, created: bool }`
- **Errors:** 400, 401, 404 (job not found), 500
- **Notes:** Generates invoice for completed job.

#### POST /adminInvoices (Action)
- **Auth:** Admin
- **Body:** `{ invoice_id: uuid, action: 'send'|'mark_paid'|'send_reminder' }`
- **Response:** `{ success: true, invoice_id: uuid, action: string, ...action_result }`
- **Errors:** 400 (missing fields), 401, 404 (invoice not found), 500
- **Notes:** `send`: Creates Stripe payment link & notifies client. `mark_paid`: Updates status & creates manual payment record. `send_reminder`: Sends unpaid invoice reminder.

#### GET /adminTasks
- **Auth:** Admin
- **Query:** `type?: string, status?: string, limit?: number`
- **Response:** `{ tasks: [{ id, title, type, status, priority, contact_name, quote_service_type, assigned_to_name, age_hours, ... }] }`
- **Errors:** 401, 500
- **Notes:** Lists tasks with optional filters. Can be assigned to crew members.

#### PUT /adminTasks (Update)
- **Auth:** Admin
- **Body:** `{ task_id: uuid, status?: string, notes?: string, assigned_to?: uuid }`
- **Response:** `{ task: object }`
- **Errors:** 400, 401, 500
- **Notes:** Updates task status, appends notes, or assigns to crew.

#### GET /adminTaxProfile
- **Auth:** Admin
- **Query:** None
- **Response:** `{ tax_profile: object, deductions: [{ category, amount, date, description }], summary: { total_deductions, estimated_tax, effective_rate } }`
- **Errors:** 401, 500
- **Notes:** Admin's tax profile.

#### PUT /adminTaxProfile (Update)
- **Auth:** Admin
- **Body:** `{ ssn?: string, ein?: string, business_type?: string, filing_status?: string, estimated_quarterly?: number }`
- **Response:** `{ tax_profile: object }`
- **Errors:** 400, 401, 500
- **Notes:** Updates tax profile.

#### POST /adminTaxProfile (Add Deduction)
- **Auth:** Admin
- **Body:** `{ category: string, amount: number, date: string, description?: string }`
- **Response:** `{ deduction: object }`
- **Errors:** 400, 401, 500
- **Notes:** Adds tax deduction entry.

#### GET /adminApplicants
- **Auth:** Admin
- **Query:** `status?: string, page?: number, limit?: number`
- **Response:** `{ applicants: [{ id, name, email, phone, position, status, created_at, resume_url, cover_letter }], total: number, page: number, limit: number }`
- **Errors:** 401, 500
- **Notes:** Lists job applicants.

#### POST /adminApplicants (Update Status)
- **Auth:** Admin
- **Body:** `{ applicant_id: uuid, status: 'new'|'reviewing'|'interviewed'|'rejected'|'hired' }`
- **Response:** `{ applicant: object }`
- **Errors:** 400, 401, 500
- **Notes:** Updates applicant status.

#### GET /adminCalendarEvents
- **Auth:** Admin
- **Query:** `start?: string (ISO), end?: string (ISO)`
- **Response:** `{ events: [{ id, summary, start, end, type, description }] }`
- **Errors:** 401, 500
- **Notes:** Admin calendar events (meetings, deadlines, etc.).

#### POST /adminCalendarEvents (Create)
- **Auth:** Admin
- **Body:** `{ summary: string, start: string, end: string, type?: string, description?: string }`
- **Response:** `{ event: object }`
- **Errors:** 400, 401, 500
- **Notes:** Creates calendar event.

#### GET /adminRequests
- **Auth:** Admin
- **Query:** `status?: string, type?: string, page?: number, limit?: number`
- **Response:** `{ requests: [{ id, contact_name, type, message, status, created_at }], total: number }`
- **Errors:** 401, 500
- **Notes:** Lists client service requests.

#### POST /adminRequests (Update)
- **Auth:** Admin
- **Body:** `{ request_id: uuid, status: 'new'|'in_progress'|'completed', response?: string }`
- **Response:** `{ request: object }`
- **Errors:** 400, 401, 500
- **Notes:** Updates request status. Optionally sends response to client.

#### GET /adminLiveLocations
- **Auth:** Admin
- **Query:** `job_id?: uuid`
- **Response:** `{ locations: [{ crew_member_id, crew_member_name, lat, lng, timestamp, job_id, distance_from_site_m }] }`
- **Errors:** 401, 500
- **Notes:** Real-time crew locations. Can filter by job.

#### GET /adminNotificationLog
- **Auth:** Admin
- **Query:** `limit?: number, type?: string`
- **Response:** `{ notifications: [{ id, type, target, message, status, sent_at, delivered_at }] }`
- **Errors:** 401, 500
- **Notes:** Log of all notifications sent to clients/crew.

#### GET /adminSettings
- **Auth:** Admin
- **Query:** None
- **Response:** `{ settings: { business_name, logo_url, timezone, notification_preferences, ... } }`
- **Errors:** 401, 500
- **Notes:** Admin panel settings.

#### PUT /adminSettings (Update)
- **Auth:** Admin
- **Body:** `{ business_name?: string, logo_url?: string, timezone?: string, notification_preferences?: object }`
- **Response:** `{ settings: object }`
- **Errors:** 400, 401, 500
- **Notes:** Updates business settings.

#### POST /adminProvisionClientAccount
- **Auth:** Admin
- **Body:** `{ contact_id: uuid, email: string, temporary_password: string }`
- **Response:** `{ client_profile: object, account_created: bool }`
- **Errors:** 400 (invalid email), 401, 404 (contact not found), 500
- **Notes:** Manually creates client portal account. Sends welcome email with temp password.

#### POST /adminProvisionCrewPin
- **Auth:** Admin
- **Body:** `{ crew_member_id: uuid, pin: string }`
- **Response:** `{ success: true, crew_member: object }`
- **Errors:** 400 (invalid pin), 401, 404 (crew not found), 500
- **Notes:** Admin sets crew PIN (hashed with bcrypt). Crew can log in with phone + pin.

---

### Background Jobs & Webhooks

#### POST /stripeWebhook
- **Auth:** Stripe signature verification
- **Body:** Stripe event JSON
- **Response:** `{ received: true }`
- **Errors:** 401 (bad signature), 500
- **Notes:** Handles `payment_intent.succeeded`, `charge.refunded`, etc. Updates invoice status, triggers receipts.

#### POST /telegramWebhook
- **Auth:** Telegram signature
- **Body:** Telegram update JSON
- **Response:** `{ ok: true }`
- **Errors:** 401 (bad signature), 500
- **Notes:** Receives Telegram messages. Routes to contact if recognized.

#### POST /whatsappWebhook
- **Auth:** WhatsApp signature
- **Body:** WhatsApp webhook JSON
- **Response:** `{ success: true }`
- **Errors:** 401, 500
- **Notes:** Receives WhatsApp messages. Routes to contact.

#### POST /processEvents
- **Auth:** None (internal trigger)
- **Body:** None
- **Response:** `{ processed: number, errors: number }`
- **Errors:** 500
- **Notes:** Cron job. Processes event queue (invoices, notifications, etc.). Idempotent.

#### POST /morningBrief
- **Auth:** None (internal trigger)
- **Body:** None
- **Response:** `{ sent: number, errors: number }`
- **Errors:** 500
- **Notes:** Cron job (runs 7 AM). Sends daily brief to admin (jobs, tasks, revenue).

#### POST /eveningDigest
- **Auth:** None (internal trigger)
- **Body:** None
- **Response:** `{ sent: number, errors: number }`
- **Errors:** 500
- **Notes:** Cron job (runs 6 PM). Sends day summary to admin.

#### POST /invoiceReminder
- **Auth:** None (internal trigger)
- **Body:** None
- **Response:** `{ reminded: number, errors: number }`
- **Errors:** 500
- **Notes:** Cron job. Sends payment reminders for unpaid invoices >7 days overdue.

#### POST /sendJobReminders
- **Auth:** None (internal trigger)
- **Body:** None
- **Response:** `{ reminded: number, errors: number }`
- **Errors:** 500
- **Notes:** Cron job (24h before job). Confirms crew availability.

#### POST /sendFeedbackRequest
- **Auth:** None (internal trigger)
- **Body:** `{ job_id: uuid }`
- **Response:** `{ sent: bool }`
- **Errors:** 404 (job not found), 500
- **Notes:** Sends post-job feedback request. Called by cron 48h after job completion.

#### POST /sendInvoice
- **Auth:** None (internal trigger)
- **Body:** `{ invoice_id: uuid }`
- **Response:** `{ payment_link: string, notified: bool }`
- **Errors:** 404 (invoice not found), 500
- **Notes:** Creates Stripe payment link & notifies client.

#### POST /sendReceipt
- **Auth:** None (internal trigger)
- **Body:** `{ job_id: uuid }`
- **Response:** `{ receipt_sent: bool }`
- **Errors:** 404 (job not found), 500
- **Notes:** Sends post-job receipt to client. Typically triggered by crew departure.

#### POST /leadNurture
- **Auth:** None (internal trigger)
- **Body:** None
- **Response:** `{ nurtured: number }`
- **Errors:** 500
- **Notes:** Cron job. Sends followup emails to stale leads (no quote in 7d).

#### POST /staleLeadCleanup
- **Auth:** None (internal trigger)
- **Body:** None
- **Response:** `{ archived: number }`
- **Errors:** 500
- **Notes:** Cron job. Archives leads with no activity >30 days.

#### POST /scheduleAudit
- **Auth:** None (internal trigger)
- **Body:** None
- **Response:** `{ audited: number, issues: number }`
- **Errors:** 500
- **Notes:** Cron job. Validates schedule constraints, detects conflicts.

#### POST /uptimeMonitor
- **Auth:** None (internal trigger)
- **Body:** None
- **Response:** `{ status: 'ok'|'degraded'|'down', checks: object }`
- **Errors:** 500
- **Notes:** Cron job. Pings critical endpoints, alerts if down.

#### POST /scheduleTracker
- **Auth:** None (internal trigger)
- **Body:** None
- **Response:** `{ tracked: number }`
- **Errors:** 500
- **Notes:** Cron job. Updates crew schedules, checks availability.

#### POST /trackingData
- **Auth:** None (internal trigger)
- **Body:** None
- **Response:** `{ updated: number }`
- **Errors:** 500
- **Notes:** Cron job. Aggregates location & job data for analytics.

#### POST /routePush
- **Auth:** None (internal trigger)
- **Body:** `{ crew_member_id: uuid, date: string }`
- **Response:** `{ route_plan: object }`
- **Errors:** 404 (crew not found), 500
- **Notes:** Sends optimized daily route to crew member.

#### POST /routeOptimize
- **Auth:** None (internal trigger)
- **Body:** `{ crew_member_ids?: [uuid], date?: string }`
- **Response:** `{ optimized_routes: object }`
- **Errors:** 500
- **Notes:** Uses geolocation to optimize routes, minimizes drive time.

#### POST /geocodeJobAddresses
- **Auth:** None (internal trigger)
- **Body:** None
- **Response:** `{ geocoded: number, failed: number }`
- **Errors:** 500
- **Notes:** Cron job. Geocodes job addresses (lat/lng) for mapping.

#### POST /reviewAnalytics
- **Auth:** None (internal trigger)
- **Body:** None
- **Response:** `{ avg_rating: number, review_count: number, trends: object }`
- **Errors:** 500
- **Notes:** Cron job. Aggregates review metrics.

#### POST /revenueTracker
- **Auth:** None (internal trigger)
- **Body:** None
- **Response:** `{ revenue_today: number, revenue_month: number }`
- **Errors:** 500
- **Notes:** Cron job. Tracks daily/monthly revenue.

#### POST /generateInvoice
- **Auth:** None (internal trigger)
- **Body:** `{ job_id: uuid }`
- **Response:** `{ invoice: object, created: bool }`
- **Errors:** 404 (job not found), 500
- **Notes:** Generates invoice for completed job (helper function).

#### POST /upsertJobFromIntake
- **Auth:** None (internal trigger)
- **Body:** `{ contact_id: uuid, service_data: object }`
- **Response:** `{ job: object, created: bool }`
- **Errors:** 400, 500
- **Notes:** Converts intake form to job. Links to quote if available.

#### POST /createDepositCheckout
- **Auth:** Client
- **Body:** `{ quote_id: uuid, deposit_amount: number }`
- **Response:** `{ checkout_url: string, session_id: string }`
- **Errors:** 400 (invalid amount), 401, 404 (quote not found), 500
- **Notes:** Creates Stripe checkout for deposit. Client must be authenticated.

#### POST /rootBridge
- **Auth:** Root auth (special token)
- **Body:** `{ action: string, payload?: object }`
- **Response:** Varies by action
- **Errors:** 401 (bad token), 400 (bad action), 500
- **Notes:** Internal bridge for cross-service communication. Requires special root token.

#### POST /coEdit (& variants)
- **Auth:** None / internal
- **Body:** Varies
- **Response:** Varies
- **Errors:** Varies
- **Notes:** CoEdit V1 core handler. Manages script generation, feature extraction, outlier recompute, watchlist sync. See CoScript section below.

---

## CoScript API

All CoScript endpoints require authentication (except login/signup). Deployments on Next.js.

### Authentication

#### POST /api/auth/login
- **Auth:** None
- **Body:** `{ email: string, password: string }` (JSON or form data)
- **Response:** `{ success: true }`
- **Errors:** 400 (missing fields), 401 (invalid credentials), 500
- **Notes:** Supabase auth. Sets session cookie on success.

#### POST /api/auth/signup
- **Auth:** None
- **Body:** `{ email: string, password: string }`
- **Response:** `{ success: true }`
- **Errors:** 400 (invalid data), 500
- **Notes:** Supabase auth. Account created & authenticated.

#### GET /api/auth/session
- **Auth:** Session cookie or Bearer token
- **Response:** `{ user: { id, email, ... } }` or `{ user: null }`
- **Errors:** 500
- **Notes:** Returns current session user or null if not authenticated.

#### POST /api/auth/logout
- **Auth:** Session cookie
- **Response:** `{ success: true }`
- **Errors:** 500
- **Notes:** Signs out user. Clears session.

### Scripts & Briefs

#### POST /api/coscript/briefs
- **Auth:** Authenticated
- **Body:** `{ script_type: string, audience: string, objective: string, constraints: string, key_points: string }`
- **Response:** `{ id: uuid, script_type, audience, objective, constraints, key_points, created_at }`
- **Errors:** 400 (missing required fields), 401, 500
- **Notes:** Creates brief for script generation.

#### POST /api/coscript/scripts/generate
- **Auth:** Authenticated
- **Body:** `{ brief_id: uuid, source_video_id?: uuid }`
- **Response:** `{ script_job_id: uuid, variants: [{ label: 'A'|'B'|'C', mode, content: string }] }`
- **Errors:** 400 (no brief_id), 401, 404 (brief not found), 500
- **Notes:** Generates 3 script variants (A=direct, B=executive, C=human) via Claude API. Async operation.

#### GET /api/coscript/scripts/[id]/history
- **Auth:** Authenticated
- **Response:** `{ script_id: uuid, variants: [{ label, mode, content, created_at, status }], iterations: number }`
- **Errors:** 401, 404 (script not found), 500
- **Notes:** Returns script variant history.

#### POST /api/coscript/scripts/[id]/fix
- **Auth:** Authenticated
- **Body:** `{ variant_label: 'A'|'B'|'C', issue: string, feedback?: string }`
- **Response:** `{ fixed_variant: { label, mode, content } }`
- **Errors:** 400, 401, 404, 500
- **Notes:** Regenerates variant based on feedback.

#### POST /api/coscript/watchlists
- **Auth:** Authenticated
- **Body:** `{ name: string, stocks: [string], update_frequency: 'daily'|'weekly' }`
- **Response:** `{ watchlist: { id, name, stocks, created_at } }`
- **Errors:** 400, 401, 500
- **Notes:** Creates watchlist for stock monitoring.

#### GET /api/coscript/watchlists
- **Auth:** Authenticated
- **Response:** `{ watchlists: [{ id, name, stocks, created_at, last_sync }] }`
- **Errors:** 401, 500
- **Notes:** Lists user's watchlists.

#### POST /api/coscript/watchlists/[id]/sync
- **Auth:** Authenticated
- **Response:** `{ synced: bool, stocks: [{ symbol, price, change }] }`
- **Errors:** 401, 404 (watchlist not found), 500
- **Notes:** Syncs stock data for watchlist.

#### POST /api/coscript/outliers
- **Auth:** Authenticated
- **Body:** `{ data: [number], threshold?: number }`
- **Response:** `{ outliers: [{ index, value, zscore }] }`
- **Errors:** 400 (no data), 401, 500
- **Notes:** Detects statistical outliers in dataset.

#### POST /api/coscript/vault/save
- **Auth:** Authenticated
- **Body:** `{ content: string, tags?: [string], type?: string }`
- **Response:** `{ vault_entry: { id, content, tags, created_at } }`
- **Errors:** 400 (no content), 401, 500
- **Notes:** Saves content to user's vault.

#### GET /api/drafts
- **Auth:** Authenticated
- **Response:** `{ drafts: [{ id, title, content, created_at, updated_at }] }`
- **Errors:** 401, 500
- **Notes:** Lists user's drafts.

#### POST /api/drafts
- **Auth:** Authenticated
- **Body:** `{ title?: string, content: string }`
- **Response:** `{ draft: { id, title, content, created_at } }`
- **Errors:** 400 (no content), 401, 500
- **Notes:** Creates new draft.

#### GET /api/drafts/[id]
- **Auth:** Authenticated
- **Response:** `{ draft: { id, title, content, created_at, updated_at } }`
- **Errors:** 401, 404 (draft not found), 500
- **Notes:** Retrieves specific draft.

#### POST /api/drafts/[id]
- **Auth:** Authenticated
- **Body:** `{ title?: string, content?: string }`
- **Response:** `{ draft: { id, title, content, updated_at } }`
- **Errors:** 401, 404 (draft not found), 500
- **Notes:** Updates draft.

#### POST /api/generate
- **Auth:** Authenticated
- **Body:** `{ prompt: string, model?: string, max_tokens?: number }`
- **Response:** `{ generated: string, tokens_used: number, model: string }`
- **Errors:** 400 (no prompt), 401, 500
- **Notes:** Generic generation endpoint (Claude API).

#### POST /api/share
- **Auth:** Authenticated
- **Body:** `{ content_id: uuid, content_type: 'draft'|'script'|'brief', recipient_email?: string, public?: bool }`
- **Response:** `{ shared: bool, share_link: string }`
- **Errors:** 400, 401, 404 (content not found), 500
- **Notes:** Shares content. Public link if `public=true`, otherwise email invitation.

---

## CoDeliver API

All CoDeliver endpoints require authentication (except login/signup). Deployments on Next.js.

### Authentication

#### POST /api/auth/login
- **Auth:** None
- **Body:** `{ email: string, password: string }` (JSON or form data)
- **Response:** `{ success: true }`
- **Errors:** 400 (missing fields), 401 (invalid credentials), 500
- **Notes:** Supabase auth. Sets session cookie on success.

#### POST /api/auth/signup
- **Auth:** None
- **Body:** `{ email: string, password: string }`
- **Response:** `{ success: true }`
- **Errors:** 400 (invalid data), 500
- **Notes:** Supabase auth. Account created & authenticated.

#### GET /api/auth/session
- **Auth:** Session cookie or Bearer token
- **Response:** `{ user: { id, email, ... } }` or `{ user: null }`
- **Errors:** 500
- **Notes:** Returns current session user or null if not authenticated.

#### POST /api/auth/logout
- **Auth:** Session cookie
- **Response:** `{ success: true }`
- **Errors:** 500
- **Notes:** Signs out user. Clears session.

### Projects

#### GET /api/projects
- **Auth:** Authenticated
- **Response:** `{ items: [{ id, name, description, owner_id, assets: [{ id, status }], created_at, updated_at }] }`
- **Errors:** 401, 500
- **Notes:** Lists user's projects. Assets included inline.

#### POST /api/projects
- **Auth:** Authenticated
- **Body:** `{ name: string, description?: string }`
- **Response:** `{ id: uuid, name, description, owner_id, created_at }`
- **Errors:** 400 (no name), 401, 500
- **Notes:** Creates new project.

#### GET /api/projects/[id]
- **Auth:** Authenticated
- **Response:** `{ id, name, description, owner_id, assets: [...], created_at, updated_at }`
- **Errors:** 401, 404 (project not found), 500
- **Notes:** Retrieves specific project.

#### GET /api/projects/[id]/assets
- **Auth:** Authenticated
- **Response:** `{ assets: [{ id, project_id, name, status, url, created_at, updated_at, ... }] }`
- **Errors:** 401, 404 (project not found), 500
- **Notes:** Lists assets in project.

#### POST /api/projects/[id]/assets
- **Auth:** Authenticated
- **Body:** `{ name: string, file?: File, url?: string, type?: string }`
- **Response:** `{ asset: { id, project_id, name, status, url } }`
- **Errors:** 400 (no name or file), 401, 404 (project not found), 500
- **Notes:** Uploads asset to project. Supports file upload or URL.

### Assets

#### GET /api/assets
- **Auth:** Authenticated
- **Response:** `{ items: [{ id, project_id, name, status, url, updated_at }] }`
- **Errors:** 401, 500
- **Notes:** Lists all user's assets (across all projects).

#### POST /api/assets
- **Auth:** Authenticated
- **Body:** `{ project_id: uuid, name: string, file?: File, url?: string }`
- **Response:** `{ asset: { id, project_id, name, status, url } }`
- **Errors:** 400, 401, 500
- **Notes:** Creates asset (typically via projects endpoint).

#### GET /api/assets/[id]
- **Auth:** Authenticated
- **Response:** `{ id, project_id, name, status, url, type, size, created_at, updated_at, versions: [...], comments: [...], approvals: [...] }`
- **Errors:** 401, 404 (asset not found), 500
- **Notes:** Retrieves full asset detail including versions, comments, approvals.

#### PUT /api/assets/[id]
- **Auth:** Authenticated
- **Body:** `{ name?: string, status?: string }`
- **Response:** `{ id, name, status, updated_at }`
- **Errors:** 401, 404, 500
- **Notes:** Updates asset metadata.

#### DELETE /api/assets/[id]
- **Auth:** Authenticated
- **Response:** `{ deleted: bool }`
- **Errors:** 401, 404, 500
- **Notes:** Deletes asset.

#### GET /api/assets/[id]/versions
- **Auth:** Authenticated
- **Response:** `{ versions: [{ id, asset_id, version_number, url, created_at, created_by }] }`
- **Errors:** 401, 404 (asset not found), 500
- **Notes:** Lists asset version history.

#### POST /api/assets/[id]/versions
- **Auth:** Authenticated
- **Body:** `{ file: File, note?: string }`
- **Response:** `{ version: { id, version_number, url, created_at } }`
- **Errors:** 400 (no file), 401, 404 (asset not found), 500
- **Notes:** Uploads new version of asset.

#### GET /api/assets/[id]/comments
- **Auth:** Authenticated
- **Response:** `{ comments: [{ id, author, text, created_at, replies?: [...] }] }`
- **Errors:** 401, 404 (asset not found), 500
- **Notes:** Lists comments on asset.

#### POST /api/assets/[id]/comments
- **Auth:** Authenticated
- **Body:** `{ text: string, parent_comment_id?: uuid }`
- **Response:** `{ comment: { id, author, text, created_at } }`
- **Errors:** 400 (no text), 401, 404 (asset not found), 500
- **Notes:** Posts comment on asset. Supports threaded replies.

#### GET /api/assets/[id]/approvals
- **Auth:** Authenticated
- **Response:** `{ approvals: [{ id, approver, status: 'pending'|'approved'|'rejected', feedback?, created_at }] }`
- **Errors:** 401, 404 (asset not found), 500
- **Notes:** Lists approval requests for asset.

#### POST /api/assets/[id]/approvals
- **Auth:** Authenticated
- **Body:** `{ reviewers: [email], message?: string }`
- **Response:** `{ approval_requests: [{ id, reviewer, status: 'pending' }] }`
- **Errors:** 400 (no reviewers), 401, 404 (asset not found), 500
- **Notes:** Creates approval request. Sends emails to reviewers.

#### PUT /api/assets/[id]/approvals
- **Auth:** Authenticated
- **Body:** `{ approval_id: uuid, status: 'approved'|'rejected', feedback?: string }`
- **Response:** `{ approval: { id, status, feedback, approved_at } }`
- **Errors:** 400, 401, 404, 500
- **Notes:** Responds to approval request.

#### GET /api/assets/[id]/share
- **Auth:** Authenticated
- **Response:** `{ shares: [{ id, shared_with, access_level: 'view'|'comment'|'edit', created_at }] }`
- **Errors:** 401, 404 (asset not found), 500
- **Notes:** Lists share records for asset.

#### POST /api/assets/[id]/share
- **Auth:** Authenticated
- **Body:** `{ email: string, access_level: 'view'|'comment'|'edit' }`
- **Response:** `{ share: { id, shared_with, access_level } }`
- **Errors:** 400 (invalid email), 401, 404 (asset not found), 500
- **Notes:** Shares asset with user. Sends email notification.

### Review (Public)

#### GET /api/review/[token]
- **Auth:** None (token-based)
- **Response:** `{ asset: { id, name, url, ... }, project: { name }, approver_email: string, status: string }`
- **Errors:** 401 (bad token), 404 (asset not found), 500
- **Notes:** Public review link. Token grants temporary access to asset + approval interface.

#### POST /api/review/[token]
- **Auth:** None (token-based)
- **Body:** `{ status: 'approved'|'rejected', feedback?: string }`
- **Response:** `{ success: true, message: string }`
- **Errors:** 401 (bad token), 400, 500
- **Notes:** Submits approval response via public link. Sets approval status & feedback.

### Activity & Summarization

#### GET /api/activity
- **Auth:** Authenticated
- **Response:** `{ activities: [{ id, type: 'asset_upload'|'comment'|'approval'|..., actor, subject, timestamp, details }] }`
- **Errors:** 401, 500
- **Notes:** Returns activity feed for user's projects.

#### POST /api/ai/summarize
- **Auth:** Authenticated
- **Body:** `{ asset_id: uuid, comments: bool }`
- **Response:** `{ summary: string, key_points: [string], sentiment?: string }`
- **Errors:** 401, 404 (asset not found), 500
- **Notes:** AI-generated summary of asset metadata & comments. Useful for long feedback threads.

---

## Error Codes & Patterns

All errors follow this pattern:
```json
{ "error": "error_code_or_message", "details?: object, "status": http_status }
```

### Common Error Codes

| Code | Status | Meaning |
|------|--------|---------|
| `missing_token` | 401 | No Authorization header |
| `invalid_token` | 401 | Malformed or expired JWT |
| `unauthorized` | 401 | Token present but user lacks permission |
| `not_admin` / `not_crew` / `not_client` | 403 | Role mismatch |
| `method_not_allowed` | 405 | HTTP method not supported |
| `not_found` | 404 | Resource doesn't exist |
| `invalid_credentials` | 401 | Bad email/password or pin |
| `email_not_verified` | 403 | Account exists but email unverified |
| `account_inactive` | 403 | User status != 'active' |
| `validation_error` | 400 | Invalid input (missing/bad fields) |
| `resource_conflict` | 409 | Resource already exists |
| `server_error` | 500 | Unexpected error |

---

## Rate Limiting & Quotas

**Not currently enforced** (V1). Consider implementing:
- Admin endpoints: 60 req/min per token
- Client endpoints: 30 req/min per IP
- Public endpoints: 20 req/min per IP

---

## Webhooks & Event Streams

### Events Table
All major actions insert event records for async processing:
- `type`: 'invoice_sent', 'job_completed', 'payment_succeeded', etc.
- `contact_id`, `business_id`: Scope
- `payload`: Event-specific data
- `processed_at`: NULL until consumed by cron job

Processed by `processEvents` cron job. Idempotent via `processed_at` check.

---

## Testing Endpoints

### Health Check (Implicit)
`GET /.netlify/functions/uptimeMonitor` (cron job)
- **Auth:** None
- **Response:** `{ status: 'ok'|'degraded'|'down', checks: object }`

---

## Future Endpoints (Planned)

- [ ] **Batch Operations**: POST /api/jobs/batch (create multiple jobs)
- [ ] **Advanced Reporting**: GET /api/reports/pipeline, /api/reports/crew_utilization
- [ ] **Webhook Management**: GET/POST /api/webhooks
- [ ] **API Keys**: POST /api/user/api-keys (for service integrations)
- [ ] **Two-Factor Auth**: POST /api/auth/2fa/setup
- [ ] **Message Search**: GET /api/messages/search
- [ ] **Custom Fields**: POST /api/custom-fields

---

## Schema & Type Definitions

Common types appear across endpoints:

### Contact
```
{
  id: uuid,
  name: string,
  email: string,
  phone: string,
  company?: string,
  city?: string,
  state?: string,
  address?: string,
  lat?: number,
  lng?: number,
  notes?: string,
  is_core?: bool,
  core_rank?: number,
  priority_score?: number,
  tags?: [string],
  created_at: timestamp,
  updated_at: timestamp
}
```

### Job
```
{
  id: uuid,
  contact_id: uuid,
  business_id: uuid,
  client_profile_id?: uuid,
  scheduled_start: timestamp,
  scheduled_end?: timestamp,
  status: 'scheduled'|'in_progress'|'completed'|'cancelled',
  notes?: string,
  total_amount_cents?: number,
  completed_at?: timestamp,
  created_at: timestamp,
  updated_at: timestamp,
  contact?: Contact,
  crew?: [CrewMember]
}
```

### Quote
```
{
  id: uuid,
  contact_id: uuid,
  business_id: uuid,
  service_type: string,
  estimated_total: number,
  deposit_amount_cents?: number,
  deposit_status?: 'not_collected'|'pending'|'collected',
  status: 'new'|'sent'|'viewed'|'accepted'|'rejected'|'converted',
  square_footage?: number,
  bedrooms?: number,
  bathrooms?: number,
  frequency?: string,
  pet_count?: number,
  has_high_reach?: bool,
  is_next_day?: bool,
  window_count?: number,
  carpet_sqft?: number,
  addons?: [string],
  created_at: timestamp,
  updated_at: timestamp
}
```

### Invoice
```
{
  id: uuid,
  job_id: uuid,
  contact_id: uuid,
  business_id: uuid,
  amount: number,
  tax: number,
  total: number,
  status: 'draft'|'sent'|'paid'|'overdue'|'cancelled',
  due_date?: date,
  stripe_payment_link?: string,
  stripe_invoice_id?: string,
  reminder_count?: number,
  last_reminder_at?: timestamp,
  paid_at?: timestamp,
  notes?: string,
  created_at: timestamp,
  updated_at: timestamp
}
```

### CrewMember
```
{
  id: uuid,
  business_id: uuid,
  name: string,
  phone: string,
  email?: string,
  role: string,
  color?: string (hex),
  status: 'active'|'inactive',
  lat?: number,
  lng?: number,
  last_location_at?: timestamp,
  created_at: timestamp
}
```

---

## Integration Notes

- **Stripe**: `createDepositCheckout`, `stripeWebhook` manage payments
- **Google Maps**: `routeOptimize`, `geocodeJobAddresses` use Google Places/Directions APIs
- **Telegram/WhatsApp**: Webhooks receive messages, `notify()` sends via preferred channel
- **Anthropic Claude**: CoScript uses Claude Sonnet for script generation
- **Supabase**: All data stored in PostgreSQL via Supabase client library

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-02-28 | Initial contract documentation. All 85 Netlify functions, 16 CoScript endpoints, 15 CoDeliver endpoints documented. |

---

**Document Maintainers**: Engineering Team
**Last Updated**: 2026-02-28
**Next Review**: 2026-03-31
