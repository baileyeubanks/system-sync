"""
Recruitment Scorer — Scores unscored applicants with Claude Sonnet
- Runs daily at 9am via LaunchAgent
- Scores each unscored applicant 1-10 on multiple dimensions
- Sends Telegram alerts for score >= 8
- Sends daily digest of 6-7 score applicants
"""
import json, re, time, urllib.request, urllib.parse
from datetime import datetime
import anthropic

SUPA_URL   = "https://briokwdoonawhxisbydy.supabase.co"
SUPA_KEY   = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJyaW9rd2Rvb25hd2h4aXNieWR5Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3MTU1Njc2MiwiZXhwIjoyMDg3MTMyNzYyfQ.5V1BsTrqIHGKUUHYJ3PBpL9re_WzKqOzKoQ94dc3me8"
import os as _os
from pathlib import Path as _Path

def _load_anthropic_key():
    env_file = _Path.home() / ".blaze_env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                return line.split("=", 1)[1].strip()
    return _os.environ.get("ANTHROPIC_API_KEY", "")

ANTHROPIC_KEY = _load_anthropic_key()

ANTHROPIC_KEY = _load_anthropic_key()
ACS_BID    = "0ade82e3-ffe9-4c17-ae59-fc4bd198482b"
CAIO_TG    = "telegram:7124538299"
OPENCLAW   = "PATH=/usr/local/bin:/opt/homebrew/bin:$PATH /usr/local/bin/openclaw"
IMSG_BAILEY = "+15013515927"
IMSG_CAIO   = "+15048581959"

def send_imessage(phone, message):
    """Send iMessage via imsg CLI. Sends immediately, kill process after 5s (post-send hang)."""
    import subprocess
    try:
        proc = subprocess.Popen(
            ["/opt/homebrew/bin/imsg", "send",
             "--to", phone, "--text", message, "--service", "imessage"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
        )
        try:
            out, err = proc.communicate(timeout=6)
            if proc.returncode not in (0, -9):
                print(f"  iMessage failed ({phone}): {err.decode().strip()}")
            else:
                print(f"  iMessage sent to {phone}")
        except subprocess.TimeoutExpired:
            proc.kill()
            print(f"  iMessage sent to {phone} (ack timeout — expected)")
    except Exception as e:
        print(f"  iMessage error ({phone}): {e}")

def sb(method, path, body=None, params=None):
    url = f"{SUPA_URL}/rest/v1/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body else None
    headers = {
        "apikey": SUPA_KEY, "Authorization": f"Bearer {SUPA_KEY}",
        "Content-Type": "application/json", "Prefer": "return=representation"
    }
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read()
                return json.loads(raw) if raw else []
        except urllib.error.HTTPError as e:
            if e.code == 409:
                return None
            if attempt == 2:
                raise
            time.sleep(1)
        except:
            if attempt == 2:
                raise
            time.sleep(2)

def claude_score(applicant):
    name = applicant.get("name", "Unknown")
    position = applicant.get("position", "Crew Member")
    app_text = applicant.get("application_text", "") or ""
    source = applicant.get("source", "direct")
    applied = applicant.get("applied_at", "")

    prompt = f"""You are evaluating a job application for Astro Cleaning Services, a residential cleaning company in Houston TX.

Position applied for: {position}
Source: {source}
Applied: {applied[:10] if applied else 'unknown'}
Applicant name: {name}

Application text:
{app_text[:2000]}

Score this applicant on a scale of 1-10 based on:
1. Communication quality (clarity, professionalism of writing)
2. Relevant experience (cleaning, customer service, or any home services)
3. Enthusiasm and motivation (genuine interest vs mass-applying)
4. Reliability signals (mentions availability, transportation, references)
5. Red flags (desperation, poor communication, unrealistic expectations)

Scoring guide:
9-10 = Exceptional — interview immediately
7-8 = Strong — high priority for interview
5-6 = Average — interview if no better candidates
3-4 = Below average — only if desperate for staff
1-2 = Pass — obvious red flags or very low quality

Return JSON only:
{{
  "score": 1-10,
  "recommendation": "Strong hire" | "Interview" | "Borderline" | "Pass",
  "notes": "2-3 sentences explaining the score and any standout positives/negatives"
}}"""

    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=250,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    content = response.content[0].text.strip()
    if content.startswith("```"):
        content = re.sub(r"^```\w*\n?", "", content).rstrip("`").strip()
    return json.loads(content)

def send_telegram(target, message):
    import subprocess
    cmd = f'{OPENCLAW} message send --channel telegram --account astro --target "{target}" --message "{message}"'
    subprocess.run(cmd, shell=True, capture_output=True, timeout=30)

# ── Main ───────────────────────────────────────────────────────────────────
print(f"=== Recruitment Scorer — {datetime.now().strftime('%Y-%m-%d %H:%M')} ===\n")

# Fetch unscored applicants
unscored = sb("GET", "job_applicants", params={
    "select": "id,name,email,position,application_text,source,applied_at,status",
    "business_id": f"eq.{ACS_BID}",
    "ai_score": "is.null",
    "status": "neq.rejected",
    "order": "applied_at.desc",
    "limit": "50",
})

if not unscored:
    print("No unscored applicants found.")
else:
    print(f"{len(unscored)} unscored applicant(s) to score\n")

high_priority = []   # score >= 8
mid_priority  = []   # score 6-7
scored_count  = 0
error_count   = 0

for applicant in (unscored or []):
    name = applicant.get("name", "Unknown")
    try:
        # Indeed/platform notifications have no resume content — mark as needs_review instead
        source   = applicant.get("source", "")
        app_text = applicant.get("application_text", "") or ""
        # Detect Indeed/platform stub by template phrases (no actual resume in notification email)
        stub_phrases = [
            "you will find their information below",
            "applied to the cleaning professional position posted on indeed",
            "resume may be attached (if one was provided)",
            "applied for the cleaning professional position",
        ]
        is_platform_stub = (
            source in ("indeed", "ziprecruiter", "linkedin") and
            any(p in app_text.lower() for p in stub_phrases)
        )
        if is_platform_stub:
            sb("PATCH", f"job_applicants?id=eq.{applicant['id']}", {
                "ai_score":          5,
                "ai_recommendation": "Review on platform",
                "ai_notes":          f"Application from {source} — resume and full profile available on {source.capitalize()} employer portal. Click 'View Resume' to assess.",
            })
            scored_count += 1
            print(f"  {name}: platform stub → marked 5/10 (review on {source})")
            time.sleep(0.3)
            continue

        result = claude_score(applicant)
        score  = int(result.get("score", 0))
        reco   = result.get("recommendation", "")
        notes  = result.get("notes", "")

        # Update Supabase — ai_score/ai_recommendation/ai_notes require ALTER TABLE migration
        try:
            sb("PATCH", f"job_applicants?id=eq.{applicant['id']}", {
                "ai_score":          score,
                "ai_recommendation": reco,
                "ai_notes":          notes,
            })
        except Exception as patch_err:
            if "42703" in str(patch_err):
                print("  ⚠ ai_score column missing — run ALTER TABLE migration in Supabase dashboard")
                print("    SQL: ALTER TABLE job_applicants ADD COLUMN ai_score INTEGER, ADD COLUMN ai_recommendation TEXT, ADD COLUMN ai_notes TEXT, ADD COLUMN applied_at TIMESTAMPTZ DEFAULT now();")
                break
            raise

        scored_count += 1
        print(f"  {name}: {score}/10 — {reco}")

        if score >= 8:
            high_priority.append((applicant, score, reco, notes))
        elif score >= 6:
            mid_priority.append((applicant, score, reco, notes))

        time.sleep(0.5)

    except Exception as e:
        error_count += 1
        print(f"  ✗ Error scoring {name}: {e}")
        time.sleep(1)

print(f"\nScored: {scored_count} | Errors: {error_count}")

# ── Send Telegram alerts ────────────────────────────────────────────────
if high_priority:
    for applicant, score, reco, notes in high_priority:
        name     = applicant.get("name", "Unknown")
        position = applicant.get("position", "Crew Member")
        email    = applicant.get("email", "")
        message  = (
            f"🧹 [ASTRO]\\n"
            f"🟢 HIGH-SCORE APPLICANT ({score}/10)\\n"
            f"Name: {name}\\n"
            f"Role: {position}\\n"
            f"Email: {email}\\n"
            f"AI: {reco} — {notes[:100]}\\n"
            f"Review: https://astrocleanings.com/admin/applicants"
        )
        try:
            send_telegram(CAIO_TG, message)
            print(f"  Telegram sent for {name}")
            time.sleep(1)
        except Exception as e:
            print(f"  Telegram failed: {e}")

if mid_priority:
    digest_lines = ["🧹 [ASTRO]", f"📋 APPLICANT DIGEST — {len(mid_priority)} to review (score 6-7):"]
    for applicant, score, reco, notes in mid_priority:
        digest_lines.append(f"• {applicant['name']} ({score}/10) — {applicant.get('position','')}")
    digest_lines.append(f"Review all: https://astrocleanings.com/admin/applicants")
    try:
        send_telegram(CAIO_TG, "\\n".join(digest_lines))
        print(f"  Digest sent for {len(mid_priority)} mid-priority applicants")
    except Exception as e:
        print(f"  Digest Telegram failed: {e}")
    # iMessage digest — Caio + Bailey
    imsg_digest = "\n".join(digest_lines)
    send_imessage(IMSG_CAIO, imsg_digest)
    time.sleep(1)
    send_imessage(IMSG_BAILEY, imsg_digest)

print(f"\n=== DONE ===")
print(f"High priority (8+): {len(high_priority)} | Mid (6-7): {len(mid_priority)} | Passed: {scored_count - len(high_priority) - len(mid_priority)}")
