# CoDeliver Rebuild — Complete Audit & Implementation Blueprint

**Date:** 2026-02-28
**Objective:** Rebuild CoDeliver as a Wipster-class video review platform, branded for Content Co-op, styled after CoEdit UI.

---

## PART 1: WIPSTER FEATURE AUDIT

### Core Review Features
| Feature | Wipster | Priority |
|---------|---------|----------|
| Frame-accurate timecoded comments | Yes — click anywhere on video | **P0 — MUST** |
| Pinpoint annotations (click on frame) | Yes — XY coordinates stored | **P0 — MUST** |
| Lasso/highlight annotations (drag area) | Yes — rectangle selection | **P0 — MUST** |
| Freehand drawing annotations | No — Wipster lacks this (Frame.io has it) | **P1 — SHOULD** |
| Arrow annotations | No — Wipster lacks this (Frame.io has it) | **P1 — SHOULD** |
| Variable playback speed (0.25x-4x) | Yes | **P0 — MUST** |
| Frame-by-frame navigation (arrow keys) | Yes | **P0 — MUST** |
| JKL shuttle controls | Yes | **P1 — SHOULD** |
| Side-by-side version comparison | Yes | **P0 — MUST** |
| Closed captions (.SRT, .VTT) | Yes — drag-and-drop, multi-language | **P2 — NICE** |
| Dark mode review interface | Yes | **P0 — MUST** (matches CoEdit) |
| Keyboard shortcuts (full set) | Yes — Space, arrows, 1-9 jump, C, F, M | **P0 — MUST** |

### Commenting System
| Feature | Wipster | Priority |
|---------|---------|----------|
| Timecoded comments | Yes — auto-stamped on video | **P0 — MUST** |
| Threaded replies | Yes | **P0 — MUST** |
| @Mentions | Yes | **P0 — MUST** |
| Team-only (private) comments | Yes — toggle internal/external | **P0 — MUST** |
| Comment completion checkmarks | Yes — mark as resolved | **P0 — MUST** |
| Like/react to comments | Yes — thumbs up | **P2 — NICE** |
| Image attachments on comments | Yes — attach reference images | **P1 — SHOULD** |
| Comment Focus Mode | Yes — dedicated workspace | **P1 — SHOULD** |
| Nudge reviewer | Yes — remind to give feedback | **P1 — SHOULD** |
| Comment navigation (next/prev) | Yes — arrows to cycle through | **P0 — MUST** |
| Hide/show comments toggle | Yes | **P1 — SHOULD** |

### Approval Workflows
| Feature | Wipster | Priority |
|---------|---------|----------|
| Share for Review (comments) | Yes | **P0 — MUST** |
| Share for Approval (approve/reject) | Yes | **P0 — MUST** |
| Share for Preview (view-only) | Yes | **P0 — MUST** |
| Multi-step approval chains | No — Wipster lacks this (Filestage has it) | **P1 — SHOULD** |
| Approval status tracking | Yes — who viewed/commented/approved | **P0 — MUST** |
| Task creation from comments | Yes — auto-generated to-do list | **P0 — MUST** |
| Due date reminders | No — Wipster lacks this | **P1 — SHOULD** |

### Version Control
| Feature | Wipster | Priority |
|---------|---------|----------|
| Version stacking | Yes | **P0 — MUST** |
| Version switcher (any prior version) | Yes | **P0 — MUST** |
| Side-by-side comparison | Yes | **P0 — MUST** |
| Version-specific feedback | Yes — comments tied to version | **P0 — MUST** |
| Delete older versions | Yes | **P1 — SHOULD** |

### Sharing & External Review
| Feature | Wipster | Priority |
|---------|---------|----------|
| No-login reviewer access | Yes — email link, enter name only | **P0 — MUST** |
| Password-protected links | Yes | **P0 — MUST** |
| Download toggle (on/off per share) | Yes — disabled by default | **P0 — MUST** |
| Link expiration | No — Wipster lacks this (CoEdit has it) | **P0 — MUST** |
| Batch sharing (multiple assets) | Yes | **P1 — SHOULD** |
| Share during upload | Yes — before processing done | **P2 — NICE** |
| Guest folder access | Yes — specific folder access | **P1 — SHOULD** |
| Custom branding on review pages | Yes — Team plan+ | **P0 — MUST** (CC branding) |

### Asset Management
| Feature | Wipster | Priority |
|---------|---------|----------|
| Project folders + sub-folders | Yes | **P0 — MUST** |
| Thumbnail view (grid) | Yes | **P0 — MUST** |
| List view | Yes | **P0 — MUST** |
| Bulk selection + batch actions | Yes — persists across folders | **P0 — MUST** |
| Drag-and-drop upload | Yes | **P0 — MUST** |
| Cloud import (Dropbox, GDrive, Box) | Yes | **P2 — NICE** |
| 12GB max file size | Yes | Match or exceed |
| Trash with 30-day retention | Yes | **P1 — SHOULD** |

### Media Support
| Feature | Wipster | Priority |
|---------|---------|----------|
| Video (MP4, MOV, H.264, ProRes) | Yes — server-side transcode | **P0 — MUST** |
| Images | Yes — zoom, pan, annotations | **P0 — MUST** |
| PDFs (multi-page, zoom, pan) | Yes | **P0 — MUST** |
| Audio (waveform + commenting) | Yes — first platform with this | **P1 — SHOULD** |

### Integrations
| Feature | Wipster | Priority |
|---------|---------|----------|
| Premiere Pro (comments as markers) | Yes | **P2 — Phase 3** |
| After Effects | Yes | **P2 — Phase 3** |
| Final Cut Pro | Yes | **P2 — Phase 3** |
| Slack | Yes — bidirectional | **P1 — SHOULD** |
| Publishing (YouTube, Vimeo, etc.) | Yes — one-click | **P2 — Phase 3** |
| API / Webhooks | Minimal API, no webhooks | **P1 — SHOULD** (beat them) |
| Zapier | No | **P2 — Phase 3** |

### Notifications
| Feature | Wipster | Priority |
|---------|---------|----------|
| Email (new comment, approval) | Yes — 5-min buffer | **P0 — MUST** |
| Hourly digest | Yes | **P1 — SHOULD** |
| In-app real-time | Yes | **P0 — MUST** |
| Per-asset toggle | Yes | **P1 — SHOULD** |
| Push (mobile) | Yes | **P2 — NICE** |
| Telegram notifications | No — we add this (ACS ecosystem) | **P0 — MUST** |

### Analytics
| Feature | Wipster | Priority |
|---------|---------|----------|
| View tracking (who watched) | Yes | **P1 — SHOULD** |
| Engagement metrics (playthrough) | Yes — Wipster Pulse | **P2 — Phase 3** |
| Multi-channel performance | Yes — aggregated | **P2 — Phase 3** |
| Review velocity metrics | No — Wipster lacks this | **P1 — SHOULD** (beat them) |

### Security
| Feature | Wipster | Priority |
|---------|---------|----------|
| 256-bit encryption (transit + rest) | Yes | **P0 — MUST** |
| SSO (Enterprise only) | Yes — via Auth0 | **P2 — Phase 3** |
| 2FA | Yes | **P1 — SHOULD** |
| Visible watermarking | No — Wipster lacks this | **P1 — SHOULD** (beat them) |
| Download controls | Yes | **P0 — MUST** |
| Team roles (Admin, Full, Guest) | Yes | **P0 — MUST** |

---

## PART 2: COMPETITIVE GAPS TO EXPLOIT

Features where we can **beat Wipster** by borrowing from Frame.io, Filestage, and Ziflow:

| Feature | Source | Impact |
|---------|--------|--------|
| Freehand drawing + arrows | Frame.io | Huge — Wipster only has lasso/rectangle |
| Multi-step approval chains | Filestage | Agencies need sequential routing |
| AI feedback summarization | Novel | Distill 20 comments into actionable revision notes |
| AI brand/compliance checking | Ziflow ReviewAI | Auto-flag brand guideline violations |
| Auto-transcription (Whisper) | Frame.io | Text-based video navigation |
| Visible watermarking | Frame.io | Dynamic per-reviewer watermarks |
| Webhooks + full REST API | All competitors | Wipster's API is minimal |
| Link expiration | Frame.io, CoEdit | Wipster lacks this |
| Review velocity metrics | Novel | Time-to-approval dashboards |
| Slack + Telegram integration | Novel combo | ACS ecosystem advantage |

---

## PART 3: EXISTING CODELIVER FOUNDATION

### Database (Supabase — already exists)
| Table | Columns | Status |
|-------|---------|--------|
| `projects` | id, owner_id, name, description, status, thumbnail_url, timestamps | Ready |
| `assets` | id, project_id, title, file_type, file_url, thumbnail_url, file_size, duration, status, metadata | Ready |
| `versions` | id, asset_id, version_number, file_url, file_size, notes, uploaded_by | Ready |
| `reviews` | id, asset_id, version_id, title, status, created_by | Ready |
| `comments` | id, review_id, asset_id, parent_id, author_name/email/id, body, timecode_seconds, pin_x/y, status | Ready |
| `approvals` | id, asset_id, step_order, role_label, assignee_email/id, status, decision_note | Ready |
| `review_invites` | id, asset_id, token, password_hash, reviewer_name/email, permissions, expires_at | Ready |
| `activity_log` | id, project_id, asset_id, actor_id/name, action, details | Ready |
| Storage bucket | `deliverables` (public) | Ready |
| RLS | Owner-based policies, anyone can comment | Ready |

### Frontend (Next.js 16 — already exists)
| Page | Route | Status |
|------|-------|--------|
| Dashboard | `/` | Built — stat cards, recent projects, activity feed |
| Projects list | `/projects` | Built |
| New project | `/projects/new` | Built |
| Project detail | `/projects/[id]` | Built |
| Asset library | `/library` | Built |
| Activity log | `/activity` | Built |
| Public review | `/review/[token]` | Built |
| Login/Signup | `/login`, `/signup` | Built |
| Shell sidebar | Component | Built — collapsible, nav, brand |

### What's Missing (to match Wipster)
1. **Video player with annotations** — Konva canvas overlay (CoEdit has this)
2. **Frame-accurate commenting UI** — timecode display, frame stepping
3. **Drawing tools** — freehand, arrow, rectangle, point pin
4. **Version comparison** — side-by-side player
5. **Comment Focus Mode** — dedicated workspace
6. **Approval workflow UI** — approve/reject buttons, status tracking
7. **File transcoding** — FFmpeg pipeline (CoEdit has this)
8. **Sprite sheet generation** — scrubber previews (CoEdit has this)
9. **Real-time WebSocket** — live comment updates (use Supabase Realtime)
10. **Notification system** — email + in-app + Telegram
11. **Bulk actions** — multi-select, batch move/delete/share
12. **Audio waveform player** — waveform rendering + commenting

---

## PART 4: COEDIT UI PATTERNS TO REPLICATE

### Design System (Dark Mode)
```css
/* CoDeliver should match CoEdit's aesthetic */
--bg: #0f172a;           /* Slate 900 — main background */
--surface: #1e293b;      /* Slate 800 — cards, panels */
--surface-2: #334155;    /* Slate 700 — elevated */
--surface-3: #475569;    /* Slate 600 — highest */
--ink: #f1f5f9;          /* Slate 100 — primary text */
--muted: #94a3b8;        /* Slate 400 — secondary text */
--dim: #64748b;          /* Slate 500 — tertiary */
--border: #334155;       /* Default borders */
--accent: #3b82f6;       /* Blue 500 — primary */
--accent-hover: #2563eb; /* Blue 600 — hover */
--green: #22c55e;        /* Approved */
--orange: #f59e0b;       /* In review */
--red: #ef4444;          /* Rejected */
--radius: 12px;          /* Default border radius */
--radius-sm: 8px;        /* Small */
--radius-lg: 16px;       /* Large */
```

### Key UI Patterns (from CoEdit)
- **Collapsible sidebar** (56px collapsed → 224px expanded)
- **Blue dot brand indicator** + "Co-Deliver" text
- **Nav links**: Dashboard, Projects, Library, Activity (Lucide icons)
- **Stat cards**: 4-column grid with colored accent icons
- **Rounded-xl cards** with slate-700 borders
- **Input style**: `bg-slate-800 border border-slate-700 rounded-lg px-4 py-2.5 focus:border-blue-500`
- **Button style**: `bg-blue-600 hover:bg-blue-500 text-white font-medium py-2.5 rounded-lg`
- **Skeleton loading states**
- **Empty states with CTA buttons**
- **Font**: Inter (Google Fonts)
- **Icons**: Lucide React

### CoEdit Features to Port
These already exist in CoEdit and should be replicated:

1. **Annotation Canvas** — Konva + react-konva for point pins, rectangles, freehand drawing
2. **Video player** — frame-accurate controls, sprite sheet scrubber
3. **Chunked upload** — 5MB chunks, 12GB max, resume support
4. **Transcode pipeline** — FFmpeg with HW accel, H.264 output, thumbnails, sprite sheets
5. **Share links** — modes (review/approval/preview), password, expiry, download toggle
6. **Approval workflow** — approve/changes requested, reviewer tracking
7. **WebSocket** — live comment updates, typing indicators, transcode progress
8. **White-label** — project logo, accent color, "Powered by Co-Deliver" footer
9. **Threaded comments** — with resolve toggle, private comments
10. **Email notifications** — SMTP for comment alerts, approval notifications

---

## PART 5: CONTENT CO-OP BRAND APPLICATION

### Colors (for CoDeliver)
Use the **CoEdit dark palette** (not the CC website cinematic palette):

| Element | Color | Hex |
|---------|-------|-----|
| Background | Slate 900 | `#0f172a` |
| Surfaces | Slate 800 | `#1e293b` |
| Borders | Slate 700 | `#334155` |
| Primary text | Slate 100 | `#f1f5f9` |
| Secondary text | Slate 400 | `#94a3b8` |
| Primary accent | Blue 500 | `#3b82f6` |
| Accent hover | Blue 600 | `#2563eb` |
| Approved | Green 500 | `#22c55e` |
| In Review | Amber 500 | `#f59e0b` |
| Rejected | Red 500 | `#ef4444` |

### Typography
- **Primary**: Inter 400/500/600
- **Monospace** (timecodes): `font-mono text-sm text-blue-400`
- **Display** (headers on marketing pages only): Bebas Neue

### Brand Elements
- **Product name**: Co-Deliver
- **Sidebar brand**: Blue dot + "Co-Deliver" text
- **Share page footer**: "Powered by Co-Deliver — Content Co-op"
- **Logo**: CC spiral mark (white variant) in sidebar
- **Favicon**: CC spiral mark (blue variant)

---

## PART 6: IMPLEMENTATION PHASES

### Phase 1 — Core Review Loop (MVP)
**Goal**: Upload → Review → Approve → Deliver

| Component | Tech | Based On |
|-----------|------|----------|
| Video player | HTML5 + HLS.js | New build |
| Annotation canvas | Konva + react-konva | Port from CoEdit |
| Frame-accurate comments | Supabase + Realtime | Existing DB schema |
| Drawing tools (pin, rect, freehand, arrow) | Konva | Port from CoEdit + extend |
| Version upload + history | Supabase Storage | Existing schema |
| Side-by-side comparison | Dual player sync | New build |
| Share links (review/approval/preview) | Token + password + expiry | Existing schema |
| External review (no login) | Public route | Existing route `/review/[token]` |
| Approval buttons | Approve / Changes Requested | Existing schema |
| File transcode | FFmpeg (Node or serverless) | Port from CoEdit |
| Thumbnails + sprite sheets | FFmpeg | Port from CoEdit |
| Email notifications | _notify.js or Resend | ACS ecosystem |
| Telegram notifications | Bot API | ACS ecosystem |
| Chunked upload (12GB max) | tus or custom | Port from CoEdit |

### Phase 2 — Workflow & Polish
| Component | Tech |
|-----------|------|
| Multi-step approval chains | Supabase (step_order on approvals) |
| Auto-transcription | Whisper API or Deepgram |
| Comment Focus Mode | React component |
| @Mentions | Text parsing + notification routing |
| Nudge reviewer | Email/Telegram reminder |
| Due date reminders | Cron + notification |
| Image + PDF review | Canvas annotations on stills |
| Audio waveform review | WaveSurfer.js + commenting |
| Bulk actions | Multi-select UI + batch API |
| Custom branding per project | Logo upload, accent color |
| Review velocity dashboard | Analytics queries |
| Visible watermarking | Canvas overlay (per-viewer) |
| Slack integration | Webhook notifications |
| REST API + Webhooks | Supabase functions |

### Phase 3 — Competitive Moat
| Component | Tech |
|-----------|------|
| AI feedback summarization | Claude API |
| AI brand compliance checking | Claude API + brand rules |
| Premiere Pro extension | CEP/UXP plugin |
| Final Cut Pro extension | App Store |
| Publishing (YouTube, Vimeo) | Platform APIs |
| SSO (SAML/OIDC) | Supabase Auth |
| Forensic watermarking | Invisible marks |
| Live co-review sessions | WebSocket sync playback |
| Text-based video editing | Whisper + ffmpeg |
| Zapier integration | Webhook triggers |

---

## PART 7: TECH STACK DECISION

### Recommended Stack (matches CoDeliver existing code)
| Layer | Technology | Rationale |
|-------|-----------|-----------|
| Framework | **Next.js 16** (App Router) | Already built, SSR + API routes |
| Styling | **Tailwind CSS v4** | Already using, matches CoEdit |
| State | **Zustand** | Same as CoEdit |
| Database | **Supabase (PostgreSQL)** | Already has schema + RLS |
| Storage | **Supabase Storage** | Already configured (deliverables bucket) |
| Real-time | **Supabase Realtime** | Built-in, replaces Redis pub/sub |
| Auth | **Supabase Auth** | Already configured |
| Video player | **HLS.js** + custom controls | Adaptive bitrate |
| Annotations | **Konva + react-konva** | Same as CoEdit |
| Transcoding | **FFmpeg** (serverless or Mac Mini) | Same as CoEdit |
| Notifications | **_notify.js** (ACS ecosystem) | Email + iMessage + Telegram |
| AI features | **Claude API** | Feedback summary, compliance |
| Icons | **Lucide React** | Same as CoEdit |
| Font | **Inter** (Google Fonts) | Same as CoEdit |
| Deploy | **Netlify** | Same as ACS ecosystem |

### What to Port from CoEdit (not rebuild)
1. Annotation canvas (Konva components)
2. Chunked upload with resume
3. FFmpeg transcode pipeline
4. Share link system (token generation, password, expiry)
5. Approval workflow logic
6. WebSocket notification patterns
7. Shell sidebar component
8. Design system CSS variables

---

## PART 8: DATABASE SCHEMA ADDITIONS

The existing CoDeliver schema covers most needs. Add these tables:

```sql
-- Transcription storage
CREATE TABLE transcriptions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  asset_id UUID REFERENCES assets(id) ON DELETE CASCADE,
  version_id UUID REFERENCES versions(id) ON DELETE CASCADE,
  language TEXT DEFAULT 'en',
  segments JSONB NOT NULL, -- [{start, end, text}]
  full_text TEXT,
  provider TEXT DEFAULT 'whisper',
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Notification preferences
CREATE TABLE notification_preferences (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
  channel TEXT CHECK (channel IN ('email', 'telegram', 'in_app')),
  event_type TEXT, -- 'comment', 'approval', 'version', 'mention'
  enabled BOOLEAN DEFAULT true,
  UNIQUE(user_id, channel, event_type)
);

-- Project members (team management)
CREATE TABLE project_members (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
  user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
  role TEXT CHECK (role IN ('owner', 'admin', 'editor', 'viewer', 'guest')),
  invited_at TIMESTAMPTZ DEFAULT NOW(),
  accepted_at TIMESTAMPTZ,
  UNIQUE(project_id, user_id)
);

-- Transcode jobs (track processing)
CREATE TABLE transcode_jobs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  asset_id UUID REFERENCES assets(id) ON DELETE CASCADE,
  version_id UUID REFERENCES versions(id) ON DELETE CASCADE,
  status TEXT CHECK (status IN ('queued', 'processing', 'complete', 'failed')),
  progress INTEGER DEFAULT 0,
  output_url TEXT,
  thumbnail_url TEXT,
  sprite_url TEXT,
  error TEXT,
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- View tracking (analytics)
CREATE TABLE asset_views (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  asset_id UUID REFERENCES assets(id) ON DELETE CASCADE,
  version_id UUID REFERENCES versions(id),
  viewer_name TEXT,
  viewer_email TEXT,
  viewer_id UUID REFERENCES auth.users(id),
  watch_duration_seconds FLOAT,
  max_position_seconds FLOAT,
  ip_address INET,
  user_agent TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## PART 9: FILE MANIFEST

### What Exists (keep and enhance)
```
codeliver/
  app/(dashboard)/          # Dashboard pages — enhance
  app/api/                  # API routes — enhance
  app/review/[token]/       # Public review — enhance
  components/Shell.tsx      # Sidebar — keep
  lib/                      # Auth, Supabase — keep
  supabase/migrations/      # Schema — enhance
```

### What to Build
```
codeliver/
  components/
    VideoPlayer.tsx         # HLS.js player with custom controls
    AnnotationCanvas.tsx    # Konva overlay (port from CoEdit)
    DrawingTools.tsx        # Pin, rect, freehand, arrow, line
    CommentPanel.tsx        # Threaded, timecoded, private toggle
    CommentFocus.tsx        # Focus mode workspace
    VersionCompare.tsx      # Side-by-side dual player
    ApprovalBar.tsx         # Approve / Request Changes UI
    ShareModal.tsx          # Create share link (type, password, expiry)
    UploadProgress.tsx      # Chunked upload with resume
    BulkActionBar.tsx       # Multi-select actions
    WaveformPlayer.tsx      # Audio review (WaveSurfer.js)
    TranscriptPanel.tsx     # Auto-transcription display
    NotificationBell.tsx    # In-app notification dropdown
    Watermark.tsx           # Dynamic visible watermark overlay
  app/api/
    transcode/route.ts      # FFmpeg pipeline trigger
    notifications/route.ts  # In-app notification CRUD
    transcribe/route.ts     # Whisper API integration
    webhook/route.ts        # Outbound webhook dispatch
    ai/summarize/route.ts   # Claude feedback summarization
  hooks/
    useAnnotations.ts       # Annotation state management
    useVideoPlayer.ts       # Player controls + timecode
    useComments.ts          # Real-time comment subscriptions
    useUpload.ts            # Chunked upload with progress
```

---

## SUMMARY

**Wipster's strengths**: Clean UX, unlimited reviewers, good NLE integrations, audio waveform review.

**Wipster's weaknesses**: No freehand drawing, no multi-step approvals, minimal API, no AI features, no watermarking, no link expiration.

**Our advantages**: We already have CoEdit's annotation engine, the CoDeliver DB schema, the ACS notification ecosystem (Telegram + iMessage + email), and Claude API for AI features. We can match Wipster's core in Phase 1 and surpass it in Phase 2 with multi-step approvals, AI summarization, and visible watermarking.

**Build order**: Phase 1 (core review loop) → Phase 2 (workflow + polish) → Phase 3 (competitive moat).

**Brand**: Dark mode, CoEdit design system, Inter font, blue accent, Lucide icons, "Co-Deliver" branding.
