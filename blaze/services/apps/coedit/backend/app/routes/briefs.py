"""Client brief form submissions."""
import uuid
import time
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
from typing import Optional, List

from app.database import get_db
from app.config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM, SMTP_ENABLED

router = APIRouter(prefix="/api/briefs", tags=["briefs"])


class BriefSubmission(BaseModel):
    # Step 1: Project Type
    project_type: str
    project_type_other: Optional[str] = None

    # Step 2: Goals
    goals: List[str] = []
    goals_detail: Optional[str] = None

    # Step 3: Audience
    audience_age: Optional[str] = None
    audience_industry: Optional[str] = None
    platforms: List[str] = []
    tone: List[str] = []

    # Step 4: Creative Direction
    mood: List[str] = []
    references: Optional[str] = None
    brand_guidelines: Optional[str] = None
    must_include: Optional[str] = None

    # Step 5: Scope
    num_deliverables: Optional[str] = None
    duration_range: Optional[str] = None
    aspect_ratios: List[str] = []
    formats: List[str] = []

    # Step 6: Production
    location: Optional[str] = None
    talent_needed: Optional[str] = None
    script_status: Optional[str] = None
    footage_status: Optional[str] = None

    # Step 7: Timeline & Budget
    deadline: Optional[str] = None
    budget_range: Optional[str] = None
    timeline_urgency: Optional[str] = None

    # Step 8: Contact
    contact_name: str
    company: Optional[str] = None
    email: str
    phone: Optional[str] = None
    preferred_contact: Optional[str] = None
    how_found: Optional[str] = None
    additional_notes: Optional[str] = None


@router.post("")
async def submit_brief(brief: BriefSubmission):
    brief_id = str(uuid.uuid4())
    now = time.time()
    data_json = brief.model_dump_json()

    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO client_briefs (id, contact_name, email, company, project_type, data, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (brief_id, brief.contact_name, brief.email, brief.company, brief.project_type, data_json, now),
        )
        await db.commit()
    finally:
        await db.close()

    # Send email notification
    if SMTP_ENABLED:
        try:
            _send_brief_notification(brief, brief_id)
        except Exception:
            pass  # Don't fail the submission if email fails

    return {"id": brief_id, "status": "received"}


@router.get("")
async def list_briefs():
    """List all briefs (admin only — no auth check for now)."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, contact_name, email, company, project_type, created_at FROM client_briefs ORDER BY created_at DESC"
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


@router.get("/{brief_id}")
async def get_brief(brief_id: str):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM client_briefs WHERE id = ?", (brief_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Brief not found")
        result = dict(row)
        result["data"] = json.loads(result["data"])
        return result
    finally:
        await db.close()


def _send_brief_notification(brief: BriefSubmission, brief_id: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "New Client Brief: {} — {}".format(brief.project_type, brief.contact_name)
    msg["From"] = SMTP_FROM
    msg["To"] = "blaze@contentco-op.com"

    lines = [
        "New client brief submitted",
        "",
        "Contact: {} ({})".format(brief.contact_name, brief.email),
        "Company: {}".format(brief.company or "N/A"),
        "Phone: {}".format(brief.phone or "N/A"),
        "",
        "Project Type: {}".format(brief.project_type),
        "Goals: {}".format(", ".join(brief.goals) if brief.goals else "N/A"),
        "Platforms: {}".format(", ".join(brief.platforms) if brief.platforms else "N/A"),
        "Budget: {}".format(brief.budget_range or "N/A"),
        "Timeline: {}".format(brief.deadline or "N/A"),
        "Urgency: {}".format(brief.timeline_urgency or "Standard"),
        "",
        "Notes: {}".format(brief.additional_notes or "None"),
        "",
        "Full brief ID: {}".format(brief_id),
    ]
    text = "\n".join(lines)

    html_lines = [
        "<h2>New Client Brief</h2>",
        "<table style='border-collapse:collapse;'>",
        "<tr><td style='padding:4px 12px 4px 0;font-weight:bold;'>Contact</td><td>{} ({})</td></tr>".format(brief.contact_name, brief.email),
        "<tr><td style='padding:4px 12px 4px 0;font-weight:bold;'>Company</td><td>{}</td></tr>".format(brief.company or "N/A"),
        "<tr><td style='padding:4px 12px 4px 0;font-weight:bold;'>Project</td><td>{}</td></tr>".format(brief.project_type),
        "<tr><td style='padding:4px 12px 4px 0;font-weight:bold;'>Goals</td><td>{}</td></tr>".format(", ".join(brief.goals) if brief.goals else "N/A"),
        "<tr><td style='padding:4px 12px 4px 0;font-weight:bold;'>Budget</td><td>{}</td></tr>".format(brief.budget_range or "N/A"),
        "<tr><td style='padding:4px 12px 4px 0;font-weight:bold;'>Deadline</td><td>{}</td></tr>".format(brief.deadline or "N/A"),
        "</table>",
        "<p style='color:#888;font-size:12px;'>Brief ID: {}</p>".format(brief_id),
    ]
    html = "\n".join(html_lines)

    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)
