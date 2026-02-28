# Authentication & Authorization Matrix
## ACS Ecosystem — Generated 2026-02-28

---

## Overview

The ACS ecosystem implements a **multi-tier JWT-based authentication system** with role-based access control (RBAC). All authentication is handled through Supabase and secured with bcrypt password hashing. JWTs are signed with a shared secret and expire after 7 days.

---

## Auth Tiers & Roles

### Tier 1: Root Auth (_rootAuth.js)
**Purpose:** Inter-repo authentication for external systems (e.g., Content Co-op) to call protected ACS endpoints.

**Configuration:**
- Uses `JWT_SECRET` or `JWT_CLIENT_AUTH_SECRET` environment variable
- Falls back to `CLIENT_AUTH_JWT_SECRET` for compatibility
- Must be set consistently across all repos

**How It Works:**
- External repos use the same JWT_SECRET to sign tokens
- Provides a higher-order function wrapper: `requireAdmin(handler)`
- Wraps entire Netlify handlers and returns 401 if auth fails
- Passes decoded JWT claims to the wrapped handler as third parameter

**Token Claims:**
```json
{
  "admin_id": "uuid",
  "contact_id": "uuid",
  "business_id": "uuid",
  "role": "admin"
}
```

**Usage Pattern:**
```javascript
const { requireAdmin } = require('./_rootAuth');

exports.handler = requireAdmin(async (event, context, claims) => {
  // claims = decoded JWT payload
  return { statusCode: 200, body: JSON.stringify({ ok: true }) };
});
```

**Error Handling:**
- Returns 401 with `{ error: 'unauthorized' }` for auth failures
- Returns 500 with `{ error: 'auth_configuration_error' }` for misconfiguration
- Recognizes JWT errors: `JsonWebTokenError`, `TokenExpiredError`, `NotBeforeError`

**Endpoints Using _rootAuth:**
- `rootBridge` (reference implementation)

---

### Tier 2: Admin Auth (_adminAuth.js)
**Purpose:** Administrative access to business operations, dashboard, and crew/client management.

**Dependency:** Built on top of `_clientAuth.js`

**Exported Functions:**
- `requireAdmin(event)` — Extracts Bearer token, verifies it, asserts role='admin'
- `verifyAdminToken(token)` — Verifies token and asserts role='admin'
- `signAuthToken(payload)` — Signs JWT with 7-day expiration
- `extractBearerToken(headers)` — Extracts token from Authorization header

**Token Claims:**
```json
{
  "admin_id": "uuid",
  "contact_id": "uuid",
  "business_id": "uuid",
  "role": "admin"
}
```

**Token Expiration:** 7 days (hard-coded in `signAuthToken`)

**Password Storage:** bcrypt with salt rounds (default 10)

**Login Endpoints:**

#### `/adminLogin` — POST
```json
Request:  { phoneOrEmail: "...", pin: "..." }
Response: { success: true, token, admin_id, role }
```
- PIN-based authentication (4-6 digits)
- Normalizes phone numbers to 10 digits, emails to lowercase
- Looks up contact by phone or email
- Verifies PIN hash against `admin_auth.pin_hash`
- Returns JWT with admin claims

#### `/authLogin` — POST (Unified)
```json
Request:  { type: "admin", email: "...", password: "..." }
Response: { success: true, token, admin_id, role }
```
- Email + password authentication
- Supports `type: 'admin'` for admins
- Returns same JWT structure as PIN login

**Database Tables Involved:**
- `contacts` — name, phone, email
- `admin_auth` — password_hash, pin_hash, email_verified, role, business_id
- `businesses` — id, name

**Admin Endpoints (requireAdmin):**

| Endpoint | Methods | Purpose |
|----------|---------|---------|
| `/adminDashboard` | GET | Fetch dashboard metrics (revenue, jobs, etc.) |
| `/adminClients` | GET, POST, PATCH | List/search/create/update clients; log interactions |
| `/adminCrew` | GET, POST, PUT | List crew; create crew; assign roles |
| `/adminJobs` | GET, POST, PUT | List jobs by date range/status; create jobs; update |
| `/adminQuotes` | GET, POST, PUT | List quotes; create quotes; update status |
| `/adminRequests` | GET, PUT | List client requests; update request status |
| `/adminTasks` | GET, PUT | List tasks; update task status |
| `/adminInvoices` | GET, POST | List invoices; generate invoice; send/mark paid |
| `/adminTaxProfile` | GET, POST | Get/set tax profile info |
| `/adminSettings` | GET, PUT | Get/update admin profile & business info |
| `/adminApplicants` | GET, PATCH | List job applicants; hire applicants |
| `/adminCalendarEvents` | GET | Pull events from Google Calendar |
| `/adminFinance` | GET | Finance dashboard (revenue, costs, margins) |
| `/adminLiveLocations` | GET, POST | Live crew locations and job sites; manual overrides |
| `/adminNotificationLog` | GET | Notification delivery history |
| `/adminProvisionClientAccount` | POST | Set PIN for existing client contact |
| `/adminProvisionCrewPin` | POST | Set PIN for existing crew member |
| `/geocodeJobAddresses` | POST | Geocode job addresses (admin-triggered) |
| `/routeOptimize` | POST | Optimize crew routes (admin-triggered) |
| `/scheduleAudit` | POST | Audit crew schedules |
| `/scheduleTracker` | POST | Track schedule changes |

---

### Tier 3: Client Auth (_clientAuth.js)
**Purpose:** Customer/client authentication for booking, job tracking, quotes, and payments.

**Functions Exported:**
- `verifyAuthToken(token)` — Verify JWT validity
- `extractBearerToken(headers)` — Extract Bearer token
- `signAuthToken(payload)` — Sign JWT (7-day expiration)
- `normalizePhone(phone)` — Normalize to last 10 digits
- `normalizeEmail(email)` — Lowercase and trim
- `findContactByIdentifier(supabase, {phone, email})` — Find contact record
- `upsertContact(supabase, existing, {name, phone, email})` — Create/update contact
- `ensureClientProfile(supabase, {contactId, businessId})` — Ensure profile exists
- `createSupabaseAdmin()` — Create Supabase client with service key
- `queueEmail(supabase, {to, subject, html})` — Queue transactional email via events table

**Token Claims:**
```json
{
  "client_profile_id": "uuid",
  "contact_id": "uuid",
  "business_id": "uuid"
}
```

**Token Expiration:** 7 days

**Password Storage:** bcrypt with salt rounds (default 10)

**Login Endpoints:**

#### `/loginPin` — POST
```json
Request:  { phoneOrEmail: "...", pin: "..." }
Response: { success: true, token, client_profile_id, contact_id, expires_in_days: 7 }
```
- PIN-based authentication (flexible digits)
- Looks up client_profiles by contact + business
- Verifies against `client_auth.pin_hash`
- Returns JWT with client claims

#### `/setPin` — POST
```json
Request:  { phone?: "...", email?: "...", name?: "...", pin: "..." }
Response: { success: true, message: "PIN set successfully. Please log in." }
```
- PIN setup for new/existing clients
- Creates or updates contact, client_profile, and client_auth records
- PIN must be 4-6 digits
- Automatically links to 'Astro Cleanings' business

#### `/authLogin` — POST (Unified)
```json
Request:  { type: "client", email: "...", password: "..." }
Response: { success: true, token, client_profile_id, contact_id }
```
- Email + password authentication
- Supports `type: 'client'` for clients
- Returns JWT with client claims

**Database Tables Involved:**
- `contacts` — id, name, phone, email
- `client_profiles` — id, contact_id, business_id, status, created_at
- `client_auth` — client_profile_id, password_hash, pin_hash, email_verified, last_login_at
- `contact_business_map` — contact_id, business_id (mapping)

**Client Endpoints (verifyAuthToken):**

| Endpoint | Methods | Purpose |
|----------|---------|---------|
| `/getPortalData` | GET | Fetch client portal data (contact, next job, quotes, payments) |
| `/createDepositCheckout` | POST | Create Stripe checkout for deposit |
| `/createRequest` | POST | Create client request (service request) |
| `/generateReferralCode` | POST | Generate referral code for client |

---

### Tier 4: Crew Auth
**Purpose:** Crew member authentication for job tracking, location reporting, and schedule access.

**Implementation:** Custom `requireCrew()` function in endpoint files (not in shared module)

**Verification Pattern:**
```javascript
function requireCrew(event) {
  const token = extractBearerToken(event.headers || {});
  if (!token) throw new Error('missing_token');
  const claims = verifyAuthToken(token);
  if (claims.role !== 'crew') throw new Error('not_crew');
  return claims;
}
```

**Token Claims:**
```json
{
  "crew_member_id": "uuid",
  "business_id": "uuid",
  "role": "crew"
}
```

**Token Expiration:** 7 days

**Password Storage:** bcrypt with salt rounds (default 10)

**Login Endpoints:**

#### `/crewLogin` — POST
```json
Request:  { phone: "...", pin: "..." }
Response: { success: true, token, crew_member_id, name, role }
```
- PIN-based authentication (flexible digits)
- Looks up crew_members by phone + business
- Verifies against `crew_auth.pin_hash`
- Requires crew member status = 'active'
- Returns JWT with crew claims (including role='crew')

#### `/authLogin` — POST (Unified)
```json
Request:  { type: "crew", email: "...", password: "..." }
Response: { success: true, token, crew_member_id, name, role, color }
```
- Email + password authentication
- Supports `type: 'crew'` for crew members
- Requires email_verified = true
- Returns JWT with crew claims

**Database Tables Involved:**
- `crew_members` — id, name, role, phone, status, business_id, color
- `crew_auth` — crew_member_id, password_hash, pin_hash, email_verified, last_login_at
- `job_crew_assignments` — job_id, crew_member_id

**Crew Endpoints (verifyAuthToken + role check):**

| Endpoint | Methods | Purpose |
|----------|---------|---------|
| `/crewPortalData` | GET | Fetch crew schedule (today's jobs, next 7 days) |
| `/crewReportLocation` | POST | Report GPS location; trigger geofence departure detection |
| `/crewUpdateJobStatus` | POST | Update job status (in_progress, completed, etc.) |

---

### Tier 5: Public (No Auth)
**Purpose:** Public-facing endpoints accessible without authentication.

**Public Login Endpoints:**
- `/authLogin` (POST) — Unified login for any role
- `/adminLogin` (POST) — Admin PIN login
- `/crewLogin` (POST) — Crew PIN login
- `/loginPin` (POST) — Client PIN login
- `/loginGoogle` — Google OAuth login (no API yet)
- `/authSignup` (POST) — Email/password signup
- `/authVerifyEmail` (POST) — Verify email with code
- `/authForgotPassword` (POST) — Request password reset
- `/authResetPassword` (POST) — Reset password with token

**Public Portal Endpoints:**
- `/bookSlot` (POST) — Book a cleaning slot
- `/getAvailableSlots` (GET) — Fetch available booking slots
- `/submitQuote` (POST) — Submit quote request (lead generation)
- `/validatePromo` (GET) — Validate promo code
- `/validateReferral` (GET) — Validate referral code
- `/generateReferralCode` (POST) — (requires client auth)

**Public Webhooks & Callbacks:**
- `/stripeWebhook` (POST) — Stripe webhook handler
- `/whatsappWebhook` (POST) — WhatsApp message webhook
- `/telegramWebhook` (POST) — Telegram webhook
- `/confirmAppointment` (POST) — Appointment confirmation callback
- `/submitFeedback` (POST) — Feedback submission
- `/submitReview` (POST) — Review submission

**Public Content & Data:**
- `/propertyLookup` (GET) — Lookup property info
- `/trackingData` (GET) — Tracking/analytics data
- `/getTestimonials` (GET) — Public testimonials
- `/submitApplication` (POST) — Job application (crew recruiting)
- `/validateReferral` (GET) — Validate referral

**Public Background Jobs:**
- `/morningBrief` (POST) — Morning digest email
- `/eveningDigest` (POST) — Evening digest email
- `/sendJobReminders` (POST) — Job reminder emails
- `/sendInvoice` (POST) — Send invoice email
- `/sendReceipt` (POST) — Send receipt email
- `/invoiceReminder` (POST) — Invoice reminder email
- `/leadNurture` (POST) — Lead nurture email
- `/sendFeedbackRequest` (POST) — Request feedback email
- `/staleLeadCleanup` (POST) — Clean up stale leads
- `/contentCoopMorningBrief` (POST) — Content Co-op morning brief

**Public Internal Processing:**
- `/processEvents` (POST) — Process queued events
- `/upsertJobFromIntake` (POST) — Create job from intake form
- `/crewUpdateJobStatus` (POST) — (may be crew-authed)
- `/requestReview` (POST) — Internal review request
- `/revenueTracker` (POST) — Revenue tracking
- `/reviewAnalytics` (POST) — Analytics review
- `/createPublicDepositCheckout` (POST) — Deposit checkout (no auth)

---

## Endpoint → Auth Mapping

### Admin Endpoints (All require `requireAdmin`)

| Endpoint | Method | Auth Required | Response | Notes |
|----------|--------|---------------|----------|-------|
| /adminDashboard | GET | Admin | Dashboard metrics | Revenue, jobs, crew stats |
| /adminClients | GET/POST/PATCH | Admin | Client list/detail/create | Search by q=term, pagination |
| /adminCrew | GET/POST/PUT | Admin | Crew list/create/update | Includes assignments |
| /adminJobs | GET/POST/PUT | Admin | Job list/create/update | Filter by date/status |
| /adminQuotes | GET/POST/PUT | Admin | Quote list/create/update | Pricing, status tracking |
| /adminRequests | GET/PUT | Admin | Request list/update | Client service requests |
| /adminTasks | GET/PUT | Admin | Task list/update | Admin task tracking |
| /adminInvoices | GET/POST | Admin | Invoice list/generate | Send/mark paid actions |
| /adminTaxProfile | GET/POST | Admin | Tax profile data | 1099 and tax info |
| /adminSettings | GET/PUT | Admin | Admin profile + biz info | Business configuration |
| /adminApplicants | GET/PATCH | Admin | Job applicants | Hire action |
| /adminCalendarEvents | GET | Admin | Google Calendar events | External integration |
| /adminFinance | GET | Admin | Finance metrics | Revenue, margins, costs |
| /adminLiveLocations | GET/POST | Admin | Crew locations + sites | Live map data |
| /adminNotificationLog | GET | Admin | Notification delivery log | Audit trail |
| /adminProvisionClientAccount | POST | Admin | Set client PIN | Create auth for contact |
| /adminProvisionCrewPin | POST | Admin | Set crew PIN | Create auth for crew member |
| /geocodeJobAddresses | POST | Admin | Geocoded addresses | Map integration |
| /routeOptimize | POST | Admin | Optimized routes | Route planning |
| /scheduleAudit | POST | Admin | Schedule audit results | Verify schedule data |
| /scheduleTracker | POST | Admin | Schedule changes | Audit trail |

### Client Endpoints (All require `verifyAuthToken` + role check)

| Endpoint | Method | Auth Required | Response | Notes |
|----------|--------|---------------|----------|-------|
| /getPortalData | GET | Client | Portal data object | Contact, next job, quotes, payments |
| /createDepositCheckout | POST | Client | Stripe session | Create checkout |
| /createRequest | POST | Client | Request created | Service request |
| /generateReferralCode | POST | Client | Referral code | Unique code + tracking |

### Crew Endpoints (All require `verifyAuthToken` + role='crew')

| Endpoint | Method | Auth Required | Response | Notes |
|----------|--------|---------------|----------|-------|
| /crewPortalData | GET | Crew | Schedule data | Today + 7-day jobs |
| /crewReportLocation | POST | Crew | Location stored | GPS ping, geofence check |
| /crewUpdateJobStatus | POST | Crew | Status updated | in_progress, completed, etc. |

### Public Endpoints (No Auth Required)

| Endpoint | Method | Auth | Purpose | Notes |
|----------|--------|------|---------|-------|
| /authLogin | POST | None | Unified login | type: 'admin'\|'client'\|'crew' |
| /adminLogin | POST | None | Admin PIN login | Legacy, PIN-based |
| /crewLogin | POST | None | Crew PIN login | Phone + PIN |
| /loginPin | POST | None | Client PIN login | Phone/email + PIN |
| /loginGoogle | GET/POST | None | Google OAuth | Integration pending |
| /authSignup | POST | None | Email signup | Create new account |
| /authVerifyEmail | POST | None | Email verification | Confirm email + code |
| /authForgotPassword | POST | None | Password reset | Request code |
| /authResetPassword | POST | None | Reset password | Apply reset code |
| /bookSlot | POST | None | Book service | Lead capture |
| /getAvailableSlots | GET | None | Fetch slots | Calendar availability |
| /submitQuote | POST | None | Request quote | Lead generation |
| /validatePromo | GET | None | Check promo code | Discount validation |
| /validateReferral | GET | None | Check referral | Referral validation |
| /createPublicDepositCheckout | POST | None | Public checkout | No auth variant |
| /propertyLookup | GET | None | Property data | Zillow/tax records |
| /trackingData | GET | None | Tracking pixel | Analytics |
| /getTestimonials | GET | None | Public testimonials | Display reviews |
| /submitApplication | POST | None | Job application | Crew recruiting |
| /stripeWebhook | POST | None | Stripe events | Payment processing |
| /whatsappWebhook | POST | None | WhatsApp messages | Chat integration |
| /telegramWebhook | POST | None | Telegram messages | Chat integration |
| /confirmAppointment | POST | None | Confirm booking | Customer action |
| /submitFeedback | POST | None | Feedback | Customer feedback |
| /submitReview | POST | None | Review | Customer review |
| /requestReview | POST | None | Request review | Internal |
| /morningBrief | POST | None | Morning email | Background job |
| /eveningDigest | POST | None | Evening email | Background job |
| /sendJobReminders | POST | None | Reminder email | Background job |
| /sendInvoice | POST | None | Invoice email | Background job |
| /sendReceipt | POST | None | Receipt email | Background job |
| /invoiceReminder | POST | None | Payment reminder | Background job |
| /leadNurture | POST | None | Nurture email | Background job |
| /sendFeedbackRequest | POST | None | Feedback request | Background job |
| /processEvents | POST | None | Event processing | Background job |
| /upsertJobFromIntake | POST | None | Job creation | Background job |
| /contentCoopMorningBrief | POST | None | Content Co-op brief | External integration |

---

## JWT Token Schemas

### Admin JWT
**Signed by:** `signAuthToken(payload)` from _clientAuth.js
**Secret:** `JWT_SECRET` or `CLIENT_AUTH_JWT_SECRET`
**Expiration:** 7 days

```json
{
  "admin_id": "550e8400-e29b-41d4-a716-446655440000",
  "contact_id": "550e8400-e29b-41d4-a716-446655440001",
  "business_id": "550e8400-e29b-41d4-a716-446655440002",
  "role": "admin",
  "iat": 1709107200,
  "exp": 1709712000
}
```

**Issued By:** `/adminLogin` or `/authLogin?type=admin`
**Verified By:** `verifyAdminToken(token)` from _adminAuth.js
**Usage:** Bearer token in Authorization header

### Client JWT
**Signed by:** `signAuthToken(payload)` from _clientAuth.js
**Secret:** `JWT_SECRET` or `CLIENT_AUTH_JWT_SECRET`
**Expiration:** 7 days

```json
{
  "client_profile_id": "550e8400-e29b-41d4-a716-446655440010",
  "contact_id": "550e8400-e29b-41d4-a716-446655440011",
  "business_id": "550e8400-e29b-41d4-a716-446655440002",
  "iat": 1709107200,
  "exp": 1709712000
}
```

**Issued By:** `/loginPin` or `/authLogin?type=client`
**Verified By:** `verifyAuthToken(token)` from _clientAuth.js
**Usage:** Bearer token in Authorization header
**Note:** Does NOT include `role` claim; role is implicit

### Crew JWT
**Signed by:** `signAuthToken(payload)` from _clientAuth.js
**Secret:** `JWT_SECRET` or `CLIENT_AUTH_JWT_SECRET`
**Expiration:** 7 days

```json
{
  "crew_member_id": "550e8400-e29b-41d4-a716-446655440020",
  "business_id": "550e8400-e29b-41d4-a716-446655440002",
  "role": "crew",
  "iat": 1709107200,
  "exp": 1709712000
}
```

**Issued By:** `/crewLogin` or `/authLogin?type=crew`
**Verified By:** `verifyAuthToken(token)` + custom role check
**Usage:** Bearer token in Authorization header

### Root/Inter-Repo JWT
**Signed by:** External repo using shared `JWT_SECRET`
**Secret:** `JWT_SECRET` or `CLIENT_AUTH_JWT_SECRET`
**Expiration:** Determined by issuer
**Format:** Same as Admin JWT (role='admin' required)

```json
{
  "admin_id": "550e8400-e29b-41d4-a716-446655440030",
  "contact_id": "550e8400-e29b-41d4-a716-446655440031",
  "business_id": "550e8400-e29b-41d4-a716-446655440002",
  "role": "admin",
  "iat": 1709107200,
  "exp": 1709712000
}
```

**Usage:** Bearer token in Authorization header
**Verified By:** `verifyAdminToken(event)` from _rootAuth.js (wrapper pattern)

---

## Bearer Token Format

All protected endpoints expect tokens in the HTTP Authorization header:

```
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

**Token Extraction:**
```javascript
const auth = event.headers.authorization || event.headers.Authorization || '';
const token = auth.startsWith('Bearer ') ? auth.slice(7).trim() : null;
```

---

## Password & PIN Hashing

**Algorithm:** bcrypt with salt rounds = 10
**Library:** bcryptjs

**Password Verification:**
```javascript
const bcrypt = require('bcryptjs');
const valid = await bcrypt.compare(passwordPlaintext, storedHash);
```

**PIN Requirements:**
- Admin PINs: 4-6 digits
- Client PINs: 4-6 digits
- Crew PINs: Flexible (no digit constraint documented)

---

## Cross-Repo Auth Pattern (coscript & codeliver)

Both **coscript** and **codeliver** use Supabase SSR integration:

**Library:** `@supabase/ssr`

```typescript
// lib/supabase-auth.ts
export async function createSupabaseAuth() {
  const cookieStore = await cookies();
  return createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
    {
      cookies: {
        getAll() { return cookieStore.getAll(); },
        setAll(cookiesToSet) {
          cookiesToSet.forEach(({ name, value, options }) => {
            cookieStore.set(name, value, options);
          });
        }
      }
    }
  );
}
```

**Usage:**
```typescript
export async function requireAuth() {
  const supabase = await createSupabaseAuth();
  const { data: { user } } = await supabase.auth.getUser();
  return user || null;
}
```

**Note:** These use Supabase's native authentication, NOT the ACS JWT system.

---

## Security Recommendations

### Current Strengths
1. **JWT Expiration:** 7-day expiration reduces token lifetime risk
2. **Password Hashing:** bcrypt with salt rounds = 10 is industry standard
3. **Bearer Token Pattern:** Standard OAuth 2.0 convention
4. **Role-Based Access Control:** Clear separation of admin/client/crew roles
5. **Header Extraction:** Case-insensitive Authorization header parsing
6. **Unified Auth:** Single `/authLogin` endpoint for all roles reduces endpoint sprawl

### Identified Gaps & Concerns

1. **Shared JWT Secret Across Repos**
   - **Issue:** If one repo (coscript, codeliver) is compromised, all ACS endpoints are exposed
   - **Recommendation:** Use separate JWT secrets per repo, or implement key rotation

2. **No Token Revocation Mechanism**
   - **Issue:** Compromised tokens cannot be revoked before 7-day expiration
   - **Recommendation:** Implement a blocklist (Redis/DB) for revoked tokens
   - **Recommendation:** Add token version/nonce to enable quick invalidation

3. **Missing Email Verification Enforcement**
   - **Issue:** Some endpoints check `email_verified`, others don't
   - Endpoints `/authLogin` checks it for crew; admin login doesn't require it
   - **Recommendation:** Consistently enforce email verification across all login types

4. **PIN Digits Constraint Inconsistent**
   - **Issue:** Admin/client PINs: 4-6 digits; crew PINs: no constraint
   - **Recommendation:** Standardize to 4-6 digits for all PINs

5. **No Rate Limiting on Auth Endpoints**
   - **Issue:** Brute force attacks on PIN/password endpoints possible
   - **Recommendation:** Implement rate limiting (e.g., 5 failed attempts → 15-minute lockout)

6. **Bearer Token in URL or Query Parameters**
   - **Issue:** Some endpoints may accept tokens via query params (not confirmed)
   - **Recommendation:** Enforce header-only token submission; never in URL/params

7. **No Request Signing/HMAC Verification**
   - **Issue:** POST requests with sensitive data aren't cryptographically signed
   - **Recommendation:** For cross-repo calls, implement HMAC-SHA256 request signing

8. **Missing CORS & CSRF Protection**
   - **Issue:** No documented CORS or CSRF token verification
   - **Recommendation:** Implement proper CORS headers and CSRF tokens for state-changing operations

9. **Plaintext Password Transmission**
   - **Issue:** Passwords sent over HTTP (assumed HTTPS in production)
   - **Recommendation:** Ensure all endpoints use HTTPS; consider mTLS for inter-repo calls

10. **No Audit Logging of Auth Events**
    - **Issue:** Failed login attempts, token issuance, role changes not logged
    - **Recommendation:** Log all auth events (failed logins, successful logins, token refresh, role changes)

11. **Admin Token Includes Contact & Business ID**
    - **Issue:** Unnecessary claim inflation could expose data
    - **Recommendation:** Include only essential claims; fetch business context as needed

12. **Default to First Business Found**
    - **Issue:** Hard-coded 'Astro Cleanings' lookup in auth flows
    - **Recommendation:** Allow business_id to be passed in login request

---

## Environment Variables Required

### ACS Website (acs-website)
```bash
SUPABASE_URL                 # Supabase project URL
SUPABASE_SERVICE_KEY         # Supabase service role key
CLIENT_AUTH_JWT_SECRET       # JWT signing secret (or use JWT_SECRET)
JWT_SECRET                   # Fallback JWT secret (if above not set)
```

### Coscript
```bash
NEXT_PUBLIC_SUPABASE_URL     # Supabase project URL
NEXT_PUBLIC_SUPABASE_ANON_KEY # Supabase anon key (client-safe)
```

### Codeliver
```bash
NEXT_PUBLIC_SUPABASE_URL     # Supabase project URL
NEXT_PUBLIC_SUPABASE_ANON_KEY # Supabase anon key (client-safe)
```

**Critical:** `JWT_SECRET` and `CLIENT_AUTH_JWT_SECRET` must be identical across all repos for inter-repo token verification.

---

## Testing & Validation

### Sample Login Flow (Admin via PIN)
```bash
curl -X POST https://astrocleanings.netlify.app/.netlify/functions/adminLogin \
  -H "Content-Type: application/json" \
  -d '{"phoneOrEmail": "555-0123", "pin": "1234"}'

# Response:
# {
#   "success": true,
#   "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
#   "admin_id": "uuid",
#   "role": "admin"
# }
```

### Using Token to Access Protected Endpoint
```bash
curl -X GET https://astrocleanings.netlify.app/.netlify/functions/adminDashboard \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."

# Response: { ... dashboard data ... }
```

### Testing Token Expiration
Modify JWT payload to set `exp` to a past timestamp:
```javascript
const jwt = require('jsonwebtoken');
const expired = jwt.sign(payload, secret, { expiresIn: '-1h' });
// Attempts to verify this token will throw TokenExpiredError
```

---

## Migration Guides

### Adding New Protected Endpoint (Admin)
1. Create function file: `/netlify/functions/myEndpoint.js`
2. Import: `const { requireAdmin, createSupabaseAdmin, json } = require('./_adminAuth');`
3. Call `requireAdmin(event)` at start of handler
4. Return 401 if auth fails, 200 with data on success

### Adding New Protected Endpoint (Crew)
1. Create function file: `/netlify/functions/myCrewEndpoint.js`
2. Import auth utils: `const { verifyAuthToken, extractBearerToken, json } = require('./_clientAuth');`
3. Implement custom `requireCrew()` check (or extract to shared module)
4. Verify `claims.role === 'crew'` before proceeding

### Migrating from PIN to Password Auth
1. Update login endpoint to accept both PIN and password
2. Support backward compatibility: if `pin` provided, verify pin; else verify password
3. Return same JWT structure regardless of auth method
4. Eventually deprecate PIN endpoint

### Cross-Repo Integration with Root Auth
1. Copy `_rootAuth.js` to your repo's `netlify/functions/`
2. Ensure `JWT_SECRET` env var is set identically in both Netlify dashboards
3. Wrap your handler: `exports.handler = requireAdmin(myHandler);`
4. Receive decoded claims as third parameter

---

## Database Schema Overview

### contacts
```
id (uuid, PK)
name (text)
phone (text, normalized)
email (text, lowercase)
created_at (timestamp)
updated_at (timestamp)
```

### admin_auth
```
id (uuid, PK)
contact_id (uuid, FK → contacts.id)
business_id (uuid, FK → businesses.id)
role (text, default: 'admin')
password_hash (text, bcrypt)
pin_hash (text, bcrypt, nullable)
email_verified (boolean, default: false)
last_login_at (timestamp, nullable)
created_at (timestamp)
updated_at (timestamp)
```

### client_profiles
```
id (uuid, PK)
contact_id (uuid, FK → contacts.id)
business_id (uuid, FK → businesses.id)
status (text, default: 'active')
created_at (timestamp)
updated_at (timestamp)
```

### client_auth
```
client_profile_id (uuid, FK → client_profiles.id)
password_hash (text, bcrypt, nullable)
pin_hash (text, bcrypt, nullable)
email_verified (boolean, default: false)
last_login_at (timestamp, nullable)
pin_set_at (timestamp, nullable)
created_at (timestamp)
updated_at (timestamp)
```

### crew_members
```
id (uuid, PK)
name (text)
phone (text, normalized)
role (text)
status (text, default: 'active')
color (text, for UI)
business_id (uuid, FK → businesses.id)
created_at (timestamp)
updated_at (timestamp)
```

### crew_auth
```
crew_member_id (uuid, FK → crew_members.id)
password_hash (text, bcrypt, nullable)
pin_hash (text, bcrypt)
email_verified (boolean, default: false)
last_login_at (timestamp, nullable)
created_at (timestamp)
updated_at (timestamp)
```

### businesses
```
id (uuid, PK)
name (text, unique)
phone (text)
email (text)
address (text)
created_at (timestamp)
updated_at (timestamp)
```

---

## Summary Table

| Tier | Role | Login Endpoint | Token Lifetime | Key Claims | Typical Use |
|------|------|----------------|----------------|------------|------------|
| 1 | Root/Admin | External repo | Per issuer | admin_id, role='admin' | Inter-repo API calls |
| 2 | Admin | /adminLogin, /authLogin | 7 days | admin_id, role='admin' | Dashboard, crew/client mgmt |
| 3 | Client | /loginPin, /authLogin | 7 days | client_profile_id | Booking, job tracking, quotes |
| 4 | Crew | /crewLogin, /authLogin | 7 days | crew_member_id, role='crew' | Schedule, location, job updates |
| 5 | Public | N/A | N/A | N/A | Booking, quotes, webhooks |

---

**Document Generated:** 2026-02-28
**Last Updated:** 2026-02-28
**Version:** 1.0
