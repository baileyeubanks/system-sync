import time
import logging
import asyncio
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from app.database import get_db
from app.services.auth_service import generate_id
from app.config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM, SMTP_ENABLED, APP_URL

logger = logging.getLogger("coedit.notifications")


async def send_email(to: str, subject: str, body_html: str):
    """Send an email via SMTP. Non-blocking, logs errors but doesn't raise."""
    if not SMTP_ENABLED:
        logger.info("Email disabled (no SMTP_PASS). Would send to %s: %s", to, subject)
        return

    try:
        import aiosmtplib

        msg = MIMEMultipart("alternative")
        msg["From"] = SMTP_FROM
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body_html, "html"))

        await aiosmtplib.send(
            msg,
            hostname=SMTP_HOST,
            port=SMTP_PORT,
            username=SMTP_USER,
            password=SMTP_PASS,
            start_tls=True,
        )
        logger.info("Email sent to %s: %s", to, subject)
    except Exception as e:
        logger.error("Failed to send email to %s: %s", to, e)


async def create_notification(
    user_id: str,
    notif_type: str,
    message: str,
    asset_id: str = None,
    reference_id: str = None,
    send_email_to: str = None,
    email_subject: str = None,
    email_body: str = None,
):
    """Create an in-app notification and optionally send an email."""
    db = await get_db()
    try:
        notif_id = generate_id()
        now = time.time()
        email_sent = 0

        await db.execute("""
            INSERT INTO notifications (id, user_id, type, reference_id, asset_id, message, is_read, email_sent, created_at)
            VALUES (?,?,?,?,?,?,0,?,?)
        """, (notif_id, user_id, notif_type, reference_id, asset_id, message, email_sent, now))
        await db.commit()

        # Send email in background (don't block)
        if send_email_to and email_subject and email_body:
            asyncio.create_task(_send_and_mark(notif_id, send_email_to, email_subject, email_body))

        return notif_id
    finally:
        await db.close()


async def _send_and_mark(notif_id: str, to: str, subject: str, body: str):
    """Send email and mark notification as email_sent."""
    await send_email(to, subject, body)
    db = await get_db()
    try:
        await db.execute("UPDATE notifications SET email_sent = 1 WHERE id = ?", (notif_id,))
        await db.commit()
    finally:
        await db.close()


async def notify_new_comment(asset_id: str, asset_name: str, comment_author: str, comment_body: str, timecode: str = None):
    """Notify asset owner about a new comment."""
    db = await get_db()
    try:
        # Find asset owner
        row = await db.execute("""
            SELECT u.id, u.email, u.name FROM users u
            JOIN assets a ON a.created_by = u.id
            WHERE a.id = ?
        """, (asset_id,))
        owner = await row.fetchone()
        if not owner:
            return

        tc_str = " at {}".format(timecode) if timecode else ""
        message = "{} commented on {}{}".format(comment_author, asset_name, tc_str)

        email_body = """
        <div style="font-family: -apple-system, sans-serif; max-width: 500px; margin: 0 auto;">
            <h2 style="color: #333;">New comment on {asset}</h2>
            <p><strong>{author}</strong> {tc}:</p>
            <blockquote style="border-left: 3px solid #3b82f6; padding-left: 12px; color: #555;">
                {body}
            </blockquote>
            <p><a href="{url}" style="color: #3b82f6;">Open in Co-Edit</a></p>
        </div>
        """.format(
            asset=asset_name,
            author=comment_author,
            tc="at " + timecode if timecode else "",
            body=comment_body,
            url=APP_URL,
        )

        await create_notification(
            user_id=owner["id"],
            notif_type="new_comment",
            message=message,
            asset_id=asset_id,
            send_email_to=owner["email"],
            email_subject="[Co-Edit] {} commented on {}".format(comment_author, asset_name),
            email_body=email_body,
        )
    finally:
        await db.close()


async def notify_approval(asset_id: str, asset_name: str, reviewer_name: str, status: str, note: str = None):
    """Notify asset owner about an approval decision."""
    db = await get_db()
    try:
        row = await db.execute("""
            SELECT u.id, u.email, u.name FROM users u
            JOIN assets a ON a.created_by = u.id
            WHERE a.id = ?
        """, (asset_id,))
        owner = await row.fetchone()
        if not owner:
            return

        status_text = "approved" if status == "approved" else "requested changes on"
        message = "{} {} {}".format(reviewer_name, status_text, asset_name)

        note_html = ""
        if note:
            note_html = '<blockquote style="border-left: 3px solid #666; padding-left: 12px; color: #555;">{}</blockquote>'.format(note)

        status_color = "#22c55e" if status == "approved" else "#ef4444"
        email_body = """
        <div style="font-family: -apple-system, sans-serif; max-width: 500px; margin: 0 auto;">
            <h2 style="color: #333;">{reviewer} {verb} {asset}</h2>
            <p style="color: {color}; font-weight: bold; font-size: 18px;">
                {status_label}
            </p>
            {note}
            <p><a href="{url}" style="color: #3b82f6;">Open in Co-Edit</a></p>
        </div>
        """.format(
            reviewer=reviewer_name,
            verb=status_text,
            asset=asset_name,
            color=status_color,
            status_label="Approved" if status == "approved" else "Changes Requested",
            note=note_html,
            url=APP_URL,
        )

        await create_notification(
            user_id=owner["id"],
            notif_type="approval",
            message=message,
            asset_id=asset_id,
            send_email_to=owner["email"],
            email_subject="[Co-Edit] {} — {}".format(asset_name, "Approved" if status == "approved" else "Changes Requested"),
            email_body=email_body,
        )
    finally:
        await db.close()
