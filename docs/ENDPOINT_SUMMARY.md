# ACS Ecosystem — Endpoint Summary
**Generated:** 2026-02-28

Quick reference for all 116 documented endpoints across three services.

---

## ACS Website (Netlify Functions) — 85 Endpoints

### Authentication (8 endpoints)
- POST /adminLogin — PIN-based admin login
- POST /crewLogin — PIN-based crew login
- POST /authLogin — Unified email+password login (admin/crew/client)
- POST /authSignup — Self-service registration (crew/client)
- POST /authForgotPassword — Password reset request
- POST /authResetPassword — Password reset with token
- POST /authVerifyEmail — Email verification
- POST /loginGoogle — Google OAuth fallback

### Client Portal (16 endpoints)
- POST /createRequest — Client service request
- POST /submitQuote — Public quote request
- GET /getAvailableSlots — Next 30 days availability
- POST /bookSlot — Book cleaning appointment
- POST /confirmAppointment — Confirm appointment
- POST /submitFeedback — Post-job feedback
- POST /submitReview — Post-job review
- POST /requestReview — Trigger review request
- POST /submitApplication — Job application
- POST /setPin — Self-service PIN setup
- POST /createPublicDepositCheckout — Stripe deposit checkout
- GET /getTestimonials — Public testimonials
- GET /propertyLookup — Property data lookup
- POST /validatePromo — Promo code validation
- POST /validateReferral — Referral code validation
- POST /generateReferralCode — Generate referral code (client auth)

### Client Portal - Authenticated (1 endpoint)
- GET /getPortalData — Client dashboard

### Crew (3 endpoints)
- GET /crewPortalData — Crew schedule & route plan
- PUT /crewUpdateJobStatus — Update job status (on_my_way/arrived/in_progress/completed)
- POST /crewReportLocation — GPS ping & geofence detection

### Admin — Clients (4 endpoints)
- GET /adminClients — List/search/detail clients
- POST /adminClients — Create client
- POST /adminClients (log_interaction) — Log client interaction
- PUT/PATCH /adminClients — Update client

### Admin — Jobs (3 endpoints)
- GET /adminJobs — List jobs by date/status
- POST /adminJobs — Create job
- PUT /adminJobs — Update job & crew assignments

### Admin — Crew (3 endpoints)
- GET /adminCrew — List/detail crew
- POST /adminCrew — Create crew member
- PUT /adminCrew — Update crew member

### Admin — Quotes (3 endpoints)
- GET /adminQuotes — List quotes
- POST /adminQuotes — Create quote
- PUT /adminQuotes — Update quote

### Admin — Invoices (3 endpoints)
- GET /adminInvoices — List invoices with stats
- POST /adminInvoices (generate) — Generate invoice from job
- POST /adminInvoices (action) — send/mark_paid/send_reminder

### Admin — Tasks (2 endpoints)
- GET /adminTasks — List tasks with filters
- PUT /adminTasks — Update task status/notes/assignment

### Admin — Tax (3 endpoints)
- GET /adminTaxProfile — Get tax profile & deductions
- PUT /adminTaxProfile — Update tax profile
- POST /adminTaxProfile — Add deduction

### Admin — Applicants & Calendar (4 endpoints)
- GET /adminApplicants — List job applicants
- POST /adminApplicants — Update applicant status
- GET /adminCalendarEvents — List calendar events
- POST /adminCalendarEvents — Create calendar event

### Admin — Requests (2 endpoints)
- GET /adminRequests — List service requests
- POST /adminRequests — Update request status

### Admin — Monitoring & Settings (6 endpoints)
- GET /adminDashboard — Dashboard summary
- GET /adminFinance — Finance dashboard with tax data
- GET /adminLiveLocations — Crew real-time locations
- GET /adminNotificationLog — Notification history
- GET /adminSettings — Business settings
- PUT /adminSettings — Update settings

### Admin — Account Provisioning (2 endpoints)
- POST /adminProvisionClientAccount — Create client portal account
- POST /adminProvisionCrewPin — Set crew PIN

### Background & Webhooks (22 endpoints)
- POST /stripeWebhook — Stripe event handler
- POST /telegramWebhook — Telegram message handler
- POST /whatsappWebhook — WhatsApp message handler
- POST /processEvents — Async event processing (cron)
- POST /morningBrief — Morning digest (cron)
- POST /eveningDigest — Evening digest (cron)
- POST /invoiceReminder — Unpaid invoice reminder (cron)
- POST /sendJobReminders — 24h job confirmation (cron)
- POST /sendFeedbackRequest — Post-job feedback request (cron)
- POST /sendInvoice — Generate & send invoice
- POST /sendReceipt — Post-job receipt
- POST /leadNurture — Stale lead followup (cron)
- POST /staleLeadCleanup — Archive inactive leads (cron)
- POST /scheduleAudit — Validate schedule constraints (cron)
- POST /uptimeMonitor — Health check (cron)
- POST /scheduleTracker — Update crew availability (cron)
- POST /trackingData — Aggregate location data (cron)
- POST /routePush — Send daily route to crew
- POST /routeOptimize — Optimize daily routes
- POST /geocodeJobAddresses — Geocode job locations (cron)
- POST /reviewAnalytics — Aggregate review metrics (cron)
- POST /revenueTracker — Daily revenue tracking (cron)

### Utility (4 endpoints)
- POST /generateInvoice — Generate invoice (helper)
- POST /upsertJobFromIntake — Intake form → job
- POST /rootBridge — Internal cross-service bridge
- POST /coEdit (+ variants) — CoEdit core handler

---

## CoScript API (Next.js) — 16 Endpoints

### Authentication (4 endpoints)
- POST /api/auth/login — Email+password login
- POST /api/auth/signup — Create account
- GET /api/auth/session — Get current session
- POST /api/auth/logout — Sign out

### Scripts & Briefs (6 endpoints)
- POST /api/coscript/briefs — Create brief
- POST /api/coscript/scripts/generate — Generate 3 script variants (A/B/C)
- GET /api/coscript/scripts/[id]/history — Script variant history
- POST /api/coscript/scripts/[id]/fix — Regenerate variant with feedback
- POST /api/coscript/watchlists — Create stock watchlist
- POST /api/coscript/watchlists/[id]/sync — Sync watchlist data

### Content Management (4 endpoints)
- GET /api/coscript/watchlists — List watchlists
- POST /api/coscript/outliers — Detect statistical outliers
- POST /api/coscript/vault/save — Save to vault
- POST /api/share — Share content (draft/script/brief)

### Drafts (4 endpoints)
- GET /api/drafts — List drafts
- POST /api/drafts — Create draft
- GET /api/drafts/[id] — Retrieve draft
- POST /api/drafts/[id] — Update draft

### Generation (1 endpoint)
- POST /api/generate — Generic text generation (Claude API)

---

## CoDeliver API (Next.js) — 15 Endpoints

### Authentication (4 endpoints)
- POST /api/auth/login — Email+password login
- POST /api/auth/signup — Create account
- GET /api/auth/session — Get current session
- POST /api/auth/logout — Sign out

### Projects (4 endpoints)
- GET /api/projects — List user projects
- POST /api/projects — Create project
- GET /api/projects/[id] — Retrieve project
- GET /api/projects/[id]/assets — List project assets

### Assets (7 endpoints)
- GET /api/assets — List all user assets
- POST /api/assets — Create asset
- GET /api/assets/[id] — Get asset detail
- PUT /api/assets/[id] — Update asset metadata
- DELETE /api/assets/[id] — Delete asset
- GET /api/assets/[id]/versions — Asset version history
- POST /api/assets/[id]/versions — Upload new version

### Comments & Approvals (6 endpoints)
- GET /api/assets/[id]/comments — List comments
- POST /api/assets/[id]/comments — Post comment
- GET /api/assets/[id]/approvals — List approval requests
- POST /api/assets/[id]/approvals — Create approval request
- PUT /api/assets/[id]/approvals — Respond to approval
- GET /api/assets/[id]/share — List shares

### Sharing (1 endpoint)
- POST /api/assets/[id]/share — Share asset with user

### Review & Activity (3 endpoints)
- GET /api/review/[token] — Public review link
- POST /api/review/[token] — Submit approval via link
- GET /api/activity — User activity feed

### AI (1 endpoint)
- POST /api/ai/summarize — Summarize asset + comments

---

## Authentication Patterns

| Role | Token | Verified | Required Fields |
|------|-------|----------|-----------------|
| **Admin** | JWT with role='admin' | Email & PIN | admin_id, business_id |
| **Crew** | JWT with role='crew' | Phone & PIN or Email+Password | crew_member_id, business_id |
| **Client** | JWT with role='client' | Email+Password verified | client_profile_id, contact_id |
| **Public** | None | — | — |

---

## Common Query Parameters

| Param | Used By | Type | Example |
|-------|---------|------|---------|
| `page` | List endpoints | int | page=2 |
| `limit` | List endpoints | int | limit=50 |
| `status` | Filter endpoints | string | status=pending,completed |
| `range` | Date range | 'day'\|'week' | range=week |
| `date` | Date filter | YYYY-MM-DD | date=2026-02-28 |
| `id` | Detail lookup | uuid | id=abc-123-def |
| `q` / `search` | Search | string | q=john doe |
| `sort` | Sort | string | sort=date |
| `type` | Filter | string | type=invoice |

---

## Response Headers

| Header | Value | Notes |
|--------|-------|-------|
| `Content-Type` | `application/json` | All responses |
| `Authorization` | `Bearer <jwt>` | Required (except public/webhooks) |
| `X-Request-Id` | UUID | Tracing (internal) |

---

## Status Codes

| Code | Meaning | Common Causes |
|------|---------|---------------|
| **200** | OK | Successful request |
| **201** | Created | Resource created |
| **400** | Bad Request | Missing/invalid fields |
| **401** | Unauthorized | Missing or invalid token |
| **403** | Forbidden | Insufficient permissions |
| **404** | Not Found | Resource doesn't exist |
| **405** | Method Not Allowed | Wrong HTTP method |
| **409** | Conflict | Resource already exists |
| **500** | Server Error | Unexpected error |

---

## High-Level Data Flows

### Quote → Booking → Invoice → Payment

1. Client submits quote (POST /submitQuote) → Email to team
2. Admin reviews & sends to client (email)
3. Client books slot (POST /bookSlot) → Job created
4. Crew completes job → Invoice auto-generated (cron)
5. Admin sends invoice (POST /adminInvoices action=send) → Stripe link
6. Client pays via Stripe → Webhook (POST /stripeWebhook) updates status
7. Receipt auto-sent → Client portal updated

### Crew Daily Workflow

1. Crew login (POST /crewLogin) → JWT token
2. Fetch schedule (GET /crewPortalData) → Today + 7d jobs + route
3. Report location (POST /crewReportLocation) → GPS tracking
4. Update status (PUT /crewUpdateJobStatus) → Client notifications
5. Leave site (geofence departure) → Receipt auto-sent

### Admin Finance Review

1. Access dashboard (GET /adminDashboard) → KPIs
2. Detailed finance report (GET /adminFinance) → Revenue breakdown, tax data
3. Invoice list (GET /adminInvoices) → Stats, reminders
4. Tax profile (GET /adminTaxProfile) → Deductions, estimates

---

## Deployment Notes

- **ACS Website**: Netlify Functions (Node.js runtime)
- **CoScript**: Next.js 14 (App Router)
- **CoDeliver**: Next.js 14 (App Router)
- **Database**: Supabase PostgreSQL
- **Auth**: Supabase Auth (email/password) + custom JWT
- **Payments**: Stripe
- **External APIs**: Google Maps, Telegram, WhatsApp, Anthropic Claude

---

## Testing Checklist

- [ ] All auth endpoints (login, signup, verify, reset)
- [ ] All client-facing endpoints (quote, booking, feedback)
- [ ] All admin CRUD operations
- [ ] All crew status updates
- [ ] Webhook handlers (Stripe, Telegram, WhatsApp)
- [ ] Background jobs (cron endpoints)
- [ ] CoScript generation & storage
- [ ] CoDeliver asset workflow (upload, comment, approve, share)
- [ ] Error cases (missing fields, bad auth, not found)
- [ ] Rate limiting (if implemented)

---

**Total Endpoints**: 116
**Last Updated**: 2026-02-28
**Contact**: Engineering Team
