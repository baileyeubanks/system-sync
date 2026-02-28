# SYSTEM_CONTEXT.md — ACS + Content Co-op Ecosystem

> Single source of truth for the entire multi-repo, multi-platform system.
> Updated: 2026-02-27

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                         CLIENTS / USERS                              │
│  Telegram  │  WhatsApp  │  iMessage  │  Web Portal  │  Admin Panel  │
└─────┬──────┴─────┬──────┴─────┬──────┴──────┬───────┴──────┬────────┘
      │            │            │             │              │
      ▼            ▼            │             ▼              ▼
┌─────────────────────────┐    │    ┌────────────────────────────────┐
│   Netlify Functions     │    │    │   Netlify Functions (Admin)    │
│   (acs-website)         │    │    │   adminDashboard, adminJobs,   │
│   telegramWebhook.js    │    │    │   adminClients, adminCrew...   │
│   whatsappWebhook.js    │    │    └────────────┬───────────────────┘
│   submitQuote.js        │    │                 │
│   bookSlot.js           │    │                 │
│   _conversationAgent.js │    │                 │
└─────────┬───────────────┘    │                 │
          │                    │                 │
          ▼                    │                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     SUPABASE (PostgreSQL)                            │
│   Project: briokwdoonawhxisbydy                                      │
│                                                                      │
│   contacts │ jobs │ quotes │ events │ conversations │ crew_members   │
│   invoices │ payments │ feedback │ reviews │ notification_log        │
│   creative_briefs │ review_assets │ script_jobs │ projects │ ...     │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
          ┌────────────────┼────────────────┐
          │                │                │
          ▼                ▼                ▼
┌─────────────┐  ┌─────────────┐  ┌─────────────────────────────────┐
│  CoDeliver  │  │  CoScript   │  │  Mac Mini M4 (Blaze V4)         │
│  (Netlify)  │  │  (Netlify)  │  │  OpenClaw Gateway (18789)       │
│  Next.js    │  │  Next.js    │  │  FastAPI (8899)                  │
│             │  │             │  │  iMessage relay                  │
│  review_    │  │  script_    │  │  Gmail monitor                   │
│  assets,    │  │  jobs,      │  │  YouTube learning engine         │
│  approval_  │  │  variants,  │  │  Netlify event bridge            │
│  gates...   │  │  watchlists │  │  26 LaunchAgents                 │
└─────────────┘  └─────────────┘  └─────────────────────────────────┘
```

## Repositories

| Repo | Domain | Platform | Framework | Purpose |
|------|--------|----------|-----------|---------|
| **acs-website** | astrocleanings.com | Netlify | Static + Netlify Functions | ACS business: booking, CRM, admin, crew, billing, conversational AI |
| **contentco-op-website** | contentco-op.com | Vercel | Next.js (Turborepo) | Content Co-op: briefs portal, media processing, onboarding |
| **codeliver** | codeliver.cc | Netlify | Next.js | Video/asset review platform with approval gates |
| **coscript** | coscript.cc | Netlify | Next.js | AI script generation, outlier detection, watchlists |
| **coedit** | coedit.cc | Netlify | Vite + React | Browser-based video editor (client-side SPA) |
| **portfolio** | baileyeubanks.com | Static | HTML | Bailey's personal portfolio |
| **system-sync** | — | GitHub | Scripts | This repo — coordination layer, schema registry, health checks |

## Supabase

**Single shared project** across all repos: `briokwdoonawhxisbydy`

Tables are namespaced by convention:
- **ACS core**: contacts, jobs, quotes, events, crew_members, invoices, payments, feedback, reviews
- **Conversations**: conversations, faq_responses
- **Tracking**: route_history, job_locations, job_tracking_tokens, job_eta_overrides
- **Auth**: admin_auth, crew_auth, client_auth (not Supabase Auth — custom JWT)
- **Content Co-op**: creative_briefs, brief_files, brief_messages, brief_status_history
- **CoDeliver**: review_assets, asset_versions, approval_gates, approval_decisions, timecoded_comments, review_events
- **CoScript**: script_jobs, script_variants, script_fixes, watchlists, outlier_scores, vault_items, drafts, share_links, briefs
- **CoEdit**: projects, co_edit_documents, co_edit_document_versions, orgs, org_members
- **Billing**: org_plan_subscriptions, plan_limits, usage_ledger, coedit_usage_current_period
- **Tax**: tax_profiles, tax_deductions, tax_deduction_categories, tax_estimates, tax_rules
- **Growth**: referral_codes, referral_uses, lead_nurture_log, revenue_snapshots

See `schema/master-schema.sql` for complete table inventory with columns and ownership.

## Mac Mini (Blaze V4)

The Mac Mini M4 is the orchestration brain:

- **SSH**: `ssh blaze-master` (10.0.0.21)
- **OpenClaw Gateway**: Port 18789 — manages AI agents
- **FastAPI**: Port 8899 — internal API for iMessage, WhatsApp, health checks
- **Agents**: main (Blaze), acs-worker (Agent Astro), cc-worker (Creative Director), research-worker
- **Model**: claude-sonnet-4-6 (primary), gpt-4.1 + gemini fallbacks

### Key Services
| Service | Method | Schedule |
|---------|--------|----------|
| Netlify Event Bridge | Poll events table | Every 60s |
| iMessage Relay | File queue + LaunchAgent | Continuous |
| iMessage Watcher | SSH loopback + SQLite | Every 3s |
| Gmail Monitor | IMAP poll | Every 5 min |
| Morning Briefing | LaunchAgent | 6:30 AM / 7:00 AM |
| YouTube Learning | RSS + Ollama | Every 10 min |

## Communication Channels

| Channel | Provider | Direction | Handler |
|---------|----------|-----------|---------|
| Telegram | Bot API (@agentastro_bot) | Inbound + Outbound | telegramWebhook.js + OpenClaw |
| WhatsApp | Meta Cloud API (+13464015841) | Inbound + Outbound | whatsappWebhook.js + FastAPI |
| iMessage | macOS Messages.app | Inbound + Outbound | imsg_watcher.py + imsg_relay.py |
| Email | Google Workspace (caio@) | Inbound monitor | gmail_monitor.py |
| SMS | — | REMOVED | — |

## Authentication Methods

| System | Method | Token Duration |
|--------|--------|----------------|
| Admin Panel | JWT (adminLogin.js) | 7 days |
| Client Portal | JWT (authLogin.js) | 7 days |
| Crew Portal | PIN + JWT (crewLogin.js) | 7 days |
| Confirmation Links | HMAC-SHA256 signed URLs | No expiry |
| Tracking Links | UUID tokens (job_tracking_tokens) | Configurable |
| CoEdit V1 | JWT (COEDIT_JWT_SECRET) | 7 days |
| Internal API | API keys (OPS_INTAKE_KEY, CO_EDIT_KEY) | Permanent |
| Stripe | Webhook signature | Per-request |

## Notification System (_notify.js)

Multi-channel with fallback chain:
1. **iMessage** (primary — via events table → Mac Mini relay)
2. **WhatsApp** (via Meta Cloud API)
3. **Email** (via events table → Mac Mini gmail)

Note: Telegram group (@ACS_CC_TEAM) is used as a backup/condensed channel, NOT the primary delivery method. Individual crew and admin messages go via iMessage.

All notifications logged to `notification_log` table.

Team notifications go to Telegram group @ACS_CC_TEAM (chat_id: -1003808234745).

## Conversational AI (_conversationAgent.js)

Intent classification via regex patterns:
- **BOOK**: schedule, appointment, book, cleaning
- **STATUS**: status, where, tracking, appointment
- **RESCHEDULE**: reschedule, change date/time, move
- **CANCEL**: cancel, stop, don't want
- **FAQ**: keyword scoring against faq_responses table (20 entries)
- **HUMAN**: human, agent, manager, speak to someone

Auto-escalation triggers:
- Negative sentiment keywords (angry, terrible, lawsuit, etc.)
- 2+ consecutive unclassified messages

Conversation state tracked in `conversations` table (messages_json JSONB).

## Key Data Flows

1. **Quote → Job Pipeline**: submitQuote → events → Mac Mini → Telegram to Caio → adminJobs → bookSlot → confirmAppointment
2. **Telegram Chat**: Message → telegramWebhook → _conversationAgent → intent handler → reply
3. **WhatsApp Chat**: Message → whatsappWebhook → _conversationAgent → intent handler → reply
4. **Crew Lifecycle**: Job scheduled → reminders → crew GPS tracking → crew /done → feedback request
5. **Invoice Pipeline**: generateInvoice → sendInvoice → stripeWebhook → payment recorded → sendReceipt

## Environment Variables

See `registry/env-vars.json` for complete list per repo.

Critical shared secrets:
- `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` — all repos
- `OPENAI_API_KEY` — acs-website + contentco-op-website
- `ANTHROPIC_API_KEY` — coscript

Never commit API keys. acs-website has pre-commit hook blocking common key patterns.
